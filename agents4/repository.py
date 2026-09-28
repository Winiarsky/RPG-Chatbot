"""Rejestr deklaracji obok tabel kroku 3, w tej SAMEJ bazie SQLite."""
from pathlib import Path
from filelock import FileLock, Timeout
from contextlib import contextmanager
from storage import RequestConflict, RevisionConflict, StorageError, signature, utc_now
from mechanics3.repository import ActionRepository, ActionError
from mechanics3.chat import public_context3

GRAPH_VERSION = 'step4_v1'


class WorkflowError(StorageError):
    pass


def public_action(record: dict | None) -> dict | None:
    if record is None:
        return None
    return {key: record[key] for key in ('action_id', 'status', 'definition', 'plan', 'result', 'public_text')}


class GraphRepository(ActionRepository):
    def initialize(self) -> None:
        super().initialize()
        with self.connection(write=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS step4_meta (
                id INTEGER PRIMARY KEY, version INTEGER NOT NULL)''')
            row = db.execute('SELECT version FROM step4_meta WHERE id=1').fetchone()
            if row and row[0] != 1:
                raise WorkflowError('Nieobsługiwana wersja tabel kroku 4.')
            db.execute('''CREATE TABLE IF NOT EXISTS step4_runs (
                campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                turn_id TEXT NOT NULL, graph_version TEXT NOT NULL,
                user_text TEXT NOT NULL, status TEXT NOT NULL
                  CHECK(status IN ('running','waiting','failed','done')),
                error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(campaign_id,turn_id))''')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS step4_one_active ON step4_runs(campaign_id) WHERE status!='done'")
            db.execute('INSERT OR IGNORE INTO step4_meta VALUES (1,1)')

    @contextmanager
    def campaign_lock(self, campaign_id: str):
        # FileLock zwalnia blokadę po zamknięciu procesu. Nie trzymamy transakcji
        # SQLite podczas LLM; ta blokada serializuje tylko sterowanie krokiem 4.
        folder = self.db_path.parent / '.rpg4_locks'
        folder.mkdir(exist_ok=True)
        key = signature('lock', {'db': str(self.db_path), 'campaign': campaign_id})
        lock = FileLock(str(folder / (key + '.lock')), timeout=0)
        try:
            lock.acquire()
        except Timeout:
            raise WorkflowError('Inne okno wykonuje tę deklarację. Odśwież po zakończeniu operacji.') from None
        try:
            yield
        finally:
            lock.release()

    def get_run(self, campaign_id: str, turn_id: str) -> dict:
        with self.connection() as db:
            row = db.execute('SELECT * FROM step4_runs WHERE campaign_id=? AND turn_id=?',
                             (campaign_id, turn_id)).fetchone()
            if row is None:
                raise WorkflowError('Nie znaleziono deklaracji.')
            if row['graph_version'] != GRAPH_VERSION:
                raise WorkflowError('Niezgodna wersja grafu zapisanego żądania; użyj zgodnej aplikacji.')
            return dict(row)

    def active_run(self, campaign_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM step4_runs WHERE campaign_id=? AND status!='done'", (campaign_id,)).fetchone()
            return dict(row) if row else None

    def recent_runs(self, campaign_id: str, limit: int = 20) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                'SELECT * FROM step4_runs WHERE campaign_id=? ORDER BY created_at DESC LIMIT ?', (campaign_id, limit))]

    def new_run(self, campaign_id: str, turn_id: str, user_text: str, max_chars: int) -> dict:
        self._action_id(turn_id)
        if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > max_chars:
            raise ValueError(f'Deklaracja musi mieć od 1 do {max_chars} znaków.')
        user_text = user_text.strip()
        with self.connection(write=True) as db:
            self._load(db, campaign_id)
            previous = db.execute('SELECT * FROM step4_runs WHERE campaign_id=? AND turn_id=?',
                                  (campaign_id, turn_id)).fetchone()
            if previous:
                if previous['user_text'] != user_text:
                    raise RequestConflict('To ID należy do innej deklaracji.')
                return dict(previous)
            if db.execute("SELECT 1 FROM step4_runs WHERE campaign_id=? AND status!='done'", (campaign_id,)).fetchone():
                raise WorkflowError('Najpierw dokończ lub anuluj poprzednią deklarację.')
            if db.execute("SELECT 1 FROM step3_actions WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone():
                raise WorkflowError('Pozostał oczekujący rzut z kroku 3. Dokończ go tam albo jawnie anuluj.')
            if self._get(db, campaign_id, turn_id):
                raise RequestConflict('To ID zostało już użyte przez wcześniejszą próbę mechaniki.')
            now = utc_now()
            db.execute('INSERT INTO step4_runs VALUES (?,?,?,?,?,?,?,?)',
                       (campaign_id, turn_id, GRAPH_VERSION, user_text, 'running', None, now, now))
        return self.get_run(campaign_id, turn_id)

    def set_run_status(self, campaign_id: str, turn_id: str, status: str, error: str | None = None):
        if status not in {'running', 'waiting', 'failed'}:
            raise ValueError('Nieprawidłowy status workflow.')
        with self.connection(write=True) as db:
            db.execute("UPDATE step4_runs SET status=?,error=?,updated_at=? WHERE campaign_id=? AND turn_id=? AND status!='done'",
                       (status, error, utc_now(), campaign_id, turn_id))

    def bundle4(self, campaign_id: str, limit: int = 12):
        """Nie dubluj syntetycznych tur silnika w nowym UI i kontekście LLM."""
        if not 1 <= limit <= 1000:
            raise ValueError('Limit historii: 1–1000.')
        with self.connection(read=True) as db:
            snapshot = self._load(db, campaign_id)
            rows = db.execute('''SELECT t.* FROM chat_turns t WHERE t.campaign_id=?
              AND NOT EXISTS (SELECT 1 FROM step4_runs r WHERE r.campaign_id=t.campaign_id
                AND t.request_id='s3:'||r.turn_id||':resolve')
              ORDER BY t.revision DESC LIMIT ?''', (campaign_id, limit)).fetchall()
            mechanics = {'progress': self._progress(db, campaign_id),
                         'pending': self._record(db.execute("SELECT * FROM step3_actions WHERE campaign_id=? AND status='pending'",
                                                           (campaign_id,)).fetchone())}
            pack = self._pack(db, campaign_id)
        context = public_context3(snapshot, mechanics)
        catalog = []
        for definition in pack.actions:
            if definition.scene_id != snapshot.state.scene_id:
                continue
            reason = None
            try:
                self._validate_action(snapshot, definition)
            except ActionError as exc:
                reason = str(exc)
            catalog.append({'definition_id': definition.id, 'label': definition.label,
                            'kind': definition.kind, 'target_id': definition.door_id or definition.scene_id,
                            'available': reason is None, 'unavailable_reason': reason})
        turns = [dict(row) for row in reversed(rows)]
        history = []
        for t in turns:
            history.extend([{'role': 'user', 'content': t['user_text']},
                            {'role': 'assistant', 'content': t['assistant_text']}])
        return context, catalog, history, turns

    def finish_run(self, campaign_id: str, turn_id: str, answer: str, *, expected_revision: int | None) -> str:
        """Atomowy zapis odpowiedzi i domknięcie deklaracji; nie modyfikuje świata."""
        if not answer.strip():
            raise ValueError('Nie zapisujemy pustej odpowiedzi.')
        request_id = f's4:{turn_id}:reply'
        with self.connection(write=True) as db:
            row = db.execute('SELECT * FROM step4_runs WHERE campaign_id=? AND turn_id=?', (campaign_id, turn_id)).fetchone()
            if row is None:
                raise WorkflowError('Brak deklaracji do zakończenia.')
            if row['status'] == 'done':
                return db.execute('SELECT assistant_text FROM chat_turns WHERE campaign_id=? AND request_id=?',
                                  (campaign_id, request_id)).fetchone()[0]
            snapshot = self._load(db, campaign_id)
            if expected_revision is not None:
                self._check_revision(snapshot, expected_revision)
            revision, now = snapshot.revision + 1, utc_now()
            db.execute('INSERT INTO chat_turns VALUES (?,?,?,?,?,?)',
                       (campaign_id, request_id, revision, row['user_text'], answer, now))
            db.execute('UPDATE campaigns SET revision=?,updated_at=? WHERE campaign_id=?', (revision, now, campaign_id))
            self._event(db, campaign_id, request_id, signature('GraphTurnSaved', {'user_text': row['user_text']}),
                        revision, 'GraphTurnSaved', {'turn_id': turn_id, 'context_revision': expected_revision})
            db.execute("UPDATE step4_runs SET status='done',error=NULL,updated_at=? WHERE campaign_id=? AND turn_id=?",
                       (now, campaign_id, turn_id))
        return answer

    def recorded_answer(self, campaign_id: str, turn_id: str) -> str | None:
        turn = self.get_turn(campaign_id, f's4:{turn_id}:reply')
        return turn['assistant_text'] if turn else None
