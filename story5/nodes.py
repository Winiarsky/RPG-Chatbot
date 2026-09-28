"""Węzeł opiekuna nie zapisuje prywatnego pakietu w stanie grafu."""
from agents4.nodes import Nodes, fallback_answer
from agents4.repository import public_action
from .models import BeatSelection
from .repository import StoryError


class StoryNodes(Nodes):
    def __init__(self, repo, gm, narrator, keeper, history_limit=12):
        super().__init__(repo, gm, narrator, history_limit)
        self.keeper = keeper

    def fixed_reply(self, state):
        if not state.get('action') and state['intent']['kind'] == 'unsupported':
            return {'answer': 'Ta czynność nie jest jeszcze obsługiwana. Niczego nie wykonano. '
                    'Możesz użyć działań z bieżącego katalogu: eksploracji, przejścia, rozmowy lub testu drzwi.',
                    'answer_revision': self.repo.load(state['campaign_id']).revision}
        return super().fixed_reply(state)

    def plan_story(self, state):
        current = self.repo.get_action(state['campaign_id'], state['turn_id'])
        if current['status'] != 'pending':
            return {'action': public_action(current)}
        packet = self.repo.keeper_packet(state['campaign_id'], state['turn_id'])
        selected = self.keeper.choose(state['user_text'], state['history'], packet)
        selected = BeatSelection.model_validate(selected)
        allowed = {b['id'] for b in packet.get('allowed_beats', [])}
        if (packet['kind'] == 'talk' and selected.beat_id not in allowed) or (packet['kind'] != 'talk' and selected.beat_id is not None):
            raise StoryError('Opiekun wskazał odpowiedź spoza dozwolonego katalogu. Nie zatwierdzono skutków.')
        # Jedynie zatwierdzone ID zostaje checkpointem; żadnego swobodnego tekstu.
        return {'keeper_selection': selected.model_dump()}

    def commit_story(self, state):
        current = self.repo.get_action(state['campaign_id'], state['turn_id'])
        if current['status'] != 'pending':
            return {'action': public_action(current)}
        result = self.repo.commit_story(state['campaign_id'], state['turn_id'], state['keeper_selection'])
        return {'action': public_action(result)}

    def cancel_story(self, state):
        current = self.repo.get_action(state['campaign_id'], state['turn_id'])
        if current['status'] == 'pending':
            current = self.repo.cancel_action(state['campaign_id'], state['turn_id'])
        return {'action': public_action(current)}
