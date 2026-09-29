"""Addytywne operacje scenariusza. Wspólne transakcje z krokami 3–5.

Nie zmienia plików wcześniejszych kroków. Pending/result używają step5_actions;
profile i aktywny kontakt są dodatkową tabelą. Nie uruchamia kodu z LLM.
"""
import json
from pathlib import Path
from schemas import CampaignSnapshot
from storage import RequestConflict, RevisionConflict, signature, encode, utc_now
from scenario_loader import load_yaml
from mechanics.repository import ActionError
from agents.repository import WorkflowError
from story.models import StoryMemory, NPCMemory, BeatSelection
from story.repository import StoryRepository, StoryError
from .models import ImprovisationProfile

DEFAULT_PROFILE = Path(__file__).resolve().parents[1] / 'rules/definitions/tower_improvisation.yaml'
VERSION = 'step6_v1'


class ImprovisationRepository(StoryRepository):
    def initialize(self):
        super().initialize()
        with self.connection(write=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS step6_profiles (
                campaign_id TEXT PRIMARY KEY REFERENCES campaigns(campaign_id),
                profile_json TEXT NOT NULL, contact_rule_id TEXT, enabled_at TEXT NOT NULL)''')

    @staticmethod
    def validate_profile(book, profile):
        if profile.scenario_id != book.scenario.id:
            raise StoryError('Profil improwizacji nie pasuje do scenariusza.')
        scenes = {s.id: s for s in book.scenario.scenes}
        npcs = {n.id: n for n in book.npcs}
        used = {a.id for a in [*book.actions, *book.mechanics.actions]}
        for s in profile.signals:
            if s.scene_id not in scenes or s.inside_scene_id not in scenes:
                raise StoryError('Sygnał wskazuje nieznaną scenę.')
            if s.door_id not in {d.id for d in scenes[s.scene_id].doors}:
                raise StoryError('Sygnał wymaga istniejących drzwi w scenie źródłowej.')
            if s.responder_id and (s.responder_id not in npcs or npcs[s.responder_id].scene_id != s.inside_scene_id):
                raise StoryError('Odpowiadający NPC musi istnieć po wskazanej stronie drzwi.')
            for ident in [s.id, 'contact_' + s.id]:
                if ident in used:
                    raise StoryError('Kolizja identyfikatorów działań profilu.')
                used.add(ident)
            # Sprawdź długość także wygenerowanego ID kontaktu.
            ImprovisationRepository._action_id('contact_' + s.id)

    def enable(self, campaign_id, path=DEFAULT_PROFILE):
        """Jawne rozszerzenie istniejącej kampanii. Nie resetuje jej świata."""
        profile = load_yaml(Path(path), ImprovisationProfile)
        with self.campaign_lock(campaign_id):
            with self.connection(write=True) as db:
                book, _ = self._story(db, campaign_id)
                self.validate_profile(book, profile)
                existing = db.execute('SELECT profile_json FROM step6_profiles WHERE campaign_id=?', (campaign_id,)).fetchone()
                if existing:
                    if ImprovisationProfile.model_validate_json(existing[0]) != profile:
                        raise StoryError('Kampania ma już inny przypięty profil. Nie nadpisano rozstrzygnięć.')
                    return False
                if db.execute("SELECT 1 FROM step4_runs WHERE campaign_id=? AND status!='done'", (campaign_id,)).fetchone():
                    raise WorkflowError('Dokończ albo anuluj aktywną deklarację w poprzedniej aplikacji przed włączeniem kroku 6.')
                for table in ('step3_actions', 'step5_actions'):
                    if db.execute(f"SELECT 1 FROM {table} WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone():
                        raise WorkflowError('Najpierw zakończ oczekującą operację.')
                snap = self._load(db, campaign_id)
                updated = self._advance(db, snap)
                db.execute('INSERT INTO step6_profiles VALUES (?,?,NULL,?)',
                    (campaign_id, profile.model_dump_json(), utc_now()))
                self._event(db, campaign_id, '__improvisation_enabled__', signature('profile', profile.model_dump()),
                    updated.revision, 'ImprovisationEnabled', {'profile_id': profile.id})
                return True

    @staticmethod
    def _profile(db, campaign_id):
        row = db.execute('SELECT * FROM step6_profiles WHERE campaign_id=?', (campaign_id,)).fetchone()
        if row is None:
            raise StoryError('Najpierw włącz profil: python manage_improv.py enable --campaign ID.')
        return ImprovisationProfile.model_validate_json(row['profile_json']), row['contact_rule_id']

    def enabled_campaigns(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('''SELECT c.campaign_id,c.name,c.revision FROM campaigns c
                JOIN step6_profiles p ON p.campaign_id=c.campaign_id ORDER BY c.created_at,c.campaign_id''')]

    def get_run(self, campaign_id, turn_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM step4_runs WHERE campaign_id=? AND turn_id=?', (campaign_id, turn_id)).fetchone()
            if not row or row['graph_version'] != VERSION:
                raise WorkflowError('Deklaracja pochodzi z innej wersji grafu. Zakończ ją w odpowiedniej aplikacji.')
            return dict(row)

    def new_run(self, campaign_id, turn_id, user_text, max_chars):
        self._action_id(turn_id)
        if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > max_chars:
            raise ValueError(f'Deklaracja musi mieć 1–{max_chars} znaków.')
        user_text = user_text.strip()
        with self.connection(write=True) as db:
            self._profile(db, campaign_id)
            row = db.execute('SELECT * FROM step4_runs WHERE campaign_id=? AND turn_id=?', (campaign_id, turn_id)).fetchone()
            if row:
                if row['graph_version'] != VERSION or row['user_text'] != user_text:
                    raise RequestConflict('ID należy do innej deklaracji lub wersji grafu.')
                return dict(row)
            if db.execute("SELECT 1 FROM step4_runs WHERE campaign_id=? AND status!='done'", (campaign_id,)).fetchone():
                raise WorkflowError('Najpierw zakończ poprzednią deklarację.')
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
    def _contact(profile, contact_id, snap, book):
        rule = next((s for s in profile.signals if s.id == contact_id and s.scene_id == snap.state.scene_id), None)
        if not rule or not rule.can_hear or rule.response_mode == 'silent':
            return None
        npc = next((n for n in book.npcs if n.id == rule.responder_id and n.scene_id == rule.inside_scene_id), None)
        return (rule, npc) if npc else None

    @staticmethod
    def _actor_ok(snap):
        if snap.state.character.hp_current <= 0 or snap.state.character.conditions:
            raise ActionError('Ta wersja silnika wymaga przytomnej postaci bez nieobsługiwanych stanów.')

    def _custom(self, db, campaign_id, definition_id):
        snap = self._load(db, campaign_id)
        book, memory = self._story(db, campaign_id)
        profile, contact_id = self._profile(db, campaign_id)
        rule = next((s for s in profile.signals if s.id == definition_id), None)
        if rule:
            if snap.state.scene_id != rule.scene_id:
                raise ActionError('Nie ma tu wskazanego celu sygnału.')
            self._actor_ok(snap)
            return ({'id': rule.id, 'kind': 'signal', 'label': rule.label, 'scene_id': rule.scene_id,
                'target_id': rule.door_id, 'rule_id': rule.id, 'custom6': True,
                'elapsed_seconds': rule.elapsed_seconds, 'noise_events': 0}, rule, None)
        if definition_id.startswith('contact_'):
            contact = self._contact(profile, contact_id, snap, book)
            if not contact or definition_id != 'contact_' + contact[0].id:
                raise ActionError('Nie nawiązano kontaktu z tym rozmówcą z tej strony drzwi.')
            self._actor_ok(snap)
            rule, npc = contact
            return ({'id': definition_id, 'kind': 'talk', 'label': 'Porozmawiaj z ' + npc.name + ' przez próg',
                'scene_id': rule.scene_id, 'target_id': npc.id, 'rule_id': rule.id, 'custom6': True,
                'elapsed_seconds': 30, 'noise_events': 0}, rule, npc)
        return None

    def bundle4(self, campaign_id, limit=12):
        context, catalog, history, turns = super().bundle4(campaign_id, limit)
        with self.connection(read=True) as db:
            snap = self._load(db, campaign_id)
            self._check_revision(snap, context['revision'])
            book, memory = self._story(db, campaign_id)
            profile, contact_id = self._profile(db, campaign_id)
            signals = []
            for rule in profile.signals:
                if rule.scene_id != snap.state.scene_id:
                    continue
                reason = None
                try:
                    self._actor_ok(snap)
                except ActionError as exc:
                    reason = str(exc)
                # Bez responder_id, odpowiedzi, ukrytych warunków i skutków.
                signals.append({'definition_id': rule.id, 'target_id': rule.door_id, 'kind': 'signal',
                    'label': rule.label, 'hint': rule.public_hint, 'available': reason is None,
                    'unavailable_reason': reason})
            context['improvisation'] = {'interactions': signals}
            context['scene']['contacts'] = []
            contact = self._contact(profile, contact_id, snap, book)
            if contact:
                rule, npc = contact
                m = memory.npc_memory.get(npc.id, NPCMemory())
                context['scene']['contacts'].append({'id': npc.id, 'name': npc.name,
                    'channel': 'przez próg', 'previous_conversations': m.conversations})
                reason = None
                try:
                    self._actor_ok(snap)
                except ActionError as exc:
                    reason = str(exc)
                catalog.append({'definition_id': 'contact_' + rule.id, 'target_id': npc.id, 'kind': 'talk',
                    'label': 'Porozmawiaj z ' + npc.name + ' przez próg',
                    'aliases': ['Witam Martę', 'Pytam Martę o latarnika'] if npc.id == 'marta' else [],
                    'available': reason is None, 'unavailable_reason': reason})
        return context, catalog, history, turns

    def _fingerprint6(self, db, campaign_id):
        snap = self._load(db, campaign_id)
        book, memory = self._story(db, campaign_id)
        profile, contact_id = self._profile(db, campaign_id)
        return signature('world6', {'world': self._fp(snap, memory, self._progress(db, campaign_id)),
            'profile': profile.model_dump(), 'contact': contact_id})

    def prepare_action(self, campaign_id, definition_id, *, action_id, expected_revision):
        self._action_id(action_id)
        with self.connection(read=True) as db:
            row = self._get_story_action(db, campaign_id, action_id)
            if row:
                record = self._record(row)
                if record['definition'].get('custom6'):
                    if record['definition']['id'] != definition_id:
                        raise RequestConflict('ID należy do innej operacji.')
                    return record
            profile, _ = self._profile(db, campaign_id)
            special = definition_id in {s.id for s in profile.signals} or definition_id.startswith('contact_')
        if not special:
            return super().prepare_action(campaign_id, definition_id, action_id=action_id, expected_revision=expected_revision)
        with self.connection(write=True) as db:
            row = self._get_story_action(db, campaign_id, action_id)
            if row:
                record = self._record(row)
                if record['definition'].get('custom6') and record['definition']['id'] == definition_id:
                    return record
                raise RequestConflict('ID należy do innej operacji.')
            if self._get(db, campaign_id, action_id):
                raise RequestConflict('ID należy do działania mechanicznego.')
            snap = self._load(db, campaign_id)
            self._check_revision(snap, expected_revision)
            for table in ('step3_actions', 'step5_actions'):
                if db.execute(f"SELECT 1 FROM {table} WHERE campaign_id=? AND status='pending'", (campaign_id,)).fetchone():
                    raise ActionError('Najpierw zakończ oczekującą operację.')
            definition, _, _ = self._custom(db, campaign_id, definition_id)
            fp = self._fingerprint6(db, campaign_id)
            sig, now = signature('prepare6', {'definition_id': definition_id}), utc_now()
            updated = self._advance(db, snap)
            db.execute('''INSERT INTO step5_actions (campaign_id,action_id,prepare_signature,definition_json,
                plan_json,state_fingerprint,status,started_revision,created_at,updated_at)
                VALUES (?,?,?,?,NULL,?,'pending',?,?,?)''',
                (campaign_id, action_id, sig, encode(definition), fp, updated.revision, now, now))
            self._event(db, campaign_id, f's6:{action_id}:prepare', sig, updated.revision,
                'InteractionRequested', {'action_id': action_id, 'definition_id': definition_id})
            return self._record(self._get_story_action(db, campaign_id, action_id))

    def keeper_packet(self, campaign_id, action_id):
        record = self.get_action(campaign_id, action_id)
        if not record['definition'].get('custom6'):
            return super().keeper_packet(campaign_id, action_id)
        with self.connection(read=True) as db:
            # Odczyt dotyczy już przygotowanej operacji. Zmianę HP lub kontaktu
            # unieważni fingerprint w commit_story, zamiast blokować wznowienie.
            definition = record['definition']
            if definition['kind'] == 'signal':
                # Jednoznaczna, zatwierdzona polityka scenariusza: nie trzeba LLM.
                return {'kind': 'signal'}
            book, memory = self._story(db, campaign_id)
            npc = next(n for n in book.npcs if n.id == definition['target_id'])
            return {'kind': 'talk', 'npc_name': npc.name, 'npc_notes': npc.gm_notes,
                'scene_notes': [], 'memory': memory.npc_memory.get(npc.id, NPCMemory()).model_dump(),
                'default_beat_id': npc.default_beat_id,
                'allowed_beats': [{'id': b.id, 'topic': b.topic, 'reply': b.reply}
                    for b in self._eligible(npc, memory, self._progress(db, campaign_id))]}

    def commit_story(self, campaign_id, action_id, selection):
        record = self.get_action(campaign_id, action_id)
        if not record['definition'].get('custom6'):
            return super().commit_story(campaign_id, action_id, selection)
        selected = BeatSelection.model_validate(selection)
        sig = signature('commit6', selected.model_dump())
        with self.connection(write=True) as db:
            row = self._get_story_action(db, campaign_id, action_id)
            if row['status'] == 'resolved':
                if row['resolve_signature'] != sig:
                    raise RequestConflict('Nie można zmienić zatwierdzonej reakcji.')
                return self._record(row)
            if row['status'] != 'pending':
                return self._record(row)
            snap = self._load(db, campaign_id)
            if row['state_fingerprint'] != self._fingerprint6(db, campaign_id):
                return self._close_story(db, snap, action_id, 'invalidated')
            definition, rule, npc = self._custom(db, campaign_id, record['definition']['id'])
            book, memory = self._story(db, campaign_id)
            profile, contact_id = self._profile(db, campaign_id)
            memo, changed = memory.model_dump(), snap.model_dump()
            reply, npc_name, facts, new_ids, opened = None, None, [], [], False
            if definition['kind'] == 'signal':
                if selected.beat_id is not None:
                    raise StoryError('Sygnał nie może ujawniać dowolnej odpowiedzi NPC.')
                npc = next((n for n in book.npcs if n.id == rule.responder_id and n.scene_id == rule.inside_scene_id), None)
                responsive = npc is not None and rule.can_hear and rule.response_mode != 'silent'
                if responsive:
                    noise = self._progress(db, campaign_id)['noise_events']
                    can_open = rule.response_mode == 'open' and rule.can_open_from_inside and (
                        rule.max_noise_to_open is None or noise <= rule.max_noise_to_open)
                    met = memory.npc_memory.get(npc.id, NPCMemory())
                    if can_open:
                        opened = not changed['state']['doors'][rule.door_id]['is_open']
                        changed['state']['doors'][rule.door_id]['is_open'] = True
                        reply = rule.repeat_reply if met.conversations else rule.first_reply
                        lead = 'Dajesz sygnał przy wejściu. Drzwi otwierają się od środka.' if opened else 'Dajesz sygnał przy otwartych drzwiach. Ze środka pada odpowiedź.'
                    else:
                        reply, lead = rule.cautious_reply, 'Dajesz sygnał przy wejściu. Ze środka pada odpowiedź; położenie drzwi nie zmienia się.'
                    npc_name, contact_id = npc.name, rule.id
                    entry = met.model_dump()
                    entry['conversations'] += 1
                    memo['npc_memory'][npc.id] = entry
                else:
                    lead, contact_id = rule.silent_text, None
            else:
                eligible = self._eligible(npc, memory, self._progress(db, campaign_id))
                beat = next((b for b in eligible if b.id == selected.beat_id), None)
                if beat is None:
                    raise StoryError('Niedozwolona odpowiedź NPC. Nie zmieniono świata.')
                reply, npc_name = beat.reply, npc.name
                entry = memory.npc_memory.get(npc.id, NPCMemory()).model_dump()
                entry['conversations'] += 1
                if beat.id not in entry['discussed_beats']:
                    entry['discussed_beats'].append(beat.id)
                memo['npc_memory'][npc.id] = entry
                new_ids = [i for i in beat.reveals if i not in memo['known_facts']]
                memo['known_facts'].extend(new_ids)
                facts = [f.model_dump() for f in book.facts if f.id in beat.reveals]
                lead = 'Rozmawiasz przez próg. Pozostajesz po dotychczasowej stronie drzwi.'
            new_memory = StoryMemory.model_validate(memo)
            changed['revision'] += 1
            updated = CampaignSnapshot.model_validate(changed)
            result = {'kind': definition['kind'], 'scene_id': updated.state.scene_id,
                'from_scene_id': snap.state.scene_id, 'npc_name': npc_name, 'npc_reply': reply,
                'door_opened': opened, 'contact_established': reply is not None, 'facts': facts,
                'new_fact_ids': new_ids, 'elapsed_seconds': definition['elapsed_seconds'], 'check': None}
            text = '**Zapis scenariusza — bez rzutu.**\n\n' + lead
            if reply:
                text += f'\n\n{npc_name}: {reply}'
            for fact in facts:
                text += '\n\n' + fact['text']
            text += f"\n\nCzas czynności: {definition['elapsed_seconds']} s. Postać nie zmieniła sceny."
            now = utc_now()
            db.execute('UPDATE campaigns SET state_json=?,revision=?,updated_at=? WHERE campaign_id=?',
                (updated.state.model_dump_json(), updated.revision, now, campaign_id))
            db.execute('UPDATE step5_books SET memory_json=? WHERE campaign_id=?', (new_memory.model_dump_json(), campaign_id))
            db.execute('UPDATE step6_profiles SET contact_rule_id=? WHERE campaign_id=?', (contact_id, campaign_id))
            db.execute('UPDATE step3_progress SET elapsed_seconds=elapsed_seconds+? WHERE campaign_id=?',
                (definition['elapsed_seconds'], campaign_id))
            db.execute("UPDATE step5_actions SET status='resolved',resolve_signature=?,result_json=?,public_text=?,finished_revision=?,updated_at=? WHERE campaign_id=? AND action_id=?",
                (sig, encode(result), text, updated.revision, now, campaign_id, action_id))
            self._event(db, campaign_id, f's6:{action_id}:resolve', sig, updated.revision,
                'InteractionResolved', {'action_id': action_id, 'definition_id': definition['id'], **result})
            return self._record(self._get_story_action(db, campaign_id, action_id))
