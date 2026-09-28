from pathlib import Path
import pytest
from game_config import GameSettings
from game_service import ensure_demo
from agents4.repository import GraphRepository
from agents4.nodes import Nodes
from agents4.roles import GameMaster, Narrator
from chat_service import ChatService
from config import Settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    values = {'LLM_PROVIDER': 'mock', 'OPENAI_API_KEY': '', 'OPENAI_MODEL': 'gpt-4.1-mini',
              'MAX_HISTORY_TURNS': '12', 'MAX_INPUT_CHARS': '4000', 'MAX_OUTPUT_TOKENS': '800',
              'REQUEST_TIMEOUT_SECONDS': '45', 'GAME_DB_PATH': str(tmp_path / 'game.sqlite3'),
              'GAME_CHARACTER_PATH': str(ROOT / 'characters/torin.yaml'),
              'GAME_SCENARIO_PATH': str(ROOT / 'scenarios/tower.yaml'), 'RPG4_INTENT_MODE': 'function_calling'}
    for key, value in values.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def repo(tmp_path):
    settings = GameSettings(tmp_path / 'game.sqlite3', ROOT / 'characters/torin.yaml', ROOT / 'scenarios/tower.yaml')
    result = GraphRepository(settings.db_path)
    ensure_demo(result, settings)
    result.ensure_pack('demo')
    return result


@pytest.fixture
def nodes(repo):
    transport = ChatService(Settings(provider='mock'))
    return Nodes(repo, GameMaster(transport), Narrator(transport))


def begin(repo, nodes, text='Wyważam drzwi', turn_id='try1'):
    repo.new_run('demo', turn_id, text, 4000)
    state = {'campaign_id': 'demo', 'turn_id': turn_id, 'user_text': text}
    for name in ('load_context', 'interpret'):
        state.update(getattr(nodes, name)(state))
    return state


def resolve(nodes, state, dice=10):
    state.update(nodes.prepare(state))
    state['roll_decision'] = {'choice': 'manual', 'dice': [dice]}
    state.update(nodes.resolve(state))
    return state
