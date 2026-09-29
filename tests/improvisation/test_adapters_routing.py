from types import SimpleNamespace
import pytest
from chat_service import ChatService, ChatError
from config import Settings
from improvisation.roles import Improviser, plan
from improvisation.routing import route_intent, route_prepared, route_recovery


@pytest.mark.parametrize('mode',['function_calling','json_text'])
def test_live_adapter_contract_without_api(repo,mode):
    context,catalog,_,_=repo.bundle4('demo')
    expected=plan('interaction','Pukam.',context['improvisation']['interactions'][0])
    recorded=[]
    class FakeModel:
        def with_structured_output(self,schema,method):
            assert method=='function_calling'
            return SimpleNamespace(invoke=lambda messages: (recorded.append(messages),expected)[1])
        def invoke(self,messages):
            recorded.append(messages)
            return SimpleNamespace(text=expected.model_dump_json())
    t=ChatService(Settings(provider='openai',api_key='not-a-real-key'), model=FakeModel())
    agent=Improviser(t,mode=mode)
    history=[{'role':'user','content':'pukam'}, {'role':'assistant','content':'W co pukasz?'}]
    out=agent.recover('w drzwi',history,context,catalog,{},None)
    assert out==expected
    assert recorded[0][1:]==[*history,{'role':'user','content':'w drzwi'}]
    assert 'blocked_reason' in recorded[0][0]['content']
    assert repo.story('demo')[0].scenario.scenes[0].gm_only[0].text not in recorded[0][0]['content']


def test_broken_json_does_not_execute_anything(repo):
    context,catalog,_,_=repo.bundle4('demo')
    model=SimpleNamespace(invoke=lambda messages:SimpleNamespace(text='not json'))
    t=ChatService(Settings(provider='openai',api_key='not-a-real-key'),model=model)
    with pytest.raises(ChatError):
        Improviser(t,mode='json_text').recover('pukam',[],context,catalog,{},None)
    assert repo.progress('demo')['elapsed_seconds']==0


def test_routing_fallback_includes_both_previous_failure_paths():
    assert route_intent({'intent':{'kind':'clarify'}})=='recover'
    assert route_intent({'intent':{'kind':'unsupported'}})=='recover'
    assert route_intent({'intent':{'kind':'chat'}})=='narrate'
    assert route_intent({'intent':{'kind':'action'}})=='prepare'


def test_routing_bounded_no_automatic_recovery_loop():
    assert route_prepared({'action':None})=='recover'
    assert route_prepared({'action':None,'recovery_attempted':True})=='fixed_reply'
    assert route_recovery({'recovery':{'mode':'clarify'}})=='fixed_reply'
    assert route_recovery({'recovery':{'mode':'interaction'}})=='prepare'


def test_signal_auto_but_move_and_roll_wait():
    def state(kind):
        return {'action':{'status':'pending','definition':{'kind':kind}}}
    assert route_prepared(state('signal'))=='plan_story'
    assert route_prepared(state('move'))=='await_story'
    assert route_prepared(state('talk'))=='await_story'
    assert route_prepared(state('force_door'))=='await_roll'
