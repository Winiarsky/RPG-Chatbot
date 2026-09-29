import json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from config import Settings
from chat_service import ChatService, ChatError
from agents.models import Intent, RollDecision
from agents.roles import GameMaster, Narrator, parse_intent
from agents.runtime import validate_resume, GraphController

CATALOG = [{'definition_id': 'force_door', 'target_id': 'tower_door', 'label': 'Wyważ drzwi'},
           {'definition_id': 'observe_scene', 'target_id': 'tower_entrance', 'label': 'Rozejrzyj się'}]
DATA = {'kind': 'action', 'definition_id': 'force_door', 'target_id': 'tower_door', 'message': 'Wyważ drzwi.'}


@pytest.mark.parametrize('text,kind', [
    ('Wyważam drzwi', 'action'), ('Próbuję wyważyć drzwi.', 'action'), ('Rozglądam się', 'action'),
    ('Ile mam HP?', 'chat'), ('Czy mogę wyważyć drzwi?', 'chat'), ('Otwieram drzwi', 'clarify'),
    ('Wyważam drzwi i atakuję goblina', 'clarify'), ('Nie wyważam drzwi', 'clarify'),
    ('Atakuję goblina', 'unsupported'), ('Rzucam Fireball', 'unsupported'),
])
def test_mock_is_explicit_and_conservative(text, kind):
    gm = GameMaster(ChatService(Settings(provider='mock')))
    assert gm.interpret(text, [], {}, CATALOG).kind == kind


@pytest.mark.parametrize('patch', [
    {'dc': 1}, {'dice': [20]}, {'kind': 'teleport'}, {'target_id': None},
    {'message': ''}, {'message': 'x'*601}, {'kind': 'chat'},
])
def test_invalid_intent_rejected(patch):
    with pytest.raises(ChatError):
        parse_intent(json.dumps({**DATA, **patch}))


@pytest.mark.parametrize('text', ['[]', 'null', 'abc {"kind":"chat"}', '{', '```python\nprint(1)\n```'])
def test_not_arbitrary_json_extraction(text):
    with pytest.raises(ChatError):
        parse_intent(text)


def test_single_json_fence():
    assert parse_intent('```json\n' + json.dumps(DATA) + '\n```').definition_id == 'force_door'


@pytest.mark.parametrize('values', [[], [0], [21], [True], ['10'], [10.0], [1,2,3], None])
def test_bad_dice(values):
    with pytest.raises(ValidationError):
        RollDecision(choice='manual', dice=values)


@pytest.mark.parametrize('choice', ['app', 'cancel'])
def test_non_manual_cannot_supply_dice(choice):
    with pytest.raises(ValidationError):
        RollDecision(choice=choice, dice=[20])


def test_resume_dice_count_and_kind():
    payload = {'kind': 'roll', 'plan': {'dice_count': 2}}
    with pytest.raises(ValueError):
        validate_resume(payload, {'choice': 'manual', 'dice': [10]})
    assert validate_resume(payload, {'choice': 'manual', 'dice': [10,12]})['dice'] == [10,12]
    with pytest.raises(ValidationError):
        validate_resume({'kind': 'narration_retry'}, {'choice': 'app'})
    assert validate_resume({'kind': 'narration_retry'}, {'choice': 'fallback'}) == {'choice': 'fallback'}


def test_thread_ids_separate_campaigns():
    assert GraphController.config('a','one') != GraphController.config('b','one')


class FakeModel:
    def __init__(self):
        self.messages = []
        self.schemas = []

    def with_structured_output(self, schema, **kwargs):
        self.schemas.append((schema, kwargs))
        outer = self
        class Result:
            def invoke(self, messages):
                outer.messages = messages
                return Intent(**DATA)
        return Result()

    def invoke(self, messages):
        self.messages = messages
        return SimpleNamespace(text=json.dumps(DATA))


@pytest.mark.parametrize('mode', ['function_calling', 'json_text'])
def test_modes_use_existing_model_without_network(mode):
    model = FakeModel()
    transport = ChatService(Settings(provider='openai', api_key='TEST_ONLY'), model=model)
    result = GameMaster(transport, mode).interpret('Wyważam drzwi', [], {'scene': 'public'}, CATALOG)
    assert result.kind == 'action'
    assert model.messages[0]['role'] == 'system'
    assert 'ACTION_CATALOG' in model.messages[0]['content']
    assert 'TEST_ONLY' not in json.dumps(model.messages)
    if mode == 'function_calling':
        assert model.schemas == [(Intent, {'method':'function_calling'})]
    else:
        assert model.schemas == []


def test_missing_structured_method_explained():
    model = SimpleNamespace(invoke=lambda _: SimpleNamespace(text='{}'))
    transport = ChatService(Settings(provider='openai', api_key='TEST_ONLY'), model=model)
    with pytest.raises(ChatError, match='json_text'):
        GameMaster(transport, 'function_calling').interpret('x', [], {}, [])
