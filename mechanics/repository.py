"""Addytywne rozszerzenie bazy kroku 2; nie nadpisuje storage.py.

Używa transakcji i pomocniczych metod GameRepository z dostarczonego kroku 2.
Podstawowy user_version pozostaje 1; rozszerzenie ma własną wersję i tabele.
Losowanie, zapis wyniku, konsekwencje oraz komunikat czatu są jedną transakcją.
"""
import json
import re
import secrets
from collections.abc import Callable, Sequence
from pathlib import Path

from schemas import CampaignSnapshot
from scenario_loader import load_yaml
from storage import GameRepository, RequestConflict, StorageError, encode, signature, utc_now
from public_context import build_public_context
from .engine import prepare_check, resolve_check
from .models import ActionDefinition, ActionPack, CheckPlan, DiceInput

DEFAULT_PACK = Path(__file__).resolve().parents[1] / 'rules' / 'definitions' / 'tower_actions.yaml'


class ActionError(StorageError):
    pass


def roll_d20() -> int:
    return secrets.randbelow(20) + 1


class ActionRepository(GameRepository):
    def initialize(self) -> None:
        super().initialize()
        with self.connection(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS step3_meta (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)')
            row = db.execute('SELECT version FROM step3_meta WHERE id=1').fetchone()
            if row is not None and row[0] != 1:
                raise ActionError('Nieobsługiwana wersja rozszerzenia etapu 3. Nie zmieniono danych.')
            db.execute('''CREATE TABLE IF NOT EXISTS step3_packs (
                campaign_id TEXT PRIMARY KEY REFERENCES campaigns(campaign_id),
                pack_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS step3_progress (
                campaign_id TEXT PRIMARY KEY REFERENCES campaigns(campaign_id),
                elapsed_seconds INTEGER NOT NULL DEFAULT 0 CHECK(elapsed_seconds >= 0),
                noise_events INTEGER NOT NULL DEFAULT 0 CHECK(noise_events >= 0))''')
            db.execute('''CREATE TABLE IF NOT EXISTS step3_actions (
                campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                action_id TEXT NOT NULL, prepare_signature TEXT NOT NULL,
                definition_json TEXT NOT NULL, plan_json TEXT,
                state_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','resolved','cancelled','invalidated')),
                resolve_signature TEXT, result_json TEXT, public_text TEXT,
                started_revision INTEGER NOT NULL, finished_revision INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(campaign_id, action_id))''')
            db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS step3_one_pending
                       ON step3_actions(campaign_id) WHERE status='pending' ''')
            db.execute('INSERT OR IGNORE INTO step3_meta VALUES (1,1)')

    @staticmethod
    def _action_id(action_id: str) -> None:
        if not isinstance(action_id, str) or re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', action_id) is None:
            raise ValueError('ID próby: 1–64 litery, cyfry, znaki _ lub -.')

    @staticmethod
    def _record(row) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        for key in ('definition', 'plan', 'result'):
            raw = item.pop(key + '_json')
            item[key] = json.loads(raw) if raw is not None else None
        return item

    @staticmethod
    def _get(db, campaign_id: str, action_id: str):
        return db.execute('SELECT * FROM step3_actions WHERE campaign_id=? AND action_id=?',
                          (campaign_id, action_id)).fetchone()

    def get_action(self, campaign_id: str, action_id: str) -> dict:
        with self.connection() as db:
            row = self._get(db, campaign_id, action_id)
            if row is None:
                raise ActionError('Nie znaleziono próby w tej kampanii.')
            return self._record(row)

    def pending_action(self, campaign_id: str) -> dict | None:
        with self.connection() as db:
            return self._record(db.execute(
                "SELECT * FROM step3_actions WHERE campaign_id=? AND status='pending'",
                (campaign_id,)).fetchone())

    def recent_actions(self, campaign_id: str, limit: int = 20) -> list[dict]:
        with self.connection() as db:
            return [self._record(row) for row in db.execute(
                'SELECT * FROM step3_actions WHERE campaign_id=? ORDER BY started_revision DESC LIMIT ?',
                (campaign_id, limit))]

    @staticmethod
    def _progress(db, campaign_id: str) -> dict:
        row = db.execute('SELECT elapsed_seconds, noise_events FROM step3_progress WHERE campaign_id=?',
                         (campaign_id,)).fetchone()
        return dict(row) if row else {'elapsed_seconds': 0, 'noise_events': 0}

    def progress(self, campaign_id: str) -> dict:
        with self.connection() as db:
            self._load(db, campaign_id)
            return self._progress(db, campaign_id)

    @staticmethod
    def _pack(db, campaign_id: str) -> ActionPack:
        row = db.execute('SELECT pack_json FROM step3_packs WHERE campaign_id=?', (campaign_id,)).fetchone()
        if row is None:
            raise ActionError('Najpierw przypnij pakiet działań do kampanii: polecenie init.')
        return ActionPack.model_validate_json(row[0])

    def pack(self, campaign_id: str) -> ActionPack:
        with self.connection() as db:
            return self._pack(db, campaign_id)

    def ensure_pack(self, campaign_id: str, path: Path = DEFAULT_PACK) -> ActionPack:
        # Istniejąca kopia ma pierwszeństwo nawet po usunięciu/edycji lokalnego YAML.
        with self.connection() as db:
            if db.execute('SELECT 1 FROM step3_packs WHERE campaign_id=?', (campaign_id,)).fetchone():
                return self._pack(db, campaign_id)
        pack = load_yaml(Path(path), ActionPack)
        with self.connection(write=True) as db:
            if db.execute('SELECT 1 FROM step3_packs WHERE campaign_id=?', (campaign_id,)).fetchone():
                return self._pack(db, campaign_id)
            snapshot = self._load(db, campaign_id)
            if pack.scenario_id != snapshot.scenario.id or pack.ruleset_id != snapshot.state.ruleset_id:
                raise ActionError('Pakiet działań nie pasuje do scenariusza lub wersji zasad.')
            scenes = {scene.id: scene for scene in snapshot.scenario.scenes}
            for action in pack.actions:
                if action.scene_id not in scenes:
                    raise ActionError('Pakiet wskazuje nieistniejącą scenę.')
                if action.door_id and action.door_id not in {d.id for d in scenes[action.scene_id].doors}:
                    raise ActionError('Cel działania nie znajduje się we wskazanej scenie.')
            db.execute('INSERT INTO step3_packs VALUES (?,?,?)', (campaign_id, pack.model_dump_json(), utc_now()))
            db.execute('INSERT INTO step3_progress VALUES (?,0,0)', (campaign_id,))
            updated = self._advance(db, snapshot)
            self._event(db, campaign_id, 's3:pack', signature('PackInstalled', {'id': pack.id}),
                        updated.revision, 'ActionPackInstalled', {'pack_id': pack.id})
        return pack

    @staticmethod
    def _fingerprint(snapshot: CampaignSnapshot, progress: dict) -> str:
        # Zwykła rozmowa zwiększa revision, ale nie zmienia faktów istotnych dla rzutu.
        return signature('world', {'state': snapshot.state.model_dump(),
                                  'scenario': snapshot.scenario.model_dump(), 'progress': progress})

    @staticmethod
    def _advance(db, snapshot: CampaignSnapshot, *, open_door: str | None = None) -> CampaignSnapshot:
        data = snapshot.model_dump()
        if open_door is not None:
            data['state']['doors'][open_door]['is_open'] = True
        data['revision'] += 1
        updated = CampaignSnapshot.model_validate(data)
        db.execute('UPDATE campaigns SET state_json=?, revision=?, updated_at=? WHERE campaign_id=?',
                   (updated.state.model_dump_json(), updated.revision, utc_now(), snapshot.campaign_id))
        return updated

    @staticmethod
    def _validate_action(snapshot: CampaignSnapshot, definition: ActionDefinition) -> None:
        if snapshot.state.scene_id != definition.scene_id:
            raise ActionError('To działanie nie jest dostępne w bieżącej scenie.')
        character = snapshot.state.character
        if character.hp_current == 0 or character.conditions:
            raise ActionError('Prototyp obsługuje te działania tylko przy HP > 0 i bez stanów postaci.')
        if definition.door_id and snapshot.state.doors[definition.door_id].is_open:
            raise ActionError('Drzwi są już otwarte. Nie przygotowano zbędnego rzutu.')

    def prepare_action(self, campaign_id: str, definition_id: str, *, action_id: str,
                       expected_revision: int) -> dict:
        self._action_id(action_id)
        request_sig = signature('prepare', {'definition_id': definition_id})
        with self.connection(write=True) as db:
            existing = self._get(db, campaign_id, action_id)
            if existing:
                if existing['prepare_signature'] != request_sig:
                    raise RequestConflict('ID próby jest już przypisane do innego działania.')
                return self._record(existing)
            snapshot = self._load(db, campaign_id)
            self._check_revision(snapshot, expected_revision)
            if db.execute("SELECT 1 FROM step3_actions WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone():
                raise ActionError('Najpierw rozstrzygnij lub anuluj oczekujący rzut.')
            definition = next((a for a in self._pack(db, campaign_id).actions if a.id == definition_id), None)
            if definition is None:
                raise ActionError('Nieobsługiwany identyfikator działania.')
            self._validate_action(snapshot, definition)
            plan = prepare_check(snapshot.state.character, definition.check) if definition.check else None
            fingerprint = self._fingerprint(snapshot, self._progress(db, campaign_id))
            # Brak testu wykonujemy od razu; test przechodzi w trwały stan pending.
            now = utc_now()
            db.execute('''INSERT INTO step3_actions
                (campaign_id,action_id,prepare_signature,definition_json,plan_json,state_fingerprint,
                 status,started_revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (campaign_id, action_id, request_sig, definition.model_dump_json(),
                 plan.model_dump_json() if plan else None, fingerprint, 'pending',
                 snapshot.revision + 1, now, now))
            if plan is None:
                self._finish(db, snapshot, action_id, definition, None, source='none', resolve_sig='no_roll')
            else:
                updated = self._advance(db, snapshot)
                self._event(db, campaign_id, f's3:{action_id}:prepare', request_sig,
                            updated.revision, 'RollRequested',
                            {'action_id': action_id, 'definition_id': definition.id, 'plan': plan.model_dump()})
            return self._record(self._get(db, campaign_id, action_id))

    def resolve_action(self, campaign_id: str, action_id: str, *, source: str,
                       dice: Sequence[int] | None = None,
                       roller: Callable[[], int] = roll_d20) -> dict:
        self._action_id(action_id)
        if source not in {'manual', 'app'}:
            raise ValueError('Źródło rzutu musi być manual lub app.')
        if source == 'app' and dice is not None:
            raise ValueError('Rzut aplikacji nie przyjmuje wyników podanych przez klienta.')
        if source == 'manual' and dice is None:
            raise ValueError('Podaj surowe wyniki kości, bez modyfikatorów.')
        provided = DiceInput(values=tuple(dice)).values if dice is not None else None
        request_sig = signature('resolve', {'source': source, 'dice': provided})
        with self.connection(write=True) as db:
            row = self._get(db, campaign_id, action_id)
            if row is None:
                raise ActionError('Nie znaleziono próby w tej kampanii.')
            if row['status'] == 'resolved':
                if row['resolve_signature'] != request_sig:
                    raise RequestConflict('Ta próba ma już zapisany wynik. Nie można zmienić kości ani źródła.')
                return self._record(row)
            if row['status'] != 'pending':
                raise ActionError('Ta próba została anulowana lub unieważniona. Utwórz nową.')
            snapshot = self._load(db, campaign_id)
            if row['state_fingerprint'] != self._fingerprint(snapshot, self._progress(db, campaign_id)):
                # Commit unieważnienia; nie podnosimy wyjątku cofającego transakcję.
                self._close_pending(db, snapshot, action_id, 'invalidated', 'RollInvalidated')
                return self._record(self._get(db, campaign_id, action_id))
            definition = ActionDefinition.model_validate_json(row['definition_json'])
            self._validate_action(snapshot, definition)
            plan = CheckPlan.model_validate_json(row['plan_json'])
            actual_dice = provided if source == 'manual' else tuple(roller() for _ in range(plan.dice_count))
            result = resolve_check(plan, actual_dice)
            self._finish(db, snapshot, action_id, definition, result.model_dump(),
                         source=source, resolve_sig=request_sig)
            return self._record(self._get(db, campaign_id, action_id))

    def _finish(self, db, snapshot, action_id, definition, check, *, source, resolve_sig) -> None:
        campaign_id = snapshot.campaign_id
        opened = definition.door_id if check and check['success'] else None
        updated = self._advance(db, snapshot, open_door=opened)
        db.execute('''UPDATE step3_progress SET elapsed_seconds=elapsed_seconds+?, noise_events=noise_events+?
                      WHERE campaign_id=?''', (definition.elapsed_seconds, definition.noise_events, campaign_id))
        if check is None:
            scene = build_public_context(updated)['scene']
            text = scene['description'] + '\n\n' + '\n'.join(d['description'] for d in scene['doors'])
            heading = '**Rozstrzygnięcie silnika — bez rzutu.**'
        else:
            dice_text = ', '.join(str(v) for v in check['dice'])
            heading = ('**Rozstrzygnięcie silnika — ' + ('sukces' if check['success'] else 'porażka') + '.**\n\n'
                       f"Kości: [{dice_text}]; użyto {check['selected']}. "
                       f"{check['selected']} {check['bonus']:+d} = {check['total']}; DC {check['dc']}.")
            text = ('Drzwi ustępują i otwierają się.' if check['success']
                    else 'Drzwi nie ustępują i pozostają zamknięte.')
        costs = f'Czas tej czynności: {definition.elapsed_seconds} s. Zdarzenia hałasu: +{definition.noise_events}.'
        public_text = heading + '\n\n' + text + '\n\n' + costs
        outcome = {'check': check, 'source': source, 'opened_door': opened,
                   'elapsed_seconds': definition.elapsed_seconds, 'noise_events': definition.noise_events}
        now = utc_now()
        db.execute('''UPDATE step3_actions SET status='resolved',resolve_signature=?,result_json=?,
                      public_text=?,finished_revision=?,updated_at=? WHERE campaign_id=? AND action_id=?''',
                   (resolve_sig, encode(outcome), public_text, updated.revision, now, campaign_id, action_id))
        request_id = f's3:{action_id}:resolve'
        # Komunikat mechaniczny nie wymaga LLM; trafia też do historii przyszłych rozmów.
        db.execute('INSERT INTO chat_turns VALUES (?,?,?,?,?,?)',
                   (campaign_id, request_id, updated.revision, definition.label, public_text, now))
        self._event(db, campaign_id, request_id, resolve_sig, updated.revision,
                    'AbilityCheckResolved' if check else 'ActionResolvedWithoutRoll',
                    {'action_id': action_id, 'definition_id': definition.id, **outcome})

    def _close_pending(self, db, snapshot, action_id: str, status: str, event: str) -> None:
        updated = self._advance(db, snapshot)
        db.execute('''UPDATE step3_actions SET status=?,finished_revision=?,updated_at=?
                      WHERE campaign_id=? AND action_id=?''',
                   (status, updated.revision, utc_now(), snapshot.campaign_id, action_id))
        self._event(db, snapshot.campaign_id, f's3:{action_id}:{status}',
                    signature(event, {'action_id': action_id}), updated.revision,
                    event, {'action_id': action_id})

    def cancel_action(self, campaign_id: str, action_id: str) -> dict:
        with self.connection(write=True) as db:
            row = self._get(db, campaign_id, action_id)
            if row is None:
                raise ActionError('Nie znaleziono próby.')
            if row['status'] == 'cancelled':
                return self._record(row)
            if row['status'] != 'pending':
                raise ActionError('Można anulować tylko nierozstrzygniętą próbę.')
            self._close_pending(db, self._load(db, campaign_id), action_id, 'cancelled', 'RollCancelled')
            return self._record(self._get(db, campaign_id, action_id))

    def context_bundle(self, campaign_id: str, limit_turns: int = 100):
        """Spójny odczyt świata, ostatnich tur i rozszerzenia mechaniki."""
        if limit_turns < 1:
            raise ValueError('Limit tur musi być dodatni.')
        with self.connection(read=True) as db:
            snapshot = self._load(db, campaign_id)
            rows = db.execute('SELECT * FROM chat_turns WHERE campaign_id=? ORDER BY revision DESC LIMIT ?',
                              (campaign_id, limit_turns)).fetchall()
            turns = [dict(row) for row in reversed(rows)]
            pending = self._record(db.execute(
                "SELECT * FROM step3_actions WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone())
            mechanics = {'progress': self._progress(db, campaign_id), 'pending': pending}
            return snapshot, turns, mechanics
