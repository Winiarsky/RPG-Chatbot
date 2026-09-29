import json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from chat_service import ChatError
from improvisation.models import RecoveryPlan
from improvisation.roles import plan
from .conftest import begin, finish_knock


@pytest.mark.parametrize('text', ['pukam', 'pukam w drzwi', 'no pukam w drzwi zeby ktos mnie wpuscil',
    'pukam zeby ktos mnie wpuscil', 'wolam czy ktos jest w srodku', 'prosze o otwarcie drzwi'])
def test_user_examples_resolve_to_signal(repo, nodes, text):
    s = begin(repo, nodes, text, 'x')
    s.update(nodes.recover(s))
    assert s['recovery']['mode'] == 'interaction'
    assert s['intent']['definition_id'] == 'signal_tower_door'
    assert not repo.load('demo').state.doors['tower_door'].is_open
    assert repo.progress('demo')['elapsed_seconds'] == 0


def test_real_user_failure_clarify_then_answer(repo, nodes):
    repo.new_run('demo','previous','pukam',4000)
    repo.finish_run('demo','previous','Co dokładnie robisz, pukając?', expected_revision=repo.load('demo').revision)
    s = begin(repo,nodes,'zeby ktos mnie wpuscil','next')
    s.update(nodes.recover(s))
    assert s['intent']['definition_id'] == 'signal_tower_door'
    assert s['history'][0]['content'] == 'pukam'


def test_success_no_auto_move_and_one_message_pair(repo, nodes):
    s = finish_knock(repo,nodes)
    assert s['finished'] and 'Marta' in s['answer']
    assert 'Ta czynność nie jest' not in s['answer']
    assert repo.load('demo').state.scene_id == 'tower_entrance'
    assert repo.load('demo').state.doors['tower_door'].is_open
    assert len(repo.bundle4('demo')[3]) == 1
    assert repo.progress('demo') == {'elapsed_seconds':6, 'noise_events':0}


def test_public_packet_does_not_preannounce_responder_or_secrets(repo):
    context,catalog,_,_ = repo.bundle4('demo')
    raw = json.dumps([context,catalog],ensure_ascii=False)
    book,_ = repo.story('demo')
    assert context['scene']['contacts'] == []
    assert 'Marta' not in raw
    for fact in book.facts:
        assert fact.text not in raw
    assert book.scenario.scenes[0].gm_only[0].text not in raw
    assert 'first_reply' not in raw and 'can_open_from_inside' not in raw


def test_clothing_gesture_does_not_change_world(repo,nodes):
    before=repo.load('demo').state.model_dump()
    s=begin(repo,nodes,'Otrzepuję płaszcz','gesture')
    for n in ('recover','narrate','finish'):
        s.update(getattr(nodes,n)(s))
    assert s['recovery']['mode']=='narrate'
    assert repo.load('demo').state.model_dump()==before
    assert repo.progress('demo')['elapsed_seconds']==0
    assert repo.pending_action('demo') is None


def test_unsupported_actual_mechanic_is_not_fictional_barrier(repo,nodes):
    s=begin(repo,nodes,'Atakuję goblina','attack')
    for n in ('recover','fixed_reply','finish'):
        s.update(getattr(nodes,n)(s))
    assert s['recovery']['mode']=='defer'
    assert 'Poza fikcją' in s['answer']
    assert 'mechaniki' in s['answer']
    assert repo.pending_action('demo') is None


def test_blocked_move_suggests_but_never_knocks(repo,nodes):
    s=begin(repo,nodes,'Wchodzę do wieży','enter')
    s.update(nodes.prepare(s))
    assert s['recovery_reason']
    for n in ('recover','fixed_reply','finish'):
        s.update(getattr(nodes,n)(s))
    assert s['recovery']['mode']=='clarify'
    assert 'Zapukasz' in s['answer']
    assert not repo.load('demo').state.doors['tower_door'].is_open
    assert repo.progress('demo')['elapsed_seconds']==0


def test_blocked_recovery_cannot_swap_to_action(repo,nodes):
    s=begin(repo,nodes,'Wchodzę do wieży','enter')
    s.update(nodes.prepare(s))
    a=s['context']['improvisation']['interactions'][0]
    nodes.improviser=SimpleNamespace(recover=lambda *args: plan('interaction','Pukam.',a))
    with pytest.raises(ChatError,match='zablokowane'):
        nodes.recover(s)
    assert repo.pending_action('demo') is None


@pytest.mark.parametrize('mode,ident,target', [('interaction','give_gold','pc_torin'),
    ('interaction','signal_tower_door','unknown'), ('map_action','signal_tower_door','tower_door')])
def test_fabricated_or_wrong_route_ids_blocked(repo,nodes,mode,ident,target):
    s=begin(repo,nodes,'pukam','x')
    bad=RecoveryPlan(mode=mode,definition_id=ident,target_id=target,message='Opis',alternative_ids=[])
    nodes.improviser=SimpleNamespace(recover=lambda *args:bad)
    with pytest.raises(ChatError):
        nodes.recover(s)
    assert repo.pending_action('demo') is None


def test_fabricated_alternatives_blocked(repo,nodes):
    s=begin(repo,nodes,'pukam','x')
    nodes.improviser=SimpleNamespace(recover=lambda *args:plan('clarify','Które?',alternatives=['free_gold']))
    with pytest.raises(ChatError,match='alternatywę'):
        nodes.recover(s)


def test_two_valid_targets_need_real_clarification(repo,nodes):
    context,catalog,history,_=repo.bundle4('demo')
    original=context['improvisation']['interactions'][0]
    context['improvisation']['interactions'].append({**original,'definition_id':'signal_second','target_id':'second'})
    result=nodes.improviser.recover('pukam',history,context,catalog,{},None)
    assert result.mode=='clarify'
    assert len(result.alternative_ids)==2


@pytest.mark.parametrize('extra', [{'hp':100},{'effects':[{'open_all':True}]},{'code':'print(1)'}])
def test_model_cannot_supply_state_or_code(extra):
    data=plan('narrate','Wzdycham.').model_dump()
    with pytest.raises(ValidationError):
        RecoveryPlan.model_validate({**data,**extra})


def test_same_intent_synonym_maps_to_existing_action(repo,nodes):
    s=begin(repo,nodes,'oglądam okolicę','look')
    s.update(nodes.recover(s))
    assert s['intent']['definition_id']=='observe_entrance'


def test_narrator_failure_keeps_recorded_signal(repo,nodes):
    def fail(*args):
        raise ChatError('Błąd testowy narratora.')
    nodes.narrator=SimpleNamespace(narrate=fail)
    s=begin(repo,nodes,'pukam','x')
    for n in ('recover','prepare','plan_story','commit_story','narrate'):
        s.update(getattr(nodes,n)(s))
    assert s['narration_error']
    assert repo.load('demo').state.doors['tower_door'].is_open
    s.update(nodes.fallback(s));s.update(nodes.finish(s))
    assert s['finished']
    assert repo.progress('demo')['elapsed_seconds']==6
