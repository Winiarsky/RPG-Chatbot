"""Rozszerzenie addytywne. Nie modyfikuje plików ani checkpointów kroku 4."""
import json
from pathlib import Path
from pydantic import ValidationError
from schemas import CampaignSnapshot, CharacterState
from scenario_loader import initial_state, load_yaml
from storage import RequestConflict, StorageError, signature, utc_now, encode
from mechanics.repository import ActionError
from mechanics.chat import public_context3
from agents.repository import GraphRepository, WorkflowError
from .models import StoryBook, StoryMemory, NPCMemory, BeatSelection

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORY = ROOT / 'scenarios/tower_story.yaml'
VERSION = 'step5_v1'
STORY_KINDS = {'move', 'talk', 'inspect'}


class StoryError(StorageError):
    pass


class StoryRepository(GraphRepository):
    def initialize(self) -> None:
        super().initialize()
        with self.connection(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS step5_meta (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)')
            version = db.execute('SELECT version FROM step5_meta WHERE id=1').fetchone()
            if version and version[0] != 1:
                raise StoryError('Nieobsługiwana wersja tabel opiekuna scenariusza.')
            db.execute('''CREATE TABLE IF NOT EXISTS step5_books (
                campaign_id TEXT PRIMARY KEY REFERENCES campaigns(campaign_id),
                book_json TEXT NOT NULL, memory_json TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS step5_actions (
                campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                action_id TEXT NOT NULL, prepare_signature TEXT NOT NULL,
                definition_json TEXT NOT NULL, plan_json TEXT,
                state_fingerprint TEXT NOT NULL, status TEXT NOT NULL
                  CHECK(status IN ('pending','resolved','cancelled','invalidated')),
                resolve_signature TEXT, result_json TEXT, public_text TEXT,
                started_revision INTEGER NOT NULL, finished_revision INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(campaign_id,action_id))''')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS step5_one_pending ON step5_actions(campaign_id) WHERE status='pending'")
            db.execute('INSERT OR IGNORE INTO step5_meta VALUES (1,1)')

    @staticmethod
    def _story(db, campaign_id):
        row = db.execute('SELECT * FROM step5_books WHERE campaign_id=?', (campaign_id,)).fetchone()
        if row is None:
            raise StoryError('Ta kampania nie ma scenariusza fabularnego. Użyj manage_graph.py do obsługi starszego zapisu.')
        try:
            book = StoryBook.model_validate_json(row['book_json'])
            memory = StoryMemory.model_validate_json(row['memory_json'])
            scenes, facts, npcs = ({s.id for s in book.scenario.scenes},
                                  {f.id for f in book.facts}, {n.id: n for n in book.npcs})
            if not set(memory.visited_scenes) <= scenes or not set(memory.known_facts) <= facts or not set(memory.npc_memory) <= npcs.keys():
                raise ValueError('Invalid references')
            for nid, m in memory.npc_memory.items():
                if not set(m.discussed_beats) <= {b.id for b in npcs[nid].beats}:
                    raise ValueError('Invalid beat')
            return book, memory
        except (ValidationError, ValueError):
            raise StoryError('Niespójny zapis scenariusza lub pamięci fabularnej. Nie wykonano operacji.') from None

    def story(self, campaign_id):
        """Prywatny odczyt dla MG/testów. Nie przekazuj całego wyniku narratorowi/UI."""
        with self.connection(read=True) as db:
            return self._story(db, campaign_id)

    def create_story(self, campaign_id: str, name: str, character_path: Path,
                     story_path: Path = DEFAULT_STORY):
        book = load_yaml(Path(story_path), StoryBook)
        character = load_yaml(Path(character_path), CharacterState)
        snap = CampaignSnapshot(campaign_id=campaign_id, name=name, revision=0,
             state=initial_state(character, book.scenario), scenario=book.scenario)
        memory = StoryMemory(visited_scenes=[snap.state.scene_id])
        with self.connection(write=True) as db:
            if db.execute('SELECT 1 FROM campaigns WHERE campaign_id=?', (campaign_id,)).fetchone():
                raise StoryError('Kampania o tym ID już istnieje. Nie nadpisano jej.')
            now = utc_now()
            db.execute('INSERT INTO campaigns VALUES (?,?,?,?,?,?,?)',
                (campaign_id, name, 0, snap.state.model_dump_json(), book.scenario.model_dump_json(), now, now))
            db.execute('INSERT INTO step3_packs VALUES (?,?,?)', (campaign_id, book.mechanics.model_dump_json(), now))
            db.execute('INSERT INTO step3_progress VALUES (?,0,0)', (campaign_id,))
            db.execute('INSERT INTO step5_books VALUES (?,?,?)',
                (campaign_id, book.model_dump_json(), memory.model_dump_json()))
            self._event(db, campaign_id, '__created__', '', 0, 'CampaignCreated',
                {'scenario_id': book.scenario.id, 'character_id': character.id, 'story_version': book.id})
        return snap

    def story_campaigns(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('''SELECT c.campaign_id,c.name,c.revision
                FROM campaigns c JOIN step5_books b ON b.campaign_id=c.campaign_id
                ORDER BY c.created_at,c.campaign_id''')]

    def ensure_pack(self, campaign_id: str, path=None):
        with self.connection(read=True) as db:
            self._story(db, campaign_id)
            return self._pack(db, campaign_id)

    def get_run(self, campaign_id: str, turn_id: str):
        with self.connection() as db:
            row = db.execute('SELECT * FROM step4_runs WHERE campaign_id=? AND turn_id=?', (campaign_id, turn_id)).fetchone()
            if not row or row['graph_version'] != VERSION:
                raise WorkflowError('Ta deklaracja nie należy do grafu kroku 5.')
            return dict(row)

    def new_run(self, campaign_id: str, turn_id: str, user_text: str, max_chars: int):
        self._action_id(turn_id)
        if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > max_chars:
            raise ValueError(f'Deklaracja musi mieć 1–{max_chars} znaków.')
        user_text = user_text.strip()
        with self.connection(write=True) as db:
            self._story(db, campaign_id)
            row = db.execute('SELECT * FROM step4_runs WHERE campaign_id=? AND turn_id=?', (campaign_id, turn_id)).fetchone()
            if row:
                if row['graph_version'] != VERSION or row['user_text'] != user_text:
                    raise RequestConflict('To ID należy do innej deklaracji lub wersji grafu.')
                return dict(row)
            if db.execute("SELECT 1 FROM step4_runs WHERE campaign_id=? AND status!='done'", (campaign_id,)).fetchone():
                raise WorkflowError('Najpierw zakończ poprzednią deklarację.')
            for table in ('step3_actions', 'step5_actions'):
                if db.execute(f"SELECT 1 FROM {table} WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone():
                    raise WorkflowError('Pozostała oczekująca operacja; zakończ ją przed nową deklaracją.')
                if db.execute(f'SELECT 1 FROM {table} WHERE campaign_id=? AND action_id=?', (campaign_id, turn_id)).fetchone():
                    raise RequestConflict('ID jest już użyte przez wcześniejszą operację.')
            now = utc_now()
            db.execute('INSERT INTO step4_runs VALUES (?,?,?,?,?,?,?,?)',
                (campaign_id, turn_id, VERSION, user_text, 'running', None, now, now))
        return self.get_run(campaign_id, turn_id)

    @staticmethod
    def _public_definition(a):
        return {'id': a.id, 'label': a.label, 'kind': a.kind, 'scene_id': a.scene_id,
                'target_id': a.target_id, 'elapsed_seconds': a.elapsed_seconds, 'noise_events': 0}

    @staticmethod
    def _legal_story(snapshot, action):
        if action.scene_id != snapshot.state.scene_id:
            raise ActionError('Cel nie znajduje się w bieżącej scenie.')
        c = snapshot.state.character
        if c.hp_current == 0 or c.conditions:
            raise ActionError('Ten prototyp wymaga HP > 0 i braku stanów postaci.')
        if action.required_open_door and not snapshot.state.doors[action.required_open_door].is_open:
            raise ActionError('Przejście blokują zamknięte drzwi.')

    @staticmethod
    def _eligible(npc, memory, progress):
        met = memory.npc_memory.get(npc.id, NPCMemory()).conversations > 0
        noise = progress['noise_events']
        return [b for b in npc.beats if set(b.requires_facts) <= set(memory.known_facts)
                and (b.requires_met is None or b.requires_met == met)
                and noise >= b.min_noise and (b.max_noise is None or noise <= b.max_noise)]

    @staticmethod
    def _fp(snapshot, memory, progress):
        return signature('story_world', {'state': snapshot.state.model_dump(),
            'scenario': snapshot.scenario.model_dump(), 'memory': memory.model_dump(), 'progress': progress})

    def bundle4(self, campaign_id, limit=12):
        """Kompatybilny port dla kontrolera: zawsze nowy obiekt z PUBLICZNYCH pól."""
        if not 1 <= limit <= 1000:
            raise ValueError('Limit historii: 1–1000.')
        with self.connection(read=True) as db:
            snapshot = self._load(db, campaign_id)
            book, memory = self._story(db, campaign_id)
            progress = self._progress(db, campaign_id)
            pending = self._record(db.execute("SELECT * FROM step3_actions WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone())
            context = public_context3(snapshot, {'progress': progress, 'pending': pending})
            # Nie serializujemy book/NPC/beat ani nie usuwamy sekretów post factum.
            context['scene']['npcs'] = [
                {'id': n.id, 'name': n.name, 'description': n.public_description,
                 'previous_conversations': memory.npc_memory.get(n.id, NPCMemory()).conversations}
                for n in book.npcs if n.scene_id == snapshot.state.scene_id]
            context['story'] = {
                'visited_scenes': [{'id': s.id, 'name': s.name} for s in book.scenario.scenes if s.id in memory.visited_scenes],
                'known_facts': [f.model_dump() for f in book.facts if f.id in memory.known_facts],
                'known_npcs': [{'id': n.id, 'name': n.name, 'conversations': memory.npc_memory[n.id].conversations}
                               for n in book.npcs if n.id in memory.npc_memory],
            }
            catalog = []
            for a in self._pack(db, campaign_id).actions:
                if a.scene_id == snapshot.state.scene_id:
                    reason = None
                    try:
                        self._validate_action(snapshot, a)
                    except ActionError as exc:
                        reason = str(exc)
                    catalog.append({'definition_id': a.id, 'label': a.label, 'kind': a.kind,
                        'target_id': a.door_id or a.scene_id, 'available': reason is None, 'unavailable_reason': reason})
            for a in book.actions:
                if a.scene_id == snapshot.state.scene_id:
                    reason = None
                    try:
                        self._legal_story(snapshot, a)
                    except ActionError as exc:
                        reason = str(exc)
                    catalog.append({'definition_id': a.id, 'label': a.label, 'kind': a.kind,
                        'target_id': a.target_id, 'aliases': list(a.aliases),
                        'available': reason is None, 'unavailable_reason': reason})
            context['scene']['exits'] = [
                {'target_id': a['target_id'], 'label': a['label'], 'available': a['available'],
                 'unavailable_reason': a['unavailable_reason']} for a in catalog if a['kind'] == 'move']
            rows = db.execute('''SELECT t.* FROM chat_turns t WHERE t.campaign_id=?
                AND NOT EXISTS (SELECT 1 FROM step4_runs r WHERE r.campaign_id=t.campaign_id
                  AND t.request_id='s3:'||r.turn_id||':resolve')
                ORDER BY t.revision DESC LIMIT ?''', (campaign_id, limit)).fetchall()
        turns = [dict(r) for r in reversed(rows)]
        history = []
        for t in turns:
            history += [{'role': 'user', 'content': t['user_text']}, {'role': 'assistant', 'content': t['assistant_text']}]
        return context, catalog, history, turns

    @staticmethod
    def _get_story_action(db, campaign_id, action_id):
        return db.execute('SELECT * FROM step5_actions WHERE campaign_id=? AND action_id=?', (campaign_id, action_id)).fetchone()

    def get_action(self, campaign_id, action_id):
        with self.connection() as db:
            row = self._get_story_action(db, campaign_id, action_id)
            if row:
                return self._record(row)
        return super().get_action(campaign_id, action_id)

    def pending_action(self, campaign_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM step5_actions WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone()
            if row:
                return self._record(row)
        return super().pending_action(campaign_id)

    def prepare_action(self, campaign_id, definition_id, *, action_id, expected_revision):
        book, _ = self.story(campaign_id)
        definition = next((a for a in book.actions if a.id == definition_id), None)
        if definition is None:
            with self.connection() as db:
                if self._get_story_action(db, campaign_id, action_id) or db.execute("SELECT 1 FROM step5_actions WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone():
                    raise ActionError('Najpierw zakończ operację scenariuszową.')
            return super().prepare_action(campaign_id, definition_id, action_id=action_id, expected_revision=expected_revision)
        self._action_id(action_id)
        sig = signature('story_prepare', {'definition_id': definition_id})
        with self.connection(write=True) as db:
            row = self._get_story_action(db, campaign_id, action_id)
            if row:
                if row['prepare_signature'] != sig:
                    raise RequestConflict('ID jest przypisane do innego działania.')
                return self._record(row)
            if self._get(db, campaign_id, action_id):
                raise RequestConflict('ID należy do działania mechanicznego.')
            snapshot = self._load(db, campaign_id)
            self._check_revision(snapshot, expected_revision)
            for table in ('step3_actions', 'step5_actions'):
                if db.execute(f"SELECT 1 FROM {table} WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone():
                    raise ActionError('Najpierw zakończ oczekujące działanie.')
            self._legal_story(snapshot, definition)
            _, memory = self._story(db, campaign_id)
            fp = self._fp(snapshot, memory, self._progress(db, campaign_id))
            now = utc_now()
            db.execute('''INSERT INTO step5_actions (campaign_id,action_id,prepare_signature,definition_json,
                plan_json,state_fingerprint,status,started_revision,created_at,updated_at)
                VALUES (?,?,?,?,NULL,?,'pending',?,?,?)''',
                (campaign_id, action_id, sig, encode(self._public_definition(definition)), fp, snapshot.revision + 1, now, now))
            updated = self._advance(db, snapshot)
            self._event(db, campaign_id, f's5:{action_id}:prepare', sig, updated.revision,
                        'StoryActionRequested', {'action_id': action_id, 'definition_id': definition.id})
            return self._record(self._get_story_action(db, campaign_id, action_id))

    def keeper_packet(self, campaign_id, action_id):
        """PRYWATNY odczyt używany tylko wewnątrz węzła opiekuna; nie jest stanem grafu."""
        with self.connection(read=True) as db:
            row = self._get_story_action(db, campaign_id, action_id)
            if row is None:
                raise StoryError('Nie znaleziono operacji scenariuszowej.')
            book, memory = self._story(db, campaign_id)
            definition = next(a for a in book.actions if a.id == json.loads(row['definition_json'])['id'])
            packet = {'kind': definition.kind}
            if definition.kind == 'talk':
                npc = next(n for n in book.npcs if n.id == definition.target_id)
                allowed = self._eligible(npc, memory, self._progress(db, campaign_id))
                scene = next(s for s in book.scenario.scenes if s.id == definition.scene_id)
                packet.update({'npc_name': npc.name, 'npc_notes': npc.gm_notes,
                    'scene_notes': [s.text for s in scene.gm_only],
                    'memory': memory.npc_memory.get(npc.id, NPCMemory()).model_dump(),
                    'default_beat_id': npc.default_beat_id,
                    'allowed_beats': [{'id': b.id, 'topic': b.topic, 'reply': b.reply} for b in allowed]})
            return packet

    def _close_story(self, db, snapshot, action_id, status):
        updated = self._advance(db, snapshot)
        db.execute('UPDATE step5_actions SET status=?,finished_revision=?,updated_at=? WHERE campaign_id=? AND action_id=?',
            (status, updated.revision, utc_now(), snapshot.campaign_id, action_id))
        self._event(db, snapshot.campaign_id, f's5:{action_id}:{status}', signature('story_close', {'status': status}),
            updated.revision, 'StoryAction' + status.title(), {'action_id': action_id})
        return self._record(self._get_story_action(db, snapshot.campaign_id, action_id))

    def cancel_action(self, campaign_id, action_id):
        with self.connection(write=True) as db:
            row = self._get_story_action(db, campaign_id, action_id)
            if row:
                if row['status'] == 'cancelled':
                    return self._record(row)
                if row['status'] != 'pending':
                    raise ActionError('Można anulować tylko nierozstrzygniętą operację.')
                return self._close_story(db, self._load(db, campaign_id), action_id, 'cancelled')
        return super().cancel_action(campaign_id, action_id)

    def commit_story(self, campaign_id, action_id, selection):
        selection = BeatSelection.model_validate(selection)
        sig = signature('story_commit', selection.model_dump())
        with self.connection(write=True) as db:
            row = self._get_story_action(db, campaign_id, action_id)
            if not row:
                raise StoryError('Nie znaleziono operacji.')
            if row['status'] == 'resolved':
                if row['resolve_signature'] != sig:
                    raise RequestConflict('Nie można zmienić odpowiedzi już wykonanej operacji.')
                return self._record(row)
            if row['status'] != 'pending':
                return self._record(row)
            snapshot = self._load(db, campaign_id)
            book, memory = self._story(db, campaign_id)
            progress = self._progress(db, campaign_id)
            if row['state_fingerprint'] != self._fp(snapshot, memory, progress) or db.execute(
                "SELECT 1 FROM step3_actions WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone():
                return self._close_story(db, snapshot, action_id, 'invalidated')
            definition = next(a for a in book.actions if a.id == json.loads(row['definition_json'])['id'])
            self._legal_story(snapshot, definition)
            changed = snapshot.model_dump()
            memo = memory.model_dump()
            reveals, reply, npc_name = list(definition.reveals), None, None
            if definition.kind == 'talk':
                npc = next(n for n in book.npcs if n.id == definition.target_id)
                beat = next((b for b in self._eligible(npc, memory, progress) if b.id == selection.beat_id), None)
                if beat is None:
                    raise StoryError('Opiekun wskazał niedozwoloną odpowiedź. Nie ujawniono informacji ani nie zmieniono pamięci.')
                reply, npc_name, reveals = beat.reply, npc.name, list(beat.reveals)
                m = memory.npc_memory.get(npc.id, NPCMemory()).model_dump()
                m['conversations'] += 1
                if beat.id not in m['discussed_beats']:
                    m['discussed_beats'].append(beat.id)
                memo['npc_memory'][npc.id] = m
            elif selection.beat_id is not None:
                raise StoryError('Tylko rozmowa może wskazywać odpowiedź NPC.')
            if definition.kind == 'move':
                changed['state']['scene_id'] = definition.target_id
                if definition.target_id not in memo['visited_scenes']:
                    memo['visited_scenes'].append(definition.target_id)
            new_ids = [fid for fid in reveals if fid not in memo['known_facts']]
            memo['known_facts'].extend(new_ids)
            new_memory = StoryMemory.model_validate(memo)
            changed['revision'] += 1
            updated = CampaignSnapshot.model_validate(changed)
            facts = [f.model_dump() for f in book.facts if f.id in reveals]
            result = {'kind': definition.kind, 'from_scene_id': snapshot.state.scene_id,
                'scene_id': updated.state.scene_id, 'npc_name': npc_name, 'npc_reply': reply,
                'facts': facts, 'new_fact_ids': new_ids, 'elapsed_seconds': definition.elapsed_seconds,
                'check': None}
            text = '**Zapis scenariusza — bez rzutu.**\n\n' + definition.result_text
            if reply:
                text += f'\n\n{npc_name}: {reply}'
            for fact in facts:
                text += '\n\n' + ('Odkrycie: ' if fact['id'] in new_ids else 'Znana informacja: ') + fact['text']
            text += f'\n\nCzas czynności: {definition.elapsed_seconds} s.'
            now = utc_now()
            db.execute('UPDATE campaigns SET state_json=?,revision=?,updated_at=? WHERE campaign_id=?',
                (updated.state.model_dump_json(), updated.revision, now, campaign_id))
            db.execute('UPDATE step5_books SET memory_json=? WHERE campaign_id=?', (new_memory.model_dump_json(), campaign_id))
            db.execute('UPDATE step3_progress SET elapsed_seconds=elapsed_seconds+? WHERE campaign_id=?',
                       (definition.elapsed_seconds, campaign_id))
            db.execute("UPDATE step5_actions SET status='resolved',resolve_signature=?,result_json=?,public_text=?,finished_revision=?,updated_at=? WHERE campaign_id=? AND action_id=?",
                (sig, encode(result), text, updated.revision, now, campaign_id, action_id))
            self._event(db, campaign_id, f's5:{action_id}:resolve', sig, updated.revision, 'StoryActionResolved',
                {'action_id': action_id, 'definition_id': definition.id, **result})
            return self._record(self._get_story_action(db, campaign_id, action_id))
