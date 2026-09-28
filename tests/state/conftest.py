from pathlib import Path

import pytest

from game_config import GameSettings
from game_service import ensure_demo
from storage import GameRepository

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    # Testy nigdy nie czytają klucza użytkownika i nie korzystają z jego bazy.
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GAME_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("GAME_CHARACTER_PATH", str(ROOT / "characters/torin.yaml"))
    monkeypatch.setenv("GAME_SCENARIO_PATH", str(ROOT / "scenarios/tower.yaml"))
    for name, value in {"OPENAI_MODEL": "gpt-4.1-mini", "MAX_HISTORY_TURNS": "12",
                        "MAX_INPUT_CHARS": "4000", "MAX_OUTPUT_TOKENS": "800",
                        "REQUEST_TIMEOUT_SECONDS": "45"}.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def game_settings(tmp_path):
    return GameSettings(tmp_path / "test.sqlite3", ROOT / "characters/torin.yaml",
                        ROOT / "scenarios/tower.yaml")


@pytest.fixture
def repo(game_settings):
    repository = GameRepository(game_settings.db_path)
    ensure_demo(repository, game_settings)
    return repository
