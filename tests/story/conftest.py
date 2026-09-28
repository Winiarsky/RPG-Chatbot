from pathlib import Path
import sys
import pytest
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from config import Settings
from chat_service import ChatService
from story5.repository import StoryRepository
from story5.roles import GameMaster, StoryKeeper, Narrator
from story5.nodes import StoryNodes


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    for key, value in {'LLM_PROVIDER':'mock','OPENAI_API_KEY':'','GAME_DB_PATH':str(tmp_path/'game.sqlite3'),
        'GAME_CHARACTER_PATH':str(ROOT/'characters/torin.yaml'), 'RPG5_INTENT_MODE':'function_calling',
        'LANGSMITH_TRACING':'false', 'LANGCHAIN_TRACING_V2':'false', 'LANGCHAIN_TRACING':'false'}.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def repo(tmp_path):
    r = StoryRepository(tmp_path/'game.sqlite3')
    r.initialize()
    r.create_story('demo5','Test opiekuna',ROOT/'characters/torin.yaml')
    return r


@pytest.fixture
def nodes(repo):
    t = ChatService(Settings(provider='mock'))
    return StoryNodes(repo,GameMaster(t),Narrator(t),StoryKeeper(t))


def act(repo, definition, ident, beat=None):
    a = repo.prepare_action('demo5',definition,action_id=ident,expected_revision=repo.load('demo5').revision)
    return repo.commit_story('demo5',ident,{'beat_id':beat})


def open_and_enter(repo):
    repo.set_door('demo5',door_id='tower_door',is_open=True,expected_revision=repo.load('demo5').revision,request_id='open_test')
    return act(repo,'enter_tower','enter_test')


def begin(repo,nodes,text,tid):
    repo.new_run('demo5',tid,text,4000)
    state={'campaign_id':'demo5','turn_id':tid,'user_text':text}
    for name in ('load_context','interpret','prepare'):
        state.update(getattr(nodes,name)(state))
    return state
