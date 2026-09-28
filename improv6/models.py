"""Modele nie przyjmują od LLM arbitralnych zmian stanu ani kodu."""
from typing import Literal, Self
from pydantic import BaseModel, ConfigDict, Field, model_validator
from schemas import Identifier, Model, Text
from story5.models import StoryTurnState


class SignalRule(Model):
    id: Identifier
    label: Text
    scene_id: Identifier
    door_id: Identifier
    inside_scene_id: Identifier
    responder_id: Identifier | None = None
    response_mode: Literal['open', 'answer', 'silent'] = 'silent'
    can_hear: bool = False
    can_open_from_inside: bool = False
    max_noise_to_open: int | None = Field(default=None, ge=0, strict=True)
    first_reply: Text
    repeat_reply: Text
    cautious_reply: Text
    silent_text: Text
    elapsed_seconds: int = Field(default=6, ge=0, le=60, strict=True)
    # Tylko opis sygnału, bez sekretów i bez obietnicy odpowiedzi.
    public_hint: Text

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if self.response_mode != 'silent' and not self.responder_id:
            raise ValueError('Odpowiedź wymaga konkretnego NPC scenariusza.')
        if self.response_mode == 'open' and not self.can_open_from_inside:
            raise ValueError('Otwarcie wymaga jawnego uprawnienia w scenariuszu.')
        if self.scene_id == self.inside_scene_id:
            raise ValueError('Kontakt przez drzwi wymaga dwóch różnych scen.')
        return self


class ImprovisationProfile(Model):
    version: Literal[1] = 1
    id: Identifier
    scenario_id: Identifier
    signals: list[SignalRule] = Field(default_factory=list)

    @model_validator(mode='after')
    def unique(self) -> Self:
        ids = [s.id for s in self.signals]
        if len(ids) != len(set(ids)):
            raise ValueError('Powtórzone identyfikatory interakcji.')
        return self


class RecoveryPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    mode: Literal['map_action', 'interaction', 'narrate', 'clarify', 'defer']
    definition_id: str | None = Field(description='Istniejący identyfikator operacji; null bez działania.')
    target_id: str | None = Field(description='Istniejący cel operacji; null bez działania.')
    message: str = Field(min_length=1, max_length=600,
        description='Krótka parafraza zamiaru, kosmetyczny gest albo pytanie. Nigdy wynik działania.')
    alternative_ids: list[str] = Field(max_length=3,
        description='Do trzech istniejących działań proponowanych do wyboru. Nie wykonywać automatycznie.')

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if not self.message.strip():
            raise ValueError('Pusta decyzja improwizatora.')
        executable = self.mode in {'map_action', 'interaction'}
        if executable != bool(self.definition_id and self.target_id):
            raise ValueError('Działanie wymaga obu identyfikatorów.')
        if not executable and (self.definition_id is not None or self.target_id is not None):
            raise ValueError('Narracja i pytanie nie wykonują operacji.')
        if len(set(self.alternative_ids)) != len(self.alternative_ids):
            raise ValueError('Powtórzone opcje.')
        return self


class RecoveryTurnState(StoryTurnState, total=False):
    original_intent: dict
    recovery: dict
    recovery_attempted: bool
    recovery_reason: str | None
    flavor_brief: str | None
