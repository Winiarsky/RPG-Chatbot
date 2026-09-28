import pytest
pytest.importorskip('langgraph',reason='Wymaga rzeczywistego LangGraph.')
pytest.importorskip('langgraph.checkpoint.sqlite',reason='Wymaga SqliteSaver.')
from chat_service import ChatError
from agents4.repository import WorkflowError
from story5.repository import StoryRepository
from story5.runtime import StoryController
from .smoke import scenario_test
from .conftest import open_and_enter


def controller(repo,nodes):
    return StoryController(repo,nodes.gm,nodes.narrator,nodes.keeper)


def test_full_graph_scenario_smoke():
    scenario_test()


def test_cancel_story_checkpoint_has_no_effect(repo,nodes):
    open_and_enter(repo)
    c=controller(repo,nodes)
    pending=c.start('demo5','Idę do archiwum',turn_id='up')
    before=repo.progress('demo5')
    done=c.resume('demo5','up',pending['interrupts'][0]['id'],{'choice':'cancel'})
    assert done['run']['status']=='done'
    assert repo.progress('demo5')==before
    assert repo.load('demo5').state.scene_id=='vestibule'


def test_narrator_error_after_story_commit_retry_only_description(repo,nodes):
    open_and_enter(repo)
    class Once:
        def __init__(self): self.calls=0
        def narrate(self,*args):
            self.calls+=1
            if self.calls==1: raise ChatError('test narrator failure')
            return 'Marta odpowiada.'
    narrator=Once()
    c=StoryController(repo,nodes.gm,narrator,nodes.keeper)
    view=c.start('demo5','Pytam Martę o latarnika',turn_id='talk')
    wait=c.resume('demo5','talk',view['interrupts'][0]['id'],{'choice':'confirm'})
    assert wait['interrupts'][0]['payload']['kind']=='narration_retry'
    assert repo.story('demo5')[1].npc_memory['marta'].conversations==1
    fresh=StoryController(StoryRepository(repo.db_path),nodes.gm,narrator,nodes.keeper)
    done=fresh.resume('demo5','talk',wait['interrupts'][0]['id'],{'choice':'retry'})
    assert done['run']['status']=='done'
    assert repo.story('demo5')[1].npc_memory['marta'].conversations==1
    assert narrator.calls==2


def test_keeper_failure_is_retryable_before_effect(repo,nodes):
    open_and_enter(repo)
    original=nodes.keeper
    class Once:
        def __init__(self): self.calls=0
        def choose(self,*args):
            self.calls+=1
            if self.calls==1: raise ChatError('test keeper failure')
            return original.choose(*args)
    keeper=Once()
    c=StoryController(repo,nodes.gm,nodes.narrator,keeper)
    wait=c.start('demo5','Pytam Martę o latarnika',turn_id='talk')
    with pytest.raises(WorkflowError):
        c.resume('demo5','talk',wait['interrupts'][0]['id'],{'choice':'confirm'})
    assert repo.story('demo5')[1].npc_memory=={}
    assert c.retry('demo5','talk')['run']['status']=='done'
    assert repo.story('demo5')[1].npc_memory['marta'].conversations==1


def test_crash_after_story_commit_before_checkpoint(repo,nodes):
    open_and_enter(repo)
    c=controller(repo,nodes)
    wait=c.start('demo5','Pytam Martę o latarnika',turn_id='talk')
    original=c.nodes.commit_story
    def crash(state):
        original(state)
        raise RuntimeError('Crash after commit')
    c.nodes.commit_story=crash
    with pytest.raises(WorkflowError):
        c.resume('demo5','talk',wait['interrupts'][0]['id'],{'choice':'confirm'})
    assert repo.story('demo5')[1].npc_memory['marta'].conversations==1
    fresh=controller(StoryRepository(repo.db_path),nodes)
    assert fresh.retry('demo5','talk')['run']['status']=='done'
    assert repo.story('demo5')[1].npc_memory['marta'].conversations==1


def test_bad_confirmation_does_not_destroy_pending(repo,nodes):
    open_and_enter(repo)
    c=controller(repo,nodes)
    wait=c.start('demo5','Idę do archiwum',turn_id='up')
    iid=wait['interrupts'][0]['id']
    with pytest.raises(ValueError):
        c.resume('demo5','up',iid,{'choice':'confirm','dice':[20]})
    assert c.view('demo5','up')['interrupts'][0]['id']==iid
    assert c.resume('demo5','up',iid,{'choice':'confirm'})['run']['status']=='done'
