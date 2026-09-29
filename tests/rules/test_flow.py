import pytest
from chat_service import ChatError
from library.librarian import Librarian
from library.search import Retriever
from rules.models import RuleNeed
from rules.repository import RulesRepository
from rules.nodes import RulesNodes
from .conftest import begin, drive


def test_question_does_not_change_world(repo, nodes, consultant):
    before, progress = repo.load('demo').state, repo.progress('demo')
    state, end = drive(nodes, begin(repo, nodes, '/zasady Jak działa przewaga?'))
    assert end == 'done' and state['rules_record']['answer']['status'] == 'supported'
    assert repo.load('demo').state == before and repo.progress('demo') == progress
    assert repo.pending_action('demo') is None and len(consultant.calls) == 1
    assert '[1]' in repo.recorded_answer('demo', 'turn')


@pytest.mark.parametrize('text', ['Jak działa przewaga?', 'Kiedy doliczam biegłość?'])
def test_natural_question_routes_to_library(repo, nodes, text):
    state, end = drive(nodes, begin(repo, nodes, text))
    assert end == 'done' and state['rules_origin'] == 'gm' and state['action'] is None


def test_rules_prefix_never_executes_knock(repo, nodes):
    state, end = drive(nodes, begin(repo, nodes, '/zasady Pukam'))
    assert end == 'done' and state['force_rules_only']
    assert not repo.load('demo').state.doors['tower_door'].is_open
    assert repo.progress('demo')['elapsed_seconds'] == 0


def test_empty_prefix_is_not_an_action(repo, nodes):
    state, end = drive(nodes, begin(repo, nodes, '/zasady'))
    assert end == 'done' and 'Po /zasady' in state['answer']
    assert repo.pending_action('demo') is None


@pytest.mark.parametrize('prefix', ['/zasady', '/rules'])
def test_long_rules_question_finishes_without_blocking_next_turn(repo, nodes, consultant, prefix):
    before, progress = repo.load('demo').state, repo.progress('demo')
    state, end = drive(nodes, begin(repo, nodes, prefix + ' ' + 'a' * 1201))
    assert end == 'done' and '1200' in state['answer']
    assert not consultant.calls and repo.active_run('demo') is None
    assert repo.load('demo').state == before and repo.progress('demo') == progress
    assert repo.pending_action('demo') is None
    assert drive(nodes, begin(repo, nodes, '/zasady Jak działa przewaga?', tid='next'))[1] == 'done'


def test_question_does_not_call_narrator(repo, nodes):
    def fail(*args):
        pytest.fail('Narrator nie może przepisywać odpowiedzi o zasadach.')
    nodes.narrator.narrate = fail
    assert drive(nodes, begin(repo, nodes, '/zasady Jak działa przewaga?'))[1] == 'done'


def test_knock_keeps_old_flow_without_lookup(repo, nodes, consultant):
    state, end = drive(nodes, begin(repo, nodes, 'pukam'))
    assert end == 'done' and not consultant.calls
    assert repo.load('demo').state.doors['tower_door'].is_open
    assert repo.load('demo').state.scene_id == 'tower_entrance'
    assert repo.progress('demo')['elapsed_seconds'] == 6


def test_plain_force_door_does_not_lookup(repo, nodes, consultant):
    state, end = drive(nodes, begin(repo, nodes, 'Próbuję wyważyć drzwi'))
    assert end == 'await_roll' and not consultant.calls
    assert state['action']['plan']['bonus'] == 5


def test_help_is_consulted_but_not_replaced_by_force_door(repo, nodes):
    before = repo.load('demo').state
    state, end = drive(nodes, begin(repo, nodes, 'Pomagam towarzyszowi wyważyć drzwi'))
    assert end == 'done' and state['rules_origin'] == 'improviser'
    assert state['rules_record']['answer']['status'] == 'supported'
    assert 'help' in state['answer'] and 'ograniczenie aplikacji' in state['answer']
    assert repo.pending_action('demo') is None and repo.load('demo').state == before
    assert repo.progress('demo')['elapsed_seconds'] == 0


def test_dynamic_advantage_not_applied_by_text(repo, nodes):
    state, end = drive(nodes, begin(repo, nodes, 'Wyważam drzwi z przewagą'))
    assert end == 'done' and 'dynamic_advantage' in state['answer']
    assert state['action'] is None and repo.progress('demo')['elapsed_seconds'] == 0


def test_consulted_action_preserves_engine_parameters(repo, nodes, consultant):
    state, end = drive(nodes, begin(repo, nodes, 'Sprawdź zasady Atletyki i próbuję wyważyć drzwi'))
    assert end == 'await_roll' and len(consultant.calls) == 1
    assert state['action']['plan']['bonus'] == 5 and state['action']['plan']['spec']['dc'] == 15
    state['roll_decision'] = {'choice': 'manual', 'dice': [10]}
    state, end = drive(nodes, state, 'resolve')
    assert end == 'done' and len(consultant.calls) == 1
    assert repo.progress('demo')['elapsed_seconds'] == 60 and repo.load('demo').state.doors['tower_door'].is_open
    assert '[1]' in state['answer']


def test_missing_source_does_not_guess(repo, nodes):
    state, end = drive(nodes, begin(repo, nodes, '/zasady Ile obrażeń zadaje Fireball?'))
    assert end == 'done' and state['rules_record']['answer']['status'] == 'insufficient'
    assert not state['rules_record']['answer']['claims']
    assert state['action'] is None


def test_source_mode_never_counts_as_supported(repo, nodes, library):
    store, cfg = library
    reader = Librarian(Retriever(store, cfg))
    nodes.consultant = type('SourceOnly', (), {'consult': lambda self, q, r: reader.consult(q)})()
    state, end = drive(nodes, begin(repo, nodes, 'Sprawdź zasady Atletyki i próbuję wyważyć drzwi'))
    assert end == 'done' and state['rules_record']['answer']['status'] == 'retrieved_only'
    assert state['action'] is None


def test_library_failure_is_pending_without_effects(repo, nodes):
    def fail(*args):
        raise ChatError('Testowa niedostępność API')
    nodes.consultant.consult = fail
    state, end = drive(nodes, begin(repo, nodes, 'Sprawdź zasady Atletyki i próbuję wyważyć drzwi'))
    assert end == 'await_rules' and state['rules_error']
    assert repo.pending_action('demo') is None and repo.consultation('demo', 'turn') is None
    state, end = drive(nodes, state, 'stop_rules')
    assert end == 'done' and 'przerwana' in state['answer']
    assert repo.progress('demo')['elapsed_seconds'] == 0


def test_retry_only_library(repo, nodes, consultant):
    original = consultant.consult
    nodes.consultant.consult = lambda *args: (_ for _ in ()).throw(ChatError('Test błędu'))
    state, end = drive(nodes, begin(repo, nodes, '/zasady Jak działa przewaga?'))
    assert end == 'await_rules'
    nodes.consultant.consult = original
    assert drive(nodes, state, 'consult_rules')[1] == 'done'


def test_cached_consultation_survives_new_repository(repo, nodes, consultant):
    state = begin(repo, nodes, '/zasady Jak działa przewaga?')
    state.update(nodes.interpret(state))
    first = nodes.consult_rules(state)
    nodes.repo = RulesRepository(repo.db_path)
    second = nodes.consult_rules(state)
    assert first == second and len(consultant.calls) == 1
    assert len(nodes.repo.consultations('demo')) == 1


def test_error_in_narrator_does_not_repeat_lookup_or_dice(repo, nodes, consultant):
    state, end = drive(nodes, begin(repo, nodes, 'Sprawdź zasady Atletyki i próbuję wyważyć drzwi'))
    def fail(*args):
        raise ChatError('Test narratora')
    nodes.narrator.narrate = fail
    state['roll_decision'] = {'choice': 'manual', 'dice': [10]}
    state, end = drive(nodes, state, 'resolve')
    assert end == 'await_narrator' and repo.load('demo').state.doors['tower_door'].is_open
    state, end = drive(nodes, state, 'fallback')
    assert end == 'done' and len(consultant.calls) == 1
    assert repo.progress('demo')['elapsed_seconds'] == 60


def test_library_receives_no_private_or_public_campaign_context(repo, nodes, consultant):
    state, _ = drive(nodes, begin(repo, nodes, 'Pomagam towarzyszowi wyważyć drzwi'))
    assert consultant.calls == [('Help action: assisting an ability check, requirements.', 'dnd_2024_srd_5_2_1')]
    assert 'Marta' not in consultant.calls[0][0] and 'Torin' not in consultant.calls[0][0]


def test_world_change_during_lookup_does_not_execute_action(repo, nodes, consultant):
    state = begin(repo, nodes, 'Sprawdź zasady Atletyki i próbuję wyważyć drzwi')
    state.update(nodes.interpret(state))
    state.update(nodes.consult_rules(state))
    # Zewnętrzna korekta (nie wymagamy działania prywatnego modelu).
    with repo.connection(write=True) as db:
        db.execute('UPDATE campaigns SET revision=revision+1 WHERE campaign_id=?', ('demo',))
    result = nodes.reconsider(state)
    assert result['rules_after'] == 'rules_reply' and 'Stan zmienił' in result['rules_block']
    assert repo.pending_action('demo') is None


def test_clarification_after_consultation_is_not_lost(repo, nodes):
    from agents.models import Intent
    from improvisation.roles import plan
    nodes.gm.after_rules = lambda *args: Intent(kind='unsupported', definition_id=None,
        target_id=None, message='Potrzeba dopasowania.')
    nodes.improviser.after_rules = lambda *args: plan('clarify', 'Którego sposobu otwarcia chcesz spróbować?')
    state, end = drive(nodes, begin(repo, nodes, 'Sprawdź zasady Atletyki i próbuję wyważyć drzwi'))
    assert end == 'done' and 'Którego sposobu otwarcia' in state['answer']
    assert state['action'] is None
