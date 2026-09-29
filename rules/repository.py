"""Addytywna integracja i audyt. Źródła biblioteki pozostają w oddzielnej bazie."""
import json
from storage import RequestConflict, signature, utc_now
from agents.repository import WorkflowError
from improvisation.repository import ImprovisationRepository
from .models import RuleNeed
from .library import verify_answer, PROMPT_VERSION

VERSION = 'step7b_v1'


class RulesRepository(ImprovisationRepository):
    def initialize(self):
        super().initialize()
        with self.connection(write=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS step7b_bindings (
                campaign_id TEXT PRIMARY KEY REFERENCES campaigns(campaign_id),
                ruleset_id TEXT NOT NULL, enabled_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS step7b_consultations (
                campaign_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                request_json TEXT NOT NULL, answer_json TEXT NOT NULL,
                origin TEXT NOT NULL, world_revision INTEGER NOT NULL,
                prompt_version TEXT NOT NULL, answer_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(campaign_id,turn_id),
                FOREIGN KEY(campaign_id,turn_id) REFERENCES step4_runs(campaign_id,turn_id))''')

    def enable_rules(self, campaign_id, ruleset_id):
        with self.campaign_lock(campaign_id):
            with self.connection(write=True) as db:
                snapshot = self._load(db, campaign_id)
                self._profile(db, campaign_id)  # Wymagany działający krok 6.
                if snapshot.state.ruleset_id != ruleset_id:
                    raise WorkflowError('Wersja biblioteki nie zgadza się z zasadami kampanii.')
                previous = db.execute('SELECT * FROM step7b_bindings WHERE campaign_id=?', (campaign_id,)).fetchone()
                if previous:
                    if previous['ruleset_id'] != ruleset_id:
                        raise WorkflowError('Kampania jest już przypięta do innej wersji zasad.')
                    return False
                if db.execute("SELECT 1 FROM step4_runs WHERE campaign_id=? AND status!='done'", (campaign_id,)).fetchone():
                    raise WorkflowError('Dokończ lub anuluj aktywną deklarację w poprzedniej aplikacji przed włączeniem 7B.')
                for table in ('step3_actions', 'step5_actions'):
                    if db.execute(f"SELECT 1 FROM {table} WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone():
                        raise WorkflowError('Najpierw zakończ oczekującą próbę lub potwierdzenie.')
                updated = self._advance(db, snapshot)
                db.execute('INSERT INTO step7b_bindings VALUES (?,?,?)', (campaign_id, ruleset_id, utc_now()))
                self._event(db, campaign_id, '__rules7b_enabled__', signature('rules7b', {'ruleset': ruleset_id}),
                    updated.revision, 'RulesLibraryEnabled', {'ruleset_id': ruleset_id})
                return True

    def ruleset(self, campaign_id):
        with self.connection() as db:
            row = db.execute('SELECT ruleset_id FROM step7b_bindings WHERE campaign_id=?', (campaign_id,)).fetchone()
            if not row:
                raise WorkflowError('Najpierw włącz 7B: manage_rules.py enable --campaign ID.')
            return row[0]

    def enabled_campaigns(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('''SELECT c.campaign_id,c.name,c.revision,b.ruleset_id
                FROM campaigns c JOIN step7b_bindings b ON b.campaign_id=c.campaign_id
                ORDER BY c.created_at,c.campaign_id''')]

    def get_run(self, campaign_id, turn_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM step4_runs WHERE campaign_id=? AND turn_id=?', (campaign_id, turn_id)).fetchone()
            if not row or row['graph_version'] != VERSION:
                raise WorkflowError('Ta deklaracja należy do innej wersji grafu. Użyj aplikacji, która ją rozpoczęła.')
            return dict(row)

    def new_run(self, campaign_id, turn_id, user_text, max_chars):
        self._action_id(turn_id)
        self.ruleset(campaign_id)
        if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > max_chars:
            raise ValueError(f'Deklaracja musi mieć 1–{max_chars} znaków.')
        user_text = user_text.strip()
        with self.connection(write=True) as db:
            self._profile(db, campaign_id)
            row = db.execute('SELECT * FROM step4_runs WHERE campaign_id=? AND turn_id=?', (campaign_id, turn_id)).fetchone()
            if row:
                if row['graph_version'] != VERSION or row['user_text'] != user_text:
                    raise RequestConflict('ID należy do innej deklaracji albo wersji grafu.')
                return dict(row)
            if db.execute("SELECT 1 FROM step4_runs WHERE campaign_id=? AND status!='done'", (campaign_id,)).fetchone():
                raise WorkflowError('Najpierw zakończ poprzednią deklarację w odpowiedniej aplikacji.')
            for table in ('step3_actions', 'step5_actions'):
                if db.execute(f"SELECT 1 FROM {table} WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone():
                    raise WorkflowError('Najpierw zakończ oczekującą operację.')
                if db.execute(f'SELECT 1 FROM {table} WHERE campaign_id=? AND action_id=?', (campaign_id, turn_id)).fetchone():
                    raise RequestConflict('ID należy do wcześniejszej operacji.')
            now = utc_now()
            db.execute('INSERT INTO step4_runs VALUES (?,?,?,?,?,?,?,?)',
                (campaign_id, turn_id, VERSION, user_text, 'running', None, now, now))
        return self.get_run(campaign_id, turn_id)

    @staticmethod
    def _consultation(row):
        if row is None:
            return None
        data = dict(row)
        request = json.loads(data.pop('request_json'))
        answer = json.loads(data.pop('answer_json'))
        if data['answer_hash'] != signature('rules7b-answer', answer):
            raise WorkflowError('Uszkodzony zapis konsultacji. Nie użyto go do działania.')
        need = RuleNeed.model_validate(request)
        verified = verify_answer(answer, need.query, answer['ruleset_id'])
        return {**data, 'request': need.model_dump(), 'answer': verified.model_dump(mode='json')}

    def consultation(self, campaign_id, turn_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM step7b_consultations WHERE campaign_id=? AND turn_id=?',
                (campaign_id, turn_id)).fetchone()
            return self._consultation(row)

    def save_consultation(self, campaign_id, turn_id, need, answer, *, origin, world_revision):
        need = RuleNeed.model_validate(need)
        verified = verify_answer(answer, need.query, self.ruleset(campaign_id))
        if origin not in {'gm', 'improviser', 'explicit'}:
            raise ValueError('Nieznany autor zapytania.')
        with self.connection(write=True) as db:
            previous = db.execute('SELECT * FROM step7b_consultations WHERE campaign_id=? AND turn_id=?',
                (campaign_id, turn_id)).fetchone()
            if previous:
                existing = self._consultation(previous)
                if existing['request'] != need.model_dump() or existing['origin'] != origin:
                    raise RequestConflict('Ta deklaracja ma już inną konsultację. Nie podmieniono źródeł.')
                # Nie nadpisujemy historycznego snapshotu nowym wynikiem API.
                return existing
            run = db.execute('SELECT * FROM step4_runs WHERE campaign_id=? AND turn_id=?', (campaign_id, turn_id)).fetchone()
            if not run or run['graph_version'] != VERSION or run['status'] == 'done':
                raise WorkflowError('Nie można przypisać konsultacji do tej deklaracji.')
            payload = verified.model_dump(mode='json')
            db.execute('INSERT INTO step7b_consultations VALUES (?,?,?,?,?,?,?,?,?)',
                (campaign_id, turn_id, need.model_dump_json(), verified.model_dump_json(), origin,
                 world_revision, PROMPT_VERSION, signature('rules7b-answer', payload), utc_now()))
        return self.consultation(campaign_id, turn_id)

    def consultations(self, campaign_id, limit=30):
        if not 1 <= limit <= 100:
            raise ValueError('Limit historii konsultacji: 1–100.')
        with self.connection() as db:
            rows = db.execute('SELECT * FROM step7b_consultations WHERE campaign_id=? ORDER BY created_at DESC LIMIT ?',
                (campaign_id, limit)).fetchall()
            return [self._consultation(r) for r in rows]
