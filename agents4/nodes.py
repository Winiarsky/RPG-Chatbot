"""Węzły poza interrupt: można je testować bez biblioteki LangGraph i bez API."""
from chat_service import ChatError
from storage import RevisionConflict, StorageError
from .models import Intent, RollDecision
from .repository import GraphRepository, public_action

UNSUPPORTED = ('Ta czynność nie ma jeszcze obsługi w silniku. Niczego nie wykonano. '
               'Obecnie możesz wyważyć drzwi, obejrzeć jawne otoczenie albo zapytać o stan postaci.')


def with_mechanics(answer: str, action: dict | None) -> str:
    if action and action.get('public_text'):
        return answer + '\n\n---\n\n' + action['public_text']
    return answer


def fallback_answer(action: dict | None) -> str:
    if action and action['status'] == 'resolved':
        return 'Opis narratora został pominięty. Poniżej zapisane rozstrzygnięcie silnika.'
    if action and action['status'] == 'cancelled':
        return 'Próba anulowana przed rozstrzygnięciem. Nie wykonano rzutu ani nie naliczono jej skutków.'
    if action and action['status'] == 'invalidated':
        return 'Świat zmienił się przed rzutem. Plan unieważniono bez rozstrzygnięcia; zadeklaruj działanie ponownie.'
    return 'Opis narratora został pominięty. Ta rozmowa nie wykonała działania mechanicznego.'


class Nodes:
    def __init__(self, repo: GraphRepository, gm, narrator, history_limit: int = 12):
        self.repo, self.gm, self.narrator = repo, gm, narrator
        self.history_limit = history_limit

    def load_context(self, state: dict) -> dict:
        context, catalog, history, _ = self.repo.bundle4(state['campaign_id'], self.history_limit)
        return {'context': context, 'catalog': catalog, 'history': history,
                'base_revision': context['revision'], 'action': None, 'narration_error': None}

    def interpret(self, state: dict) -> dict:
        intent = self.gm.interpret(state['user_text'], state['history'], state['context'], state['catalog'])
        # Nawet customowe adaptery agentów muszą przejść ten kontrakt.
        intent = Intent.model_validate(intent)
        return {'intent': intent.model_dump()}

    def fixed_reply(self, state: dict) -> dict:
        if state.get('action'):
            answer = fallback_answer(state['action'])
        else:
            intent = Intent.model_validate(state['intent'])
            answer = ('**MG — doprecyzowanie.** ' + intent.message if intent.kind == 'clarify' else UNSUPPORTED)
        return {'answer': answer, 'answer_revision': self.repo.load(state['campaign_id']).revision}

    def prepare(self, state: dict) -> dict:
        intent = Intent.model_validate(state['intent'])
        item = next((a for a in state['catalog'] if a['definition_id'] == intent.definition_id), None)
        if item is None or item['target_id'] != intent.target_id:
            return {'answer': 'MG wskazał działanie lub cel spoza katalogu. Nie wykonano żadnej operacji.',
                    'answer_revision': state['base_revision']}
        try:
            # Weryfikacja celu powyżej + legality/revision w sprawdzonym silniku kroku 3.
            action = self.repo.prepare_action(state['campaign_id'], intent.definition_id,
                        action_id=state['turn_id'], expected_revision=state['base_revision'])
        except StorageError as exc:
            return {'answer': 'Nie przygotowano działania: ' + str(exc),
                    'answer_revision': self.repo.load(state['campaign_id']).revision}
        return {'action': public_action(action)}

    def resolve(self, state: dict) -> dict:
        decision = RollDecision.model_validate(state['roll_decision'])
        campaign, turn = state['campaign_id'], state['turn_id']
        current = self.repo.get_action(campaign, turn)
        if current['status'] in {'invalidated', 'cancelled'}:
            return {'action': public_action(current)}
        if decision.choice == 'cancel':
            # Zewnętrznie zatwierdzonego wyniku nie cofamy.
            action = current if current['status'] == 'resolved' else self.repo.cancel_action(campaign, turn)
        else:
            action = self.repo.resolve_action(campaign, turn, source=decision.choice, dice=decision.dice)
        return {'action': public_action(action)}

    def narrate(self, state: dict) -> dict:
        context, _, history, _ = self.repo.bundle4(state['campaign_id'], self.history_limit)
        try:
            answer = self.narrator.narrate(state['user_text'], history, context, state.get('action'))
            if not isinstance(answer, str) or not answer.strip():
                raise ChatError('Narrator zwrócił pusty opis.')
        except ChatError as exc:
            return {'narration_error': str(exc), 'context': context}
        return {'answer': answer.strip(), 'answer_revision': context['revision'],
                'context': context, 'narration_error': None}

    def fallback(self, state: dict) -> dict:
        return {'answer': fallback_answer(state.get('action')), 'narration_error': None,
                'answer_revision': self.repo.load(state['campaign_id']).revision}

    def finish(self, state: dict) -> dict:
        answer = with_mechanics(state['answer'], state.get('action'))
        try:
            final = self.repo.finish_run(state['campaign_id'], state['turn_id'], answer,
                                        expected_revision=state['answer_revision'])
        except RevisionConflict:
            # Nie utrwalamy opisu opartego na nieaktualnym stanie i nie zapętlamy API.
            stable = ('Stan zmienił się podczas przygotowywania odpowiedzi; opis nie został zatwierdzony. '
                      'Sprawdź bieżący panel. Ewentualne rozstrzygnięcie poniżej jest zapisem wcześniejszej próby.')
            final = self.repo.finish_run(state['campaign_id'], state['turn_id'],
                      with_mechanics(stable, state.get('action')), expected_revision=None)
        return {'answer': final, 'finished': True}
