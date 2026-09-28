"""Ustawienia gry osobno od działającej konfiguracji LLM z etapu 1."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class GameSettings:
    db_path: Path
    character_path: Path
    scenario_path: Path


def load_game_settings() -> GameSettings:
    values = {**dotenv_values(ROOT / ".env"), **os.environ}

    def path(name: str, default: str) -> Path:
        raw = str(values.get(name) or default).strip()
        candidate = Path(raw).expanduser()
        return (candidate if candidate.is_absolute() else ROOT / candidate).resolve()

    return GameSettings(
        db_path=path("GAME_DB_PATH", "data/rpg.sqlite3"),
        character_path=path("GAME_CHARACTER_PATH", "characters/torin.yaml"),
        scenario_path=path("GAME_SCENARIO_PATH", "scenarios/tower.yaml"),
    )
