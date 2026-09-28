"""Kontrakty stanu i szablonów. To nie jest jeszcze silnik zasad D&D."""
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
Text = Annotated[str, Field(min_length=1, max_length=12000)]
Score = Annotated[int, Field(strict=True, ge=1, le=30)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AbilityScores(Model):
    strength: Score
    dexterity: Score
    constitution: Score
    intelligence: Score
    wisdom: Score
    charisma: Score


class InventoryItem(Model):
    id: Identifier
    name: Text
    quantity: int = Field(default=1, strict=True, ge=1)


class CharacterState(Model):
    id: Identifier
    name: Text
    character_class: str
    level: int = Field(strict=True, ge=1, le=20)
    hp_current: int = Field(strict=True, ge=0)
    hp_max: int = Field(strict=True, ge=1)
    armor_class: int = Field(strict=True, ge=0)
    speed_ft: int = Field(strict=True, ge=0)
    proficiency_bonus: int = Field(strict=True, ge=0)
    abilities: AbilityScores
    skill_proficiencies: list[str] = Field(default_factory=list)
    inventory: list[InventoryItem] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_character(self) -> Self:
        if self.hp_current > self.hp_max:
            raise ValueError("Bieżące HP nie może przekraczać maksymalnego HP.")
        ids = [item.id for item in self.inventory]
        if len(ids) != len(set(ids)):
            raise ValueError("Identyfikatory przedmiotów muszą być unikalne.")
        return self


class DoorDefinition(Model):
    id: Identifier
    name: Text
    initially_open: bool = False
    visible_when_closed: Text
    visible_when_open: Text


class Secret(Model):
    id: Identifier
    text: Text


class SceneDefinition(Model):
    id: Identifier
    name: Text
    public_description: Text
    doors: list[DoorDefinition] = Field(default_factory=list)
    gm_only: list[Secret] = Field(default_factory=list)


class ScenarioDefinition(Model):
    schema_version: Literal[1] = 1
    id: Identifier
    title: Text
    ruleset_id: Literal["dnd_2024_srd_5_2_1"]
    start_scene_id: Identifier
    scenes: list[SceneDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        scene_ids = [scene.id for scene in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("Identyfikatory scen muszą być unikalne.")
        if self.start_scene_id not in scene_ids:
            raise ValueError("Scena startowa nie istnieje.")
        door_ids = [door.id for scene in self.scenes for door in scene.doors]
        if len(door_ids) != len(set(door_ids)):
            raise ValueError("Identyfikatory drzwi muszą być unikalne w scenariuszu.")
        return self


class DoorState(Model):
    is_open: bool = Field(strict=True)


class GameState(Model):
    schema_version: Literal[1] = 1
    ruleset_id: Literal["dnd_2024_srd_5_2_1"]
    scene_id: Identifier
    character: CharacterState
    doors: dict[str, DoorState]


class CampaignSnapshot(Model):
    campaign_id: Identifier
    name: Text
    revision: int = Field(strict=True, ge=0)
    state: GameState
    scenario: ScenarioDefinition

    @model_validator(mode="after")
    def validate_world(self) -> Self:
        if self.state.ruleset_id != self.scenario.ruleset_id:
            raise ValueError("Wersja zasad stanu i scenariusza nie pasuje.")
        if self.state.scene_id not in {s.id for s in self.scenario.scenes}:
            raise ValueError("Bieżąca scena nie istnieje w scenariuszu.")
        expected_doors = {d.id for s in self.scenario.scenes for d in s.doors}
        if set(self.state.doors) != expected_doors:
            raise ValueError("Stan drzwi nie odpowiada obiektom scenariusza.")
        return self
