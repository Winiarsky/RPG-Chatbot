"""Kontrakty etapu 3. Nie zmieniają istniejącego schematu postaci i świata."""
from typing import Annotated, Literal, Self
from pydantic import Field, model_validator
from schemas import Identifier, Model, Text

Ability = Literal['strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma']
Skill = Literal['acrobatics', 'animal_handling', 'arcana', 'athletics', 'deception',
                'history', 'insight', 'intimidation', 'investigation', 'medicine',
                'nature', 'perception', 'performance', 'persuasion', 'religion',
                'sleight_of_hand', 'stealth', 'survival']
Die = Annotated[int, Field(strict=True, ge=1, le=20)]
Mode = Literal['normal', 'advantage', 'disadvantage']


class CheckSpec(Model):
    kind: Literal['ability_check'] = 'ability_check'
    ability: Ability
    skill: Skill | None = None
    dc: int = Field(strict=True, ge=1, le=60)
    dc_origin: Text
    advantage_sources: tuple[Text, ...] = ()
    disadvantage_sources: tuple[Text, ...] = ()


class CheckPlan(Model):
    actor_id: Identifier
    actor_name: Text
    spec: CheckSpec
    ability_score: int = Field(strict=True, ge=1, le=30)
    ability_modifier: int
    proficiency_applied: int = Field(strict=True, ge=0)
    bonus: int
    mode: Mode
    dice_count: Literal[1, 2]

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if self.ability_modifier != (self.ability_score - 10) // 2:
            raise ValueError('Niespójny modyfikator cechy.')
        if self.bonus != self.ability_modifier + self.proficiency_applied:
            raise ValueError('Niespójna suma modyfikatorów.')
        adv, dis = bool(self.spec.advantage_sources), bool(self.spec.disadvantage_sources)
        mode = 'normal' if adv == dis else ('advantage' if adv else 'disadvantage')
        if self.mode != mode or self.dice_count != (1 if mode == 'normal' else 2):
            raise ValueError('Niespójny tryb rzutu.')
        return self


class DiceInput(Model):
    values: tuple[Die, ...] = Field(min_length=1, max_length=2)


class CheckResult(Model):
    dice: tuple[Die, ...]
    selected: Die
    bonus: int
    total: int
    dc: int
    success: bool
    mode: Mode


class ActionDefinition(Model):
    id: Identifier
    label: Text
    kind: Literal['force_door', 'observe_scene']
    scene_id: Identifier
    door_id: Identifier | None = None
    check: CheckSpec | None = None
    elapsed_seconds: int = Field(default=0, strict=True, ge=0, le=86400)
    noise_events: int = Field(default=0, strict=True, ge=0, le=1)

    @model_validator(mode='after')
    def supported(self) -> Self:
        if self.kind == 'force_door' and (self.door_id is None or self.check is None):
            raise ValueError('Wyważenie drzwi wymaga celu oraz definicji testu.')
        if self.kind == 'observe_scene' and (self.door_id is not None or self.check is not None):
            raise ValueError('Oglądanie jawnego otoczenia nie wymaga testu ani drzwi.')
        return self


class ActionPack(Model):
    schema_version: Literal[1] = 1
    id: Identifier
    scenario_id: Identifier
    ruleset_id: Literal['dnd_2024_srd_5_2_1']
    actions: list[ActionDefinition] = Field(min_length=1)

    @model_validator(mode='after')
    def unique_ids(self) -> Self:
        ids = [a.id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError('ID działań muszą być unikalne.')
        return self
