"""Czyste funkcje: żadnego LLM, I/O, zapisu bazy ani losowania.

Obsługujemy tylko podstawowe ability checks, nie ataki ani death saves.
Nie ma automatycznego sukcesu na 20 ani automatycznej porażki na 1.
"""
from collections.abc import Sequence
from schemas import CharacterState
from .models import CheckPlan, CheckResult, CheckSpec, DiceInput


def prepare_check(character: CharacterState, spec: CheckSpec) -> CheckPlan:
    if character.hp_current == 0:
        raise ValueError('Postać z 0 HP nie może wykonać tej próby w prototypie.')
    if character.conditions:
        raise ValueError('Ta wersja silnika nie obsługuje efektów stanów postaci. Nie pomijamy ich.')
    score = getattr(character.abilities, spec.ability)
    modifier = (score - 10) // 2
    proficiency = (character.proficiency_bonus
                   if spec.skill is not None and spec.skill in character.skill_proficiencies else 0)
    adv, dis = bool(spec.advantage_sources), bool(spec.disadvantage_sources)
    mode = 'normal' if adv == dis else ('advantage' if adv else 'disadvantage')
    return CheckPlan(actor_id=character.id, actor_name=character.name, spec=spec,
                     ability_score=score, ability_modifier=modifier,
                     proficiency_applied=proficiency, bonus=modifier + proficiency,
                     mode=mode, dice_count=1 if mode == 'normal' else 2)


def resolve_check(plan: CheckPlan, dice: Sequence[int]) -> CheckResult:
    values = DiceInput(values=tuple(dice)).values
    if len(values) != plan.dice_count:
        raise ValueError(f'Ten test wymaga dokładnie {plan.dice_count} kości k20.')
    if plan.mode == 'advantage':
        selected = max(values)
    elif plan.mode == 'disadvantage':
        selected = min(values)
    else:
        selected = values[0]
    total = selected + plan.bonus
    return CheckResult(dice=values, selected=selected, bonus=plan.bonus,
                       total=total, dc=plan.spec.dc,
                       success=total >= plan.spec.dc, mode=plan.mode)
