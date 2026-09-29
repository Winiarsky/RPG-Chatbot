from pathlib import Path
import sys
import pytest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from config import Settings
from chat_service import ChatService
from story.roles import StoryKeeper
from improvisation.roles import GameMaster, Narrator, Improviser
from improvisation.nodes import RecoveryNodes
from improvisation.repository import ImprovisationRepository


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for key, value in {'LLM_PROVIDER':'mock', 'OPENAI_API_KEY':'',
        'GAME_DB_PATH': str(tmp_path/'game.sqlite3'),
        'GAME_CHARACTER_PATH': str(ROOT/'characters/torin.yaml'),
        'RPG5_INTENT_MODE':'function_calling',
        'LANGSMITH_TRACING':'false','LANGCHAIN_TRACING':'false','LANGCHAIN_TRACING_V2':'false'}.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def bare_repo(tmp_path):
    repo = ImprovisationRepository(tmp_path/'game.sqlite3')
    repo.initialize()
    repo.create_story('demo', 'Test', ROOT/'characters/torin.yaml')
    return repo


@pytest.fixture
def repo(bare_repo):
    bare_repo.enable('demo')
    return bare_repo


def make_nodes(repo):
    t = ChatService(Settings(provider='mock'))
    return RecoveryNodes(repo, GameMaster(t), Narrator(t), StoryKeeper(t), Improviser(t))


@pytest.fixture
def nodes(repo):
    return make_nodes(repo)


def begin(repo, nodes, text, tid):
    repo.new_run('demo', tid, text, 4000)
    s = {'campaign_id':'demo', 'turn_id':tid, 'user_text':text}
    for name in ('load_context','interpret'):
        s.update(getattr(nodes, name)(s))
    return s


def knock(repo, action_id='knock'):
    repo.prepare_action('demo', 'signal_tower_door', action_id=action_id,
        expected_revision=repo.load('demo').revision)
    return repo.commit_story('demo', action_id, {'beat_id':None})


def finish_knock(repo, nodes, text='pukam', tid='knock'):
    s = begin(repo, nodes, text, tid)
    for name in ('recover','prepare','plan_story','commit_story','narrate','finish'):
        s.update(getattr(nodes, name)(s))
    return s
