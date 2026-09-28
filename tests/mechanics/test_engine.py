import pytest
from pydantic import ValidationError
from schemas import CharacterState
from mechanics3.engine import prepare_check, resolve_check
from mechanics3.models import CheckSpec, CheckPlan


def spec(**kwargs):
    return CheckSpec(**{'ability': 'strength', 'skill': 'athletics', 'dc': 15,
                       'dc_origin': 'test scenario', **kwargs})


@pytest.mark.parametrize('score', list(range(1, 31)))
def test_modifiers_for_all_scores(character, score):
    data = character.model_dump()
    data['abilities']['strength'] = score
    plan = prepare_check(CharacterState.model_validate(data), spec())
    assert plan.ability_modifier == (score - 10) // 2
    assert plan.bonus == plan.ability_modifier + 2


@pytest.mark.parametrize('die,success,total', [(1, False, 6), (9, False, 14), (10, True, 15), (20, True, 25)])
def test_boundary(character, die, success, total):
    result = resolve_check(prepare_check(character, spec()), [die])
    assert result.total == total and result.success == success


def test_one_not_automatic_failure(character):
    assert resolve_check(prepare_check(character, spec(dc=5)), [1]).success


def test_twenty_not_automatic_success(character):
    assert not resolve_check(prepare_check(character, spec(dc=30)), [20]).success


@pytest.mark.parametrize('skill,bonus', [(None, 3), ('arcana', 3), ('athletics', 5)])
def test_proficiency_is_conditional(character, skill, bonus):
    assert prepare_check(character, spec(skill=skill)).bonus == bonus


@pytest.mark.parametrize('adv,dis,mode,values,selected', [
    ([], [], 'normal', [12], 12),
    (['a'], [], 'advantage', [4, 16], 16),
    (['a', 'b'], [], 'advantage', [16, 4], 16),
    ([], ['x', 'y'], 'disadvantage', [16, 4], 4),
    (['a', 'b'], ['x'], 'normal', [11], 11),
])
def test_advantage(character, adv, dis, mode, values, selected):
    plan = prepare_check(character, spec(advantage_sources=adv, disadvantage_sources=dis))
    result = resolve_check(plan, values)
    assert result.mode == mode and result.selected == selected


@pytest.mark.parametrize('values', [[0], [21], [True], ['10'], [10.5], [], [1, 2], [1, 2, 3]])
def test_invalid_dice_rejected(character, values):
    with pytest.raises(ValueError):
        resolve_check(prepare_check(character, spec()), values)


def test_advantage_requires_two_dice(character):
    with pytest.raises(ValueError):
        resolve_check(prepare_check(character, spec(advantage_sources=['a'])), [10])


@pytest.mark.parametrize('updates', [{'hp_current': 0}, {'conditions': ['poisoned']}])
def test_unsupported_actor_state(character, updates):
    with pytest.raises(ValueError):
        prepare_check(CharacterState.model_validate({**character.model_dump(), **updates}), spec())


@pytest.mark.parametrize('changes', [{'bonus': 999}, {'ability_modifier': 100}, {'mode': 'advantage'}, {'dice_count': 2}])
def test_inconsistent_plan_rejected(character, changes):
    plan = prepare_check(character, spec())
    with pytest.raises(ValidationError):
        CheckPlan.model_validate({**plan.model_dump(), **changes})
