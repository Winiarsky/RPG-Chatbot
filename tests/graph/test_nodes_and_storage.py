import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from chat_service import ChatError
from storage import RequestConflict
from agents4.models import Intent
from agents4.repository import GraphRepository, WorkflowError
from agents4.runtime import GraphController
from tests.graph.conftest import begin, resolve


def test_mechanics_then_single_visible_turn(repo, nodes):
    state = resolve(nodes, begin(repo, nodes))
    assert state['action']['status'] == 'resolved'
    assert repo.load('demo').state.doors['tower_door'].is_open
    assert repo.active_run('demo')
    state.update(nodes.narrate(state))
    state.update(nodes.finish(state))
    assert state['finished']
    assert repo.progress('demo') == {'elapsed_seconds':60, 'noise_events':1}
    assert repo.active_run('demo') is None
    assert len(repo.bundle4('demo')[3]) == 1
    assert len(repo.read_bundle('demo')[1]) == 2  # wewnętrzny log mechaniki zachowany
    assert '10 +5 = 15' in state['answer']
    assert repo.bundle4('demo')[3][0]['user_text'] == 'Wyważam drzwi'


def test_replaying_resolve_and_save_is_idempotent(repo, nodes):
    state = resolve(nodes, begin(repo, nodes))
    state.update(nodes.resolve(state))
    state.update(nodes.narrate(state))
    state.update(nodes.finish(state))
    revision = repo.load('demo').revision
    saved = state['answer']
    # Odtworzenie węzła sprzed checkpointu z tą samą odpowiedzią nie dopisuje tury.
    assert nodes.finish(state)['answer'] == saved
    assert repo.load('demo').revision == revision
    assert repo.progress('demo')['elapsed_seconds'] == 60


def test_failed_check(repo, nodes):
    state = resolve(nodes, begin(repo, nodes), dice=9)
    assert state['action']['result']['check']['success'] is False
    assert repo.load('demo').state.doors['tower_door'].is_open is False
    assert repo.progress('demo')['noise_events'] == 1


def test_no_roll(repo, nodes):
    state = begin(repo, nodes, 'Rozglądam się')
    state.update(nodes.prepare(state))
    assert state['action']['status'] == 'resolved' and state['action']['plan'] is None
    assert repo.progress('demo') == {'elapsed_seconds':0, 'noise_events':0}


@pytest.mark.parametrize('text,kind', [('Otwieram drzwi', 'clarify'), ('Atakuję goblina', 'unsupported')])
def test_fixed_paths_do_not_mutate_world(repo, nodes, text, kind):
    state = begin(repo, nodes, text)
    before = repo.load('demo').state
    assert state['intent']['kind'] == kind
    state.update(nodes.fixed_reply(state)); state.update(nodes.finish(state))
    assert repo.load('demo').state == before
    assert repo.recent_actions('demo') == []


@pytest.mark.parametrize('definition,target', [('drop_database','tower_door'), ('force_door','other_door')])
def test_invalid_action_or_target_is_blocked(repo, nodes, definition, target):
    state = begin(repo, nodes)
    state['intent'] = Intent(kind='action', definition_id=definition,target_id=target,message='x').model_dump()
    result = nodes.prepare(state)
    assert 'Nie wykonano' in result['answer']
    assert repo.pending_action('demo') is None


def test_change_before_prepare(repo, nodes):
    state = begin(repo, nodes)
    repo.set_hp('demo',hp_current=7,expected_revision=repo.load('demo').revision,request_id='hp-change')
    assert 'Nie przygotowano' in nodes.prepare(state)['answer']
    assert repo.pending_action('demo') is None


def test_change_before_roll(repo, nodes):
    state = begin(repo,nodes); state.update(nodes.prepare(state))
    repo.set_hp('demo',hp_current=7,expected_revision=repo.load('demo').revision,request_id='hp-change')
    state['roll_decision']={'choice':'manual','dice':[20]}
    state.update(nodes.resolve(state))
    assert state['action']['status']=='invalidated'
    assert not repo.load('demo').state.doors['tower_door'].is_open
    assert repo.progress('demo')['elapsed_seconds']==0


def test_narrator_error_preserves_result_and_fallback(repo, nodes):
    state = resolve(nodes,begin(repo,nodes))
    class Broken:
        def narrate(self,*_): raise ChatError('TEST: opis niedostępny')
    nodes.narrator=Broken()
    state.update(nodes.narrate(state))
    assert state['narration_error']=='TEST: opis niedostępny'
    assert repo.load('demo').state.doors['tower_door'].is_open
    state.update(nodes.fallback(state)); state.update(nodes.finish(state))
    assert 'pominięty' in state['answer'] and '10 +5 = 15' in state['answer']
    assert repo.progress('demo')['elapsed_seconds']==60


def test_world_change_during_narration_does_not_save_stale_prose(repo,nodes):
    state=resolve(nodes,begin(repo,nodes)); state.update(nodes.narrate(state))
    state['answer']='UNSAFE_STALE_PROSE'
    repo.set_hp('demo',hp_current=7,expected_revision=repo.load('demo').revision,request_id='hp')
    state.update(nodes.finish(state))
    assert 'UNSAFE' not in state['answer'] and 'Stan zmienił się' in state['answer']
    assert repo.load('demo').state.character.hp_current==7


def test_cancel_before_roll(repo,nodes):
    state=begin(repo,nodes); state.update(nodes.prepare(state)); state['roll_decision']={'choice':'cancel'}
    state.update(nodes.resolve(state)); state.update(nodes.fixed_reply(state)); state.update(nodes.finish(state))
    assert state['action']['status']=='cancelled'
    assert repo.progress('demo')['elapsed_seconds']==0


@pytest.mark.parametrize('stage',['unprepared','pending','resolved'])
def test_abort_never_undoes_resolved_mechanics(repo,nodes,stage):
    state=begin(repo,nodes)
    if stage=='pending': state.update(nodes.prepare(state))
    if stage=='resolved': state=resolve(nodes,state)
    controller=GraphController(repo,nodes.gm,nodes.narrator)
    result=controller.abort('demo','try1')
    assert result['run']['status']=='done'
    assert repo.load('demo').state.doors['tower_door'].is_open == (stage=='resolved')
    assert repo.progress('demo')['elapsed_seconds']==(60 if stage=='resolved' else 0)
    revision=repo.load('demo').revision
    assert controller.abort('demo','try1')['answer']==result['answer']
    assert repo.load('demo').revision==revision


def test_context_contains_no_scenario_secrets(repo,nodes):
    state=begin(repo,nodes)
    serialized=json.dumps(state,ensure_ascii=False)
    for scene in repo.load('demo').scenario.scenes:
        for secret in scene.gm_only:
            assert secret.text not in serialized and secret.id not in serialized
    assert 'gm_only' not in serialized


def test_turn_id_and_active_guard(repo):
    repo.new_run('demo','x','abc',4000)
    assert repo.new_run('demo','x','abc',4000)['turn_id']=='x'
    with pytest.raises(RequestConflict): repo.new_run('demo','x','different',4000)
    with pytest.raises(WorkflowError): repo.new_run('demo','y','abc',4000)


def test_old_step3_pending_not_silently_adopted(repo):
    repo.prepare_action('demo','force_door',action_id='old',expected_revision=repo.load('demo').revision)
    with pytest.raises(WorkflowError,match='kroku 3'): repo.new_run('demo','new','x',4000)


def test_new_repository_retains_pending_run(repo,nodes):
    state=begin(repo,nodes); state.update(nodes.prepare(state))
    fresh=GraphRepository(repo.db_path)
    assert fresh.active_run('demo')['turn_id']=='try1'
    assert fresh.pending_action('demo')['action_id']=='try1'


def test_simultaneous_requests_allow_only_one_active(repo):
    def attempt(i):
        try: return repo.new_run('demo',f'r{i}','x',4000)['turn_id']
        except WorkflowError: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        values=list(pool.map(attempt,[1,2]))
    assert sum(v is not None for v in values)==1


def test_campaign_lock(repo):
    with repo.campaign_lock('demo'):
        with pytest.raises(WorkflowError):
            with repo.campaign_lock('demo'): pass
    with repo.campaign_lock('demo'): pass


def test_failed_final_transaction_rolls_back(repo,nodes):
    state=begin(repo,nodes,'Atakuję goblina'); state.update(nodes.fixed_reply(state))
    original=repo._event
    def broken(*_): raise RuntimeError('test transaction')
    repo._event=broken
    with pytest.raises(RuntimeError): nodes.finish(state)
    assert repo.get_turn('demo','s4:try1:reply') is None
    assert repo.active_run('demo')
    repo._event=original
    state.update(nodes.finish(state)); assert state['finished']
