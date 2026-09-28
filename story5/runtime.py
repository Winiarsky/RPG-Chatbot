"""Reużywa trwałego kontrolera z kroku 4, z nowym grafem i walidacją potwierdzeń."""
import sqlite3
from contextlib import contextmanager
from agents4.runtime import GraphController, validate_resume
from agents4.repository import WorkflowError
from .models import StoryDecision
from .nodes import StoryNodes


def validate_story_resume(payload, value):
    if payload.get('kind') == 'story_confirm':
        return StoryDecision.model_validate(value).model_dump()
    return validate_resume(payload, value)


class StoryController(GraphController):
    def __init__(self, repo, gm, narrator, keeper, *, history_limit=12, max_chars=4000):
        self.repo, self.max_chars = repo, max_chars
        self.nodes = StoryNodes(repo, gm, narrator, keeper, history_limit)

    @staticmethod
    def config(campaign_id, turn_id):
        return {'configurable': {'thread_id': f'rpg5:{campaign_id}:{turn_id}'}, 'recursion_limit': 40}

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

    def resume(self, campaign_id, turn_id, interrupt_id, value):
        from langgraph.types import Command
        with self.repo.campaign_lock(campaign_id):
            with self._graph() as graph:
                view = self._view(campaign_id, turn_id, graph)
                if view['run']['status'] == 'done':
                    return view
                current = next((i for i in view['interrupts'] if i['id']==interrupt_id), None)
                if current is None:
                    raise WorkflowError('Ta decyzja jest nieaktualna. Odśwież panel; nie wykonano nowej operacji.')
                validated = validate_story_resume(current['payload'], value)
                return self._invoke(graph, campaign_id, turn_id, Command(resume=validated))


def make_controller(repo, settings):
    from chat_service import ChatService
    from .roles import GameMaster, StoryKeeper, Narrator
    transport = ChatService(settings)  # zachowuje twoje Responses API i diagnostykę
    return StoryController(repo, GameMaster(transport), Narrator(transport), StoryKeeper(transport),
        history_limit=settings.max_history_turns, max_chars=settings.max_input_chars)
