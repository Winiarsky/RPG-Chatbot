"""Szablony czytamy przy tworzeniu kampanii, nie przy każdym odczycie zapisu."""
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from schemas import CharacterState, DoorState, GameState, ScenarioDefinition

T = TypeVar("T", bound=BaseModel)


class TemplateError(ValueError):
    pass


def load_yaml(path: Path, model: type[T]) -> T:
    try:
        if path.stat().st_size > 1_000_000:
            raise TemplateError(f"Szablon {path.name} jest zbyt duży (limit 1 MB).")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return model.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        # Nie pokazujemy surowej zawartości gm_only w publicznym interfejsie.
        raise TemplateError(
            f"Niepoprawny lub niedostępny szablon {path.name} ({type(exc).__name__})."
        ) from None


def load_templates(character_path: Path, scenario_path: Path):
    return (
        load_yaml(character_path, CharacterState),
        load_yaml(scenario_path, ScenarioDefinition),
    )


def initial_state(character: CharacterState, scenario: ScenarioDefinition) -> GameState:
    return GameState(
        ruleset_id=scenario.ruleset_id,
        scene_id=scenario.start_scene_id,
        character=character,
        doors={d.id: DoorState(is_open=d.initially_open)
               for scene in scenario.scenes for d in scene.doors},
    )
