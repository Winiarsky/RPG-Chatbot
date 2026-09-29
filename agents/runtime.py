"""Jeden kontroler dla UI i CLI. Nie wywołuj grafu z pominięciem kontroli deklaracji."""
from contextlib import contextmanager
import sqlite3
from uuid import uuid4
from pydantic import ValidationError
from chat_service import ChatError
from storage import StorageError
from mechanics.repository import ActionError
from .models import RollDecision, NarrationDecision
from .nodes import Nodes, fallback_answer, with_mechanics
from .repository import GraphRepository, WorkflowError, public_action


def validate_resume(payload: dict, value: dict) -> dict:
    if payload.get('kind') == 'roll':
        decision = RollDecision.model_validate(value)
        if decision.choice == 'manual' and len(decision.dice) != payload['plan']['dice_count']:
            raise ValueError(f"Ta próba wymaga {payload['plan']['dice_count']} kości.")
        return decision.model_dump()
    if payload.get('kind') == 'narration_retry':
        return NarrationDecision.model_validate(value).model_dump()
    raise WorkflowError('Nieobsługiwany rodzaj oczekującej decyzji.')


def safe_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return 'Dane nie pasują do schematu. Nie zmieniaj parametrów żądania; sprawdź model lub wyniki kości.'
    if isinstance(exc, (ChatError, StorageError, ValueError)):
        return str(exc)
    if isinstance(exc, ModuleNotFoundError):
        return 'Brakuje zależności. Zainstaluj requirements.txt w aktywnym środowisku.'
    # Nie pokazujemy surowych obiektów request, tokenów ani pełnego stanu grafu.
    return f'Błąd wykonania ({type(exc).__name__}). Uruchom manage_rules.py doctor i sprawdź instalację.'


class GraphController:
    def __init__(self, repo: GraphRepository, gm, narrator, *, history_limit: int = 12, max_chars: int = 4000):
        self.repo = repo
        self.nodes = Nodes(repo, gm, narrator, history_limit)
        self.max_chars = max_chars

    @staticmethod
    def config(campaign_id: str, turn_id: str) -> dict:
        # Osobny trwały thread dla jednej deklaracji; kampania jest osobnym bytem.
        return {'configurable': {'thread_id': f'rpg4:{campaign_id}:{turn_id}'}, 'recursion_limit': 30}

    @contextmanager
    def _graph(self):
        from langgraph.checkpoint.sqlite import SqliteSaver
        from .graph import build_graph
        connection = sqlite3.connect(str(self.repo.db_path), timeout=10, check_same_thread=False)
        try:
            saver = SqliteSaver(connection)
            saver.setup()
            yield build_graph(self.nodes, saver)
        finally:
            connection.close()

    def _view(self, campaign_id: str, turn_id: str, graph=None) -> dict:
        run = self.repo.get_run(campaign_id, turn_id)
        view = {'run': run, 'interrupts': [], 'next': [], 'intent': None,
                'answer': self.repo.recorded_answer(campaign_id, turn_id), 'action': None}
        try:
            view['action'] = public_action(self.repo.get_action(campaign_id, turn_id))
        except ActionError:
            pass
        if run['status'] == 'done' or graph is None:
            return view
        snap = graph.get_state(self.config(campaign_id, turn_id))
        view['intent'] = snap.values.get('intent') if snap.values else None
        view['next'] = list(snap.next)
        for task in snap.tasks:
            for item in task.interrupts:
                view['interrupts'].append({'id': item.id, 'payload': item.value})
        return view

    def view(self, campaign_id: str, turn_id: str) -> dict:
        with self.repo.campaign_lock(campaign_id):
            if self.repo.get_run(campaign_id, turn_id)['status'] == 'done':
                return self._view(campaign_id, turn_id)
            with self._graph() as graph:
                return self._view(campaign_id, turn_id, graph)

    def _invoke(self, graph, campaign_id: str, turn_id: str, value) -> dict:
        self.repo.set_run_status(campaign_id, turn_id, 'running')
        try:
            graph.invoke(value, config=self.config(campaign_id, turn_id))
        except Exception as exc:
            # Interrupty są obsługiwane przez LangGraph; nie łapiemy BaseException.
            self.repo.set_run_status(campaign_id, turn_id, 'failed', safe_error(exc))
            raise WorkflowError(safe_error(exc)) from None
        result = self._view(campaign_id, turn_id, graph)
        if result['run']['status'] != 'done':
            self.repo.set_run_status(campaign_id, turn_id, 'waiting' if result['interrupts'] else 'running')
            result['run'] = self.repo.get_run(campaign_id, turn_id)
        return result

    def _continue(self, graph, campaign_id: str, turn_id: str) -> dict:
        view = self._view(campaign_id, turn_id, graph)
        if view['run']['status'] == 'done' or view['interrupts']:
            return view
        snapshot = graph.get_state(self.config(campaign_id, turn_id))
        if snapshot.values and snapshot.next:
            return self._invoke(graph, campaign_id, turn_id, None)  # wznowienie nieudanego węzła
        if snapshot.values:
            raise WorkflowError('Graf zakończony bez zapisu deklaracji. Przerwij ją i sprawdź wersje plików.')
        run = view['run']
        return self._invoke(graph, campaign_id, turn_id, {
            'campaign_id': campaign_id, 'turn_id': turn_id, 'user_text': run['user_text']})

    def start(self, campaign_id: str, user_text: str, *, turn_id: str | None = None) -> dict:
        turn_id = turn_id or uuid4().hex
        with self.repo.campaign_lock(campaign_id):
            self.repo.ensure_pack(campaign_id)
            self.repo.new_run(campaign_id, turn_id, user_text, self.max_chars)
            with self._graph() as graph:
                return self._continue(graph, campaign_id, turn_id)

    def retry(self, campaign_id: str, turn_id: str) -> dict:
        with self.repo.campaign_lock(campaign_id):
            with self._graph() as graph:
                return self._continue(graph, campaign_id, turn_id)

    def resume(self, campaign_id: str, turn_id: str, interrupt_id: str, value: dict) -> dict:
        from langgraph.types import Command
        with self.repo.campaign_lock(campaign_id):
            with self._graph() as graph:
                view = self._view(campaign_id, turn_id, graph)
                if view['run']['status'] == 'done':
                    return view  # nigdy nie uruchamiaj zakończonej deklaracji ponownie
                current = next((i for i in view['interrupts'] if i['id'] == interrupt_id), None)
                if current is None:
                    raise WorkflowError('Ta decyzja jest już nieaktualna. Odśwież panel, nie wykonano nowego rzutu.')
                validated = validate_resume(current['payload'], value)  # przed zapisem Command
                return self._invoke(graph, campaign_id, turn_id, Command(resume=validated))

    def abort(self, campaign_id: str, turn_id: str) -> dict:
        """Przerwij obsługę, ale NIGDY nie cofaj zakończonej mechaniki."""
        with self.repo.campaign_lock(campaign_id):
            if self.repo.get_run(campaign_id, turn_id)['status'] == 'done':
                return self._view(campaign_id, turn_id)
            try:
                record = self.repo.get_action(campaign_id, turn_id)
            except ActionError:
                record = None
            if record and record['status'] == 'pending':
                record = self.repo.cancel_action(campaign_id, turn_id)
            action = public_action(record)
            message = 'Przerwano obsługę deklaracji. ' + fallback_answer(action)
            self.repo.finish_run(campaign_id, turn_id, with_mechanics(message, action), expected_revision=None)
            # Rejestr done blokuje późniejsze wznowienie starego checkpointu.
            return self._view(campaign_id, turn_id)


def make_controller(repo, settings):
    from chat_service import ChatService
    from .roles import GameMaster, Narrator
    transport = ChatService(settings)
    return GraphController(repo, GameMaster(transport), Narrator(transport),
                           history_limit=settings.max_history_turns, max_chars=settings.max_input_chars)
