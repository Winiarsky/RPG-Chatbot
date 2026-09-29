"""Nowy thread prefix; zachowuje konfigurację i transport działającego ChatService."""
from contextlib import contextmanager
import sqlite3
from story.runtime import StoryController
from .nodes import RecoveryNodes


class RecoveryController(StoryController):
    def __init__(self, repo, gm, narrator, keeper, improviser, *, history_limit=12, max_chars=4000):
        self.repo, self.max_chars = repo, max_chars
        self.nodes = RecoveryNodes(repo, gm, narrator, keeper, improviser, history_limit)

    @staticmethod
    def config(campaign_id, turn_id):
        return {'configurable': {'thread_id': f'rpg6:{campaign_id}:{turn_id}'}, 'recursion_limit': 40}

    @contextmanager
    def _graph(self):
        from langgraph.checkpoint.sqlite import SqliteSaver
        from .graph import build_graph
        db = sqlite3.connect(str(self.repo.db_path), timeout=10, check_same_thread=False)
        try:
            saver = SqliteSaver(db)
            saver.setup()
            yield build_graph(self.nodes, saver)
        finally:
            db.close()

    def _view(self, campaign_id, turn_id, graph=None):
        view = super()._view(campaign_id, turn_id, graph)
        view['recovery'] = None
        if graph and view['run']['status'] != 'done':
            values = graph.get_state(self.config(campaign_id, turn_id)).values
            view['recovery'] = values.get('recovery') if values else None
        return view


def make_controller(repo, settings):
    from chat_service import ChatService
    from story.roles import StoryKeeper
    from .roles import GameMaster, Narrator, Improviser
    transport = ChatService(settings)
    return RecoveryController(repo, GameMaster(transport), Narrator(transport), StoryKeeper(transport),
        Improviser(transport), history_limit=settings.max_history_turns, max_chars=settings.max_input_chars)
