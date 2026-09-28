from pathlib import Path
import pytest
from game_config import GameSettings
from game_service import ensure_demo
from scenario_loader import load_templates
from mechanics3.repository import ActionRepository

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    values = {'LLM_PROVIDER': 'mock', 'OPENAI_API_KEY': '', 'OPENAI_MODEL': 'gpt-4.1-mini',
              'MAX_HISTORY_TURNS': '12', 'MAX_INPUT_CHARS': '4000', 'MAX_OUTPUT_TOKENS': '800',
              'REQUEST_TIMEOUT_SECONDS': '45', 'GAME_DB_PATH': str(tmp_path / 'game.sqlite3'),
              'GAME_CHARACTER_PATH': str(ROOT / 'characters/torin.yaml'),
              'GAME_SCENARIO_PATH': str(ROOT / 'scenarios/tower.yaml')}
    for key, value in values.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def character():
    return load_templates(ROOT / 'characters/torin.yaml', ROOT / 'scenarios/tower.yaml')[0]


@pytest.fixture
def repo(tmp_path):
    settings = GameSettings(tmp_path / 'game.sqlite3', ROOT / 'characters/torin.yaml', ROOT / 'scenarios/tower.yaml')
    repository = ActionRepository(settings.db_path)
    ensure_demo(repository, settings)
    repository.ensure_pack('demo')
    return repository


def prepare(repo, action_id='try1', definition_id='force_door'):
    return repo.prepare_action('demo', definition_id, action_id=action_id,
                               expected_revision=repo.load('demo').revision)
