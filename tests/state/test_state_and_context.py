import json

from pydantic import ValidationError
import pytest
import yaml

from game_service import create_campaign, ensure_demo
from game_config import GameSettings, ROOT, load_game_settings
from public_context import build_public_context
from scenario_loader import TemplateError, load_yaml
from schemas import CampaignSnapshot, CharacterState, ScenarioDefinition


@pytest.mark.parametrize("field,value", [
    ("hp_current", -1), ("hp_current", 13), ("hp_current", True),
    ("hp_max", 0), ("level", 0), ("level", 21), ("armor_class", -1),
    ("hp_current", "12"), ("unrecognized_field", 123),
])
def test_invalid_character_rejected(repo, field, value):
    data = repo.load("demo").state.character.model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        CharacterState.model_validate(data)


def test_duplicate_inventory_ids_rejected(repo):
    data = repo.load("demo").state.character.model_dump()
    data["inventory"].append(data["inventory"][0])
    with pytest.raises(ValidationError):
        CharacterState.model_validate(data)


@pytest.mark.parametrize("problem", ["scene", "door", "ruleset"])
def test_broken_world_references_rejected(repo, problem):
    data = repo.load("demo").model_dump()
    if problem == "scene":
        data["state"]["scene_id"] = "missing"
    elif problem == "door":
        data["state"]["doors"] = {}
    else:
        data["state"]["ruleset_id"] = "dnd_2014"
    with pytest.raises(ValidationError):
        CampaignSnapshot.model_validate(data)


def test_secret_and_closed_view_not_sent_when_open(repo):
    snapshot = repo.load("demo")
    public = build_public_context(snapshot)
    serialized = json.dumps(public, ensure_ascii=False)
    secret = snapshot.scenario.scenes[0].gm_only[0]
    assert secret.text not in serialized
    assert secret.id not in serialized
    assert "gm_only" not in serialized
    assert "schody" not in serialized
    repo.set_door("demo", door_id="tower_door", is_open=True, expected_revision=0, request_id="open")
    context = build_public_context(repo.load("demo"))
    assert context["scene"]["doors"][0]["is_open"] is True
    assert "schody" in context["scene"]["doors"][0]["description"]
    opened = json.dumps(context, ensure_ascii=False)
    assert secret.text not in opened
    assert secret.id not in opened
    assert "gm_only" not in opened
    assert "zamknięte" not in context["scene"]["description"]


def test_serialization_roundtrip(repo):
    snapshot = repo.load("demo")
    assert CampaignSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_bad_yaml_and_unsafe_tag_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    for text in ["[broken", "!!python/object/apply:builtins.eval ['1+1']", "{}"]:
        path.write_text(text, encoding="utf-8")
        with pytest.raises(TemplateError):
            load_yaml(path, CharacterState)


def test_missing_or_large_yaml_rejected(tmp_path):
    path = tmp_path / "missing.yaml"
    with pytest.raises(TemplateError):
        load_yaml(path, CharacterState)
    path.write_text("#" * 1_000_001)
    with pytest.raises(TemplateError, match="duży"):
        load_yaml(path, CharacterState)


def test_init_does_not_overwrite_existing_snapshot(repo, game_settings, tmp_path):
    original = repo.load("demo")
    raw = original.scenario.model_dump()
    raw["scenes"][0]["public_description"] = "Zmieniony opis szablonu."
    changed = tmp_path / "changed.yaml"
    changed.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    altered = GameSettings(game_settings.db_path, game_settings.character_path, changed)
    assert ensure_demo(repo, altered) == original
    new = create_campaign(repo, altered, "second", "Druga kampania")
    assert new.scenario.scenes[0].public_description == "Zmieniony opis szablonu."
    assert repo.load("demo") == original


def test_game_db_path_relative_to_project(monkeypatch):
    monkeypatch.setenv("GAME_DB_PATH", "data/relative.sqlite3")
    assert load_game_settings().db_path == (ROOT / "data/relative.sqlite3").resolve()


def test_bad_start_scene_rejected(repo):
    data = repo.load("demo").scenario.model_dump()
    data["start_scene_id"] = "missing"
    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate(data)
