"""Kontrakty kierowania do biblioteki. Nie przyjmują HP, DC ani premii od modelu."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from agents.models import Intent
from improvisation.models import RecoveryPlan, RecoveryTurnState

MAX_RULE_QUERY_CHARS = 1200


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class RuleNeed(Model):
    purpose: Literal['question', 'action']
    query: str = Field(min_length=3, max_length=MAX_RULE_QUERY_CHARS,
        description='Samodzielne pytanie o regułę, bez fabuły, historii i sekretów.')
    required_capabilities: list[str] = Field(max_length=8,
        description='Dla akcji: wszystkie wymagane możliwości silnika. Dla pytania: pusta lista.')

    @model_validator(mode='after')
    def coherent(self):
        if not self.query.strip():
            raise ValueError('Pytanie nie może być puste.')
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError('Powtórzone wymagania.')
        if any(not x or len(x) > 80 for x in self.required_capabilities):
            raise ValueError('Nieprawidłowa nazwa możliwości silnika.')
        if self.purpose == 'question' and self.required_capabilities:
            raise ValueError('Pytanie nie uruchamia mechaniki.')
        if self.purpose == 'action' and not self.required_capabilities:
            raise ValueError('Konsultowane działanie musi jawnie wskazywać wymagania.')
        return self


class GMDecision(Model):
    intent: Intent
    rules: RuleNeed | None

    @model_validator(mode='after')
    def coherent(self):
        if self.rules and self.rules.purpose == 'question' and self.intent.kind == 'action':
            raise ValueError('Pytania o zasady nie są zgodą na wykonanie akcji.')
        return self


class RecoveryDecision(Model):
    recovery: RecoveryPlan
    rules: RuleNeed | None


class RulesRetry(Model):
    choice: Literal['retry', 'stop']


class RulesTurnState(RecoveryTurnState, total=False):
    rules_need: dict | None
    rules_origin: str | None
    rules_record: dict | None
    rules_error: str | None
    rules_decision: dict
    rules_attempted: bool
    rules_block: str | None
    rules_after: str | None
    force_rules_only: bool
    rules_rendered: bool
