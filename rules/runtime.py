"""Nowy prefiks checkpointu, bez zmian działającego transportu modeli."""
import sqlite3
from contextlib import contextmanager
from agents.repository import WorkflowError
from story.runtime import validate_story_resume
from improvisation.runtime import RecoveryController
from .nodes import RulesNodes
from .models import RulesRetry


def validate_rules_resume(payload, value):
    if payload.get('kind') == 'rules_retry':
        return RulesRetry.model_validate(value).model_dump()
    return validate_story_resume(payload, value)


class RulesController(RecoveryController):
    def __init__(self, repo, gm, narrator, keeper, improviser, consultant, *, history_limit=12, max_chars=4000):
        self.repo, self.max_chars = repo, max_chars
        self.nodes = RulesNodes(repo, gm, narrator, keeper, improviser, consultant, history_limit)

    @staticmethod
    def config(campaign_id, turn_id):
        return {'configurable': {'thread_id': f'rpg7b:{campaign_id}:{turn_id}'}, 'recursion_limit': 50}

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
        view['rules'] = self.repo.consultation(campaign_id, turn_id)
        return view

    def resume(self, campaign_id, turn_id, interrupt_id, value):
        from langgraph.types import Command
        with self.repo.campaign_lock(campaign_id):
            with self._graph() as graph:
                view = self._view(campaign_id, turn_id, graph)
                if view['run']['status'] == 'done':
                    return view
                current = next((i for i in view['interrupts'] if i['id'] == interrupt_id), None)
                if current is None:
                    raise WorkflowError('Nieaktualna decyzja. Odśwież panel; niczego nie wykonano ponownie.')
                validated = validate_rules_resume(current['payload'], value)
                return self._invoke(graph, campaign_id, turn_id, Command(resume=validated))


def make_controller(repo, settings, library_settings=None, integration_settings=None):
    from chat_service import ChatService
    from story.roles import StoryKeeper
    from improvisation.roles import Narrator
    from library.settings import load_library_settings
    from .settings import load_integration_settings
    from .roles import GameMaster, Improviser
    from .library import LocalConsultant
    transport = ChatService(settings)  # Nie podmieniamy modeli ani ustawienia Responses API.
    consultant = LocalConsultant(library_settings or load_library_settings(),
        integration_settings or load_integration_settings(), settings.provider)
    return RulesController(repo, GameMaster(transport), Narrator(transport), StoryKeeper(transport),
        Improviser(transport), consultant, history_limit=settings.max_history_turns, max_chars=settings.max_input_chars)
