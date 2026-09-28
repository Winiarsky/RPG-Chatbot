"""Konsultacja przed skutkami; jedna zaakceptowana konsultacja na deklarację."""
import re
from chat_service import ChatError
from agents4.models import Intent
from agents4.runtime import safe_error
from improv6.nodes import RecoveryNodes
from improv6.models import RecoveryPlan
from library7a.models import LibraryAnswer
from .models import GMDecision, RecoveryDecision, RuleNeed, MAX_RULE_QUERY_CHARS
from .library import verify_answer, compact_answer, advice_packet
from .capabilities import missing, unsupported_text


class RulesNodes(RecoveryNodes):
    def __init__(self, repo, gm, narrator, keeper, improviser, consultant, history_limit=12):
        super().__init__(repo, gm, narrator, keeper, improviser, history_limit)
        self.consultant = consultant

    def load_context(self, state):
        return {**super().load_context(state), 'rules_need': None, 'rules_origin': None,
            'rules_record': None, 'rules_error': None, 'rules_attempted': False,
            'rules_block': None, 'rules_after': None, 'force_rules_only': False,
            'rules_rendered': False}

    def interpret(self, state):
        text = state['user_text'].strip()
        command = re.fullmatch(r'/(?:zasady|rules)(?:\s+(.*))?', text, flags=re.I | re.S)
        if command:
            query = (command.group(1) or '').strip()
            if len(query) < 3:
                return {'intent': Intent(kind='clarify', definition_id=None, target_id=None,
                    message='Po /zasady wpisz pytanie o regułę.').model_dump(),
                    'force_rules_only': True, 'rules_block': 'Po /zasady wpisz pytanie, np. /zasady Jak działa przewaga?'}
            if len(query) > MAX_RULE_QUERY_CHARS:
                message = f'Skróć pytanie o zasady do {MAX_RULE_QUERY_CHARS} znaków.'
                return {'intent': Intent(kind='clarify', definition_id=None, target_id=None,
                    message=message).model_dump(), 'force_rules_only': True, 'rules_block': message}
            need = RuleNeed(purpose='question', query=query, required_capabilities=[])
            return {'intent': Intent(kind='chat', definition_id=None, target_id=None,
                    message='Pytanie do bibliotekarza.').model_dump(),
                'rules_need': need.model_dump(), 'rules_origin': 'explicit', 'force_rules_only': True}
        decision = GMDecision.model_validate(self.gm.decide(text, state['history'], state['context'], state['catalog']))
        return {'intent': decision.intent.model_dump(),
            'rules_need': decision.rules.model_dump() if decision.rules else None,
            'rules_origin': 'gm' if decision.rules else None}

    def _apply_recovery(self, state, decision, context, catalog, history):
        decision = RecoveryPlan.model_validate(decision)
        interactions = context.get('improvisation', {}).get('interactions', [])
        available = {a['definition_id']: a for a in [*catalog, *interactions] if a['available']}
        if any(i not in available for i in decision.alternative_ids):
            raise ChatError('Improwizator wskazał niedostępną alternatywę. Nie wykonano skutków.')
        if state.get('recovery_reason') and decision.mode not in {'clarify', 'defer'}:
            raise ChatError('Improwizator próbował zastąpić zablokowane działanie innym. Nie wykonano skutków.')
        result = {'recovery': decision.model_dump(), 'recovery_attempted': True,
            'original_intent': state['intent'], 'context': context, 'catalog': catalog,
            'history': history, 'base_revision': context['revision'], 'flavor_brief': None}
        if decision.mode in {'map_action', 'interaction'}:
            scope = catalog if decision.mode == 'map_action' else interactions
            action = next((a for a in scope if a['definition_id'] == decision.definition_id), None)
            if action is None or not action['available'] or action['target_id'] != decision.target_id:
                raise ChatError('Improwizator wskazał nieprawidłowe działanie lub cel. Nie wykonano skutków.')
            result['catalog'] = [*catalog, *interactions]
            result['intent'] = Intent(kind='action', definition_id=decision.definition_id,
                target_id=decision.target_id, message=decision.message).model_dump()
        elif decision.mode == 'narrate':
            result['flavor_brief'] = decision.message
        return result

    def recover(self, state):
        context, catalog, history, _ = self.repo.bundle4(state['campaign_id'], self.history_limit)
        if state.get('rules_record'):
            answer = LibraryAnswer.model_validate(state['rules_record']['answer'])
            recovery = self.improviser.after_rules(state['user_text'], history, context, catalog,
                state['intent'], state.get('recovery_reason'), advice_packet(answer))
            result = self._apply_recovery(state, recovery, context, catalog, history)
            recovery = RecoveryPlan.model_validate(recovery)
            if recovery.mode not in {'map_action', 'interaction'}:
                result['rules_block'] = recovery.message
            return result
        decision = RecoveryDecision.model_validate(self.improviser.decide(state['user_text'], history,
            context, catalog, state['intent'], state.get('recovery_reason')))
        # Plan przy konsultacji nie jest jeszcze wykonywany ani traktowany jako zatwierdzony.
        if decision.rules:
            if state.get('rules_attempted'):
                return {'rules_block': 'Limit automatycznej konsultacji wykorzystany. Doprecyzuj zamiar w nowej deklaracji.'}
            return {'recovery': decision.recovery.model_dump(), 'recovery_attempted': True,
                'original_intent': state['intent'], 'context': context, 'catalog': catalog,
                'history': history, 'base_revision': context['revision'],
                'rules_need': decision.rules.model_dump(), 'rules_origin': 'improviser'}
        return self._apply_recovery(state, decision.recovery, context, catalog, history)

    def consult_rules(self, state):
        need = RuleNeed.model_validate(state['rules_need'])
        try:
            previous = self.repo.consultation(state['campaign_id'], state['turn_id'])
            if previous:
                if previous['request'] != need.model_dump() or previous['origin'] != state['rules_origin']:
                    raise ValueError('Zapisana konsultacja dotyczy innego żądania. Nie podmieniono źródeł.')
                record = previous
            else:
                ruleset = self.repo.ruleset(state['campaign_id'])
                answer = verify_answer(self.consultant.consult(need.query, ruleset), need.query, ruleset)
                record = self.repo.save_consultation(state['campaign_id'], state['turn_id'], need, answer,
                    origin=state['rules_origin'], world_revision=state['base_revision'])
        except Exception as exc:
            return {'rules_attempted': True, 'rules_error': safe_error(exc)}
        unavailable = missing(need.required_capabilities) if need.purpose == 'action' else []
        return {'rules_attempted': True, 'rules_record': record, 'rules_error': None,
            'rules_block': unsupported_text(unavailable) if unavailable else None}

    def reconsider(self, state):
        answer = LibraryAnswer.model_validate(state['rules_record']['answer'])
        need = RuleNeed.model_validate(state['rules_need'])
        if answer.status != 'supported' or need.purpose != 'action':
            return {'rules_after': 'rules_reply', 'rules_block': 'Nie ma wystarczających podstaw do przygotowania działania.'}
        context, catalog, history, _ = self.repo.bundle4(state['campaign_id'], self.history_limit)
        if context['revision'] != state['base_revision']:
            return {'rules_after': 'rules_reply', 'rules_block':
                'Stan zmienił się podczas konsultacji. Nie przygotowano działania; zadeklaruj je ponownie.'}
        advice = advice_packet(answer)
        if state['rules_origin'] == 'improviser':
            recovery = RecoveryPlan.model_validate(self.improviser.after_rules(state['user_text'], history, context,
                catalog, state['original_intent'], state.get('recovery_reason'), advice))
            result = self._apply_recovery(state, recovery, context, catalog, history)
            if recovery.mode in {'map_action', 'interaction'}:
                return {**result, 'rules_after': 'prepare'}
            return {**result, 'rules_after': 'rules_reply', 'rules_block': recovery.message}
        intent = Intent.model_validate(self.gm.after_rules(state['user_text'], history, context, catalog, advice))
        result = {'intent': intent.model_dump(), 'context': context, 'catalog': catalog,
            'history': history, 'base_revision': context['revision']}
        if intent.kind == 'action':
            return {**result, 'rules_after': 'prepare'}
        if intent.kind == 'unsupported':
            return {**result, 'rules_after': 'recover'}
        return {**result, 'rules_after': 'rules_reply', 'rules_block': intent.message}

    def prepare(self, state):
        if state.get('force_rules_only'):
            return {'rules_block': 'Pytanie /zasady nie może wykonać działania.'}
        if state.get('rules_need'):
            need = RuleNeed.model_validate(state['rules_need'])
            record = state.get('rules_record')
            if need.purpose != 'action' or not record or record['answer']['status'] != 'supported':
                return {'rules_block': 'Nie przygotowano akcji bez zakończonej konsultacji.'}
            selected = next((a for a in state['catalog'] if a['definition_id'] ==
                state['intent']['definition_id'] and a['target_id'] == state['intent']['target_id']), None)
            absent = missing(need.required_capabilities, selected['kind'] if selected else '__none__')
            if absent:
                return {'rules_block': unsupported_text(absent)}
        return super().prepare(state)

    def stop_rules(self, state):
        return {'rules_block': 'Konsultacja została przerwana. Nie wykonano działania. '
            'Możesz zadać pytanie ponownie po sprawdzeniu biblioteki lub API.'}

    def rules_reply(self, state):
        record = state.get('rules_record')
        parts = [compact_answer(LibraryAnswer.model_validate(record['answer']))] if record else []
        if state.get('rules_block'):
            parts += ['', state['rules_block']]
        if (state.get('rules_need') or {}).get('purpose') == 'action':
            parts += ['', '**Działania nie wykonano:** bez rzutu, kosztu czasu ani nowych efektów. '
                'Odpowiedź biblioteki nie jest rozstrzygnięciem silnika.']
        else:
            parts += ['', 'To odpowiedź poza fikcją. Zapis rozmowy nie przesuwa czasu świata.']
        return {'answer': '\n'.join(parts).strip(), 'answer_revision': self.repo.load(state['campaign_id']).revision,
            'rules_rendered': True}

    def finish(self, state):
        if state.get('rules_record') and not state.get('rules_rendered'):
            answer = LibraryAnswer.model_validate(state['rules_record']['answer'])
            state = {**state, 'answer': state['answer'] + '\n\n---\n\n' + compact_answer(answer)}
        return super().finish(state)
