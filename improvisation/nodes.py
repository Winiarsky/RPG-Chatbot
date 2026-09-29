"""Jeden krok odzyskiwania na deklarację; żadnej pętli prób obejścia walidatora."""
from agents.models import Intent
from agents.nodes import Nodes
from chat_service import ChatError
from story.nodes import StoryNodes
from .models import RecoveryPlan


class RecoveryNodes(StoryNodes):
    def __init__(self, repo, gm, narrator, keeper, improviser, history_limit=12):
        super().__init__(repo, gm, narrator, keeper, history_limit)
        self.improviser = improviser

    def load_context(self, state):
        return {**super().load_context(state), 'recovery_attempted': False,
                'recovery_reason': None, 'flavor_brief': None}

    def recover(self, state):
        context, catalog, history, _ = self.repo.bundle4(state['campaign_id'], self.history_limit)
        decision = RecoveryPlan.model_validate(self.improviser.recover(
            state['user_text'], history, context, catalog, state['intent'], state.get('recovery_reason')))
        interactions = context.get('improvisation', {}).get('interactions', [])
        available = {a['definition_id']: a for a in [*catalog, *interactions] if a['available']}
        if any(i not in available for i in decision.alternative_ids):
            raise ChatError('Improwizator wskazał nieistniejącą lub niedostępną alternatywę. Nie wykonano skutków.')
        if state.get('recovery_reason') and decision.mode not in {'clarify', 'defer'}:
            raise ChatError('Improwizator próbował sam zmienić zablokowane działanie. Nie wykonano skutków.')
        result = {'recovery': decision.model_dump(), 'recovery_attempted': True,
            'original_intent': state['intent'], 'context': context, 'catalog': catalog,
            'history': history, 'base_revision': context['revision'], 'flavor_brief': None}
        if decision.mode in {'map_action', 'interaction'}:
            scope = catalog if decision.mode == 'map_action' else interactions
            chosen = next((a for a in scope if a['definition_id'] == decision.definition_id), None)
            if chosen is None or not chosen['available'] or chosen['target_id'] != decision.target_id:
                raise ChatError('Improwizator wskazał nieprawidłowe działanie lub cel. Nie wykonano skutków.')
            # Dalej nadal działa zwykły walidator przygotowania oraz repozytorium.
            result['catalog'] = [*catalog, *interactions]
            result['intent'] = Intent(kind='action', definition_id=decision.definition_id,
                target_id=decision.target_id, message=decision.message).model_dump()
        elif decision.mode == 'narrate':
            result['flavor_brief'] = decision.message
        return result

    def prepare(self, state):
        result = super().prepare(state)
        if not result.get('action'):
            known = any(a['definition_id'] == state['intent']['definition_id'] and
                        a['target_id'] == state['intent']['target_id'] for a in state['catalog'])
            result['recovery_reason'] = (result.get('answer', 'Nie można wykonać tego działania w aktualnym stanie.')
                                         if known else None)
        return result

    def fixed_reply(self, state):
        if state.get('action'):
            return super().fixed_reply(state)
        decision = state.get('recovery')
        if decision and decision['mode'] in {'clarify', 'defer'}:
            prefix = '**Poza fikcją:** ' if decision['mode'] == 'defer' else ''
            answer = prefix + decision['message']
            actions = [*state['catalog'], *state['context'].get('improvisation', {}).get('interactions', [])]
            labels = {a['definition_id']: a['label'] for a in actions}
            options = [labels[i] for i in decision['alternative_ids'] if i in labels]
            if options:
                answer += '\n\nMożliwe dalsze działania: ' + '; '.join(options) + '.'
            return {'answer': answer, 'answer_revision': state['base_revision']}
        if state.get('recovery_reason'):
            return {'answer': 'Sytuacja nie pozwala jeszcze wykonać tego zamiaru. ' + state['recovery_reason'],
                    'answer_revision': self.repo.load(state['campaign_id']).revision}
        return super().fixed_reply(state)

    def narrate(self, state):
        if not state.get('flavor_brief'):
            return super().narrate(state)
        context, _, history, _ = self.repo.bundle4(state['campaign_id'], self.history_limit)
        context['cosmetic_intent'] = state['flavor_brief']
        try:
            answer = self.narrator.narrate(state['user_text'], history, context, None)
            if not isinstance(answer, str) or not answer.strip():
                raise ChatError('Narrator zwrócił pusty opis.')
        except ChatError as exc:
            return {'narration_error': str(exc), 'context': context}
        return {'answer': answer.strip(), 'answer_revision': context['revision'],
                'context': context, 'narration_error': None}
