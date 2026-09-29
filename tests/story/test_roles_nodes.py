import json
from types import SimpleNamespace
import pytest
from chat_service import ChatService, ChatError
from config import Settings
from agents.models import Intent
from story.models import BeatSelection
from story.roles import GameMaster, StoryKeeper, Narrator, StructuredAdapter, ask_schema
from story.runtime import validate_story_resume, StoryController
from .conftest import begin, open_and_enter, act


def finish_story_nodes(nodes,state):
    for name in ('plan_story','commit_story','narrate','finish'):
        state.update(getattr(nodes,name)(state))
    return state


def test_nodes_route_story_and_keep_private_packet_out_of_graph(repo,nodes):
    open_and_enter(repo)
    state=begin(repo,nodes,'Pytam Martę o latarnika','talk')
    assert state['intent']['definition_id']=='talk_marta'
    state.update(nodes.plan_story(state))
    text=json.dumps(state,ensure_ascii=False)
    assert state['keeper_selection']=={'beat_id':'keeper'}
    assert 'allowed_beats' not in text and 'gm_notes' not in text
    assert 'Zacznij od ostatniego wpisu' not in text
    for step in ('commit_story','narrate','finish'):
        state.update(getattr(nodes,step)(state))
    assert repo.get_run('demo5','talk')['status']=='done'
    assert repo.story('demo5')[1].known_facts==['journal_hint']
    assert len(repo.bundle4('demo5')[3])==1
    assert nodes.finish(state)['answer']==state['answer']
    assert repo.story('demo5')[1].npc_memory['marta'].conversations==1


def test_model_failure_after_commit_does_not_repeat_effects(repo,nodes):
    open_and_enter(repo)
    state=begin(repo,nodes,'Pytam Martę o latarnika','talk')
    for step in ('plan_story','commit_story'):
        state.update(getattr(nodes,step)(state))
    original=nodes.narrator
    class Broken:
        def narrate(self,*args):
            raise ChatError('Awaria testowa narratora')
    nodes.narrator=Broken()
    state.update(nodes.narrate(state))
    assert state['narration_error']
    assert repo.story('demo5')[1].npc_memory['marta'].conversations==1
    nodes.narrator=original
    state.update(nodes.commit_story(state)) # symulacja powtórzenia po awarii
    state.update(nodes.narrate(state));state.update(nodes.finish(state))
    assert repo.story('demo5')[1].npc_memory['marta'].conversations==1
    assert repo.progress('demo5')['elapsed_seconds']==40


def test_bad_keeper_not_committed(repo,nodes):
    open_and_enter(repo)
    state=begin(repo,nodes,'Witam Martę','bad')
    class Bad:
        def choose(self,*args):
            return BeatSelection(beat_id='share_journal')
    nodes.keeper=Bad()
    with pytest.raises(Exception):
        state.update(nodes.plan_story(state))
    assert repo.story('demo5')[1].known_facts==[]
    assert repo.story('demo5')[1].npc_memory=={}


def test_narrator_only_receives_authorized_context(repo,nodes):
    open_and_enter(repo)
    state=begin(repo,nodes,'Witam Martę','hello')
    captured=[]
    class Spy:
        def narrate(self,*args):
            captured.append(args)
            return 'Marta wita bohatera.'
    nodes.narrator=Spy()
    finish_story_nodes(nodes,state)
    payload=json.dumps(captured,ensure_ascii=False)
    for private in ['Starym Moście','gm_notes','allowed_beats','ferryman_mark','zapieczętowana skrzynia']:
        assert private not in payload


def test_prepare_rejects_forged_target(repo,nodes):
    repo.new_run('demo5','forged','test',4000)
    state={'campaign_id':'demo5','turn_id':'forged','user_text':'test'}
    state.update(nodes.load_context(state))
    state['intent']={'kind':'action','definition_id':'force_door','target_id':'marta','message':'Fałszywy cel'}
    result=nodes.prepare(state)
    assert 'spoza katalogu' in result['answer']
    assert repo.pending_action('demo5') is None


@pytest.mark.parametrize('value', [{'choice':'manual','dice':[10]}, {'choice':'confirm','hp':10}, {'choice':True}])
def test_confirmation_schema_rejects_extra_fields(value):
    with pytest.raises(ValueError):
        validate_story_resume({'kind':'story_confirm'},value)


def test_confirmation_keeps_existing_roll_validation():
    assert validate_story_resume({'kind':'story_confirm'},{'choice':'confirm'})=={'choice':'confirm'}
    assert validate_story_resume({'kind':'roll','plan':{'dice_count':1}}, {'choice':'manual','dice':[10]})['dice']==[10]
    with pytest.raises(ValueError):
        validate_story_resume({'kind':'roll','plan':{'dice_count':1}},{'choice':'manual','dice':[10,11]})


@pytest.mark.parametrize('text,kind', [('Próbuję wyważyć drzwi','action'),('Otwieram drzwi','clarify'),
    ('Atakuję goblina','unsupported'),('Ile mam HP?','chat'),('Czy mogę wejść?','chat')])
def test_mock_intent_limits(repo,nodes,text,kind):
    context,catalog,history,_=repo.bundle4('demo5')
    intent=nodes.gm.interpret(text,history,context,catalog)
    assert intent.kind==kind


def test_mock_keeper_rejects_share_before_discovery(repo,nodes):
    open_and_enter(repo)
    repo.prepare_action('demo5','talk_marta',action_id='talk',expected_revision=repo.load('demo5').revision)
    result=nodes.keeper.choose('Opowiadam Marcie o dzienniku',[],repo.keeper_packet('demo5','talk'))
    assert result.beat_id=='other'


def test_structured_adapter_uses_function_calling_and_keeps_transport():
    received={}
    class Model:
        def with_structured_output(self,schema,**kwargs):
            received['schema']=schema;received.update(kwargs)
            return self
        def invoke(self,messages):
            received['messages']=messages
            return BeatSelection(beat_id='other')
    model=Model()
    transport=ChatService(Settings(provider='openai',api_key='test-not-real'),model=model)
    result=ask_schema(transport,BeatSelection,'PRIVATE_PROMPT','Hello',[],'function_calling')
    assert result.beat_id=='other'
    assert received['method']=='function_calling'
    assert received['messages'][0]['content']=='PRIVATE_PROMPT'
    assert transport.model is model


def test_json_text_adapter_does_not_need_function_calling():
    class Model:
        def invoke(self,messages):
            return SimpleNamespace(text='{"beat_id":"other"}')
    transport=ChatService(Settings(provider='openai',api_key='fake'),model=Model())
    assert ask_schema(transport,BeatSelection,'prompt','Hi',[],'json_text').beat_id=='other'


def test_bad_json_not_accepted():
    class Model:
        def invoke(self,messages):
            return SimpleNamespace(text='{"beat_id":"other","reveals":["spoiler"]}')
    transport=ChatService(Settings(provider='openai',api_key='fake'),model=Model())
    with pytest.raises(ChatError):
        ask_schema(transport,BeatSelection,'prompt','Hi',[],'json_text')


def test_checkpoint_namespace_changed():
    assert StoryController.config('c','t')['configurable']['thread_id']=='rpg5:c:t'


def test_full_story_sequence_through_real_nodes_without_scheduler(repo,nodes):
    """Łączy role i repozytorium; celowo NIE udaje testu schedulera LangGraph."""
    def step(text,tid,die=None):
        state=begin(repo,nodes,text,tid)
        if state['action']['status']=='pending':
            if state['action']['definition']['kind']=='force_door':
                state['roll_decision']={'choice':'manual','dice':[die]}
                state.update(nodes.resolve(state))
            else:
                state.update(nodes.plan_story(state));state.update(nodes.commit_story(state))
        state.update(nodes.narrate(state));state.update(nodes.finish(state))
        return state
    step('Próbuję wyważyć drzwi','fail',9)
    step('Próbuję wyważyć drzwi','pass',10)
    step('Wchodzę do wieży','enter')
    step('Witam Martę','first')
    step('Pytam Martę o latarnika','hint')
    step('Idę do archiwum','up')
    step('Rozglądam się','look')
    assert repo.story('demo5')[1].known_facts==['journal_hint']
    step('Czytam dziennik','read')
    step('Wracam do przedsionka','back')
    repeated=step('Witam Martę','again')
    assert 'Pamiętam' in repeated['answer']
    step('Opowiadam Marcie o dzienniku','share')
    assert repo.progress('demo5')=={'elapsed_seconds':330,'noise_events':2}
    assert repo.story('demo5')[1].npc_memory['marta'].conversations==4
    assert repo.story('demo5')[1].known_facts==['journal_hint','journal_revelation','ferryman_mark']
    assert len(repo.bundle4('demo5')[3])==11
