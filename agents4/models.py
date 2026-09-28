"""Kontrakty agentów i UI. Żaden model nie zawiera arbitralnych zmian świata."""
from typing import Literal, Self
from typing_extensions import TypedDict
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Intent(BaseModel):
    """Interpretacja zamiaru, nie wynik testu i nie kod do wykonania."""
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['action', 'chat', 'clarify', 'unsupported']
    definition_id: str | None = Field(description='ID z katalogu, tylko dla action.')
    target_id: str | None = Field(description='ID celu z katalogu, tylko dla action.')
    message: str = Field(description='Jedno krótkie pytanie lub opis zamiaru, bez rozstrzygnięcia.')

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if not self.message.strip() or len(self.message) > 600:
            raise ValueError('Opis zamiaru musi mieć 1–600 znaków.')
        if self.kind == 'action':
            if not self.definition_id or not self.target_id:
                raise ValueError('Działanie wymaga identyfikatora i celu.')
        elif self.definition_id is not None or self.target_id is not None:
            raise ValueError('Tylko action może wskazywać działanie i cel.')
        return self


class RollDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    choice: Literal['manual', 'app', 'cancel']
    dice: list[int] | None = None

    @model_validator(mode='after')
    def valid_dice(self) -> Self:
        if self.choice == 'manual':
            if self.dice is None or not 1 <= len(self.dice) <= 2:
                raise ValueError('Podaj jedną lub dwie kości.')
            if any(type(d) is not int or not 1 <= d <= 20 for d in self.dice):
                raise ValueError('Kości muszą być liczbami całkowitymi od 1 do 20.')
        elif self.dice is not None:
            raise ValueError('Tylko rzut fizyczny przyjmuje wyniki kości.')
        return self


class NarrationDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    choice: Literal['retry', 'fallback']


class TurnState(TypedDict, total=False):
    campaign_id: str
    turn_id: str
    user_text: str
    context: dict
    history: list[dict]
    catalog: list[dict]
    base_revision: int
    intent: dict
    action: dict | None
    roll_decision: dict
    narration_decision: dict
    answer: str
    answer_revision: int
    narration_error: str | None
    finished: bool
