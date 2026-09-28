from copy import deepcopy
from types import SimpleNamespace

import pytest

from chat_service import ChatError, ChatService, prepare_messages
from config import Settings
from prompts import SYSTEM_PROMPT


def test_mock_does_not_invoke_model():
    class ForbiddenModel:
        def invoke(self, messages):
            raise AssertionError("Nie wolno wywoływać modelu w trybie mock.")

    result = ChatService(Settings(), model=ForbiddenModel()).reply([
        {"role": "user", "content": "Oglądam drzwi."}
    ])
    assert "MOCK" in result
    assert "Oglądam drzwi." in result


def test_trim_keeps_complete_pairs_and_system_prompt():
    history = []
    for index in range(5):
        history += [
            {"role": "user", "content": f"pytanie {index}"},
            {"role": "assistant", "content": f"odpowiedź {index}"},
        ]
    history.append({"role": "user", "content": "nowe pytanie"})
    original = deepcopy(history)
    result = prepare_messages(history, Settings(max_history_turns=2))
    assert [message["role"] for message in result] == [
        "system", "user", "assistant", "user"
    ]
    assert result[0]["content"] == SYSTEM_PROMPT
    assert result[1]["content"] == "pytanie 4"
    assert history == original


def test_one_turn_context_contains_only_new_declaration():
    history = [
        {"role": "user", "content": "dawniej"},
        {"role": "assistant", "content": "dawna odpowiedź"},
        {"role": "user", "content": "teraz"},
    ]
    result = prepare_messages(history, Settings(max_history_turns=1))
    assert len(result) == 2
    assert result[-1]["content"] == "teraz"


@pytest.mark.parametrize("history", [
    [],
    [{"role": "user", "content": " "}],
    [{"role": "assistant", "content": "tekst"}],
    [{"role": "system", "content": "podmieniona instrukcja"}],
    [{"role": "user", "content": "tekst"}, {"role": "assistant", "content": "OK"}],
    [{"role": "user", "content": None}],
])
def test_invalid_history(history):
    with pytest.raises(ValueError):
        prepare_messages(history, Settings())


def test_input_limit():
    with pytest.raises(ValueError, match="limit"):
        prepare_messages([{"role": "user", "content": "za długi tekst"}],
                         Settings(max_input_chars=5))


def test_model_receives_history_and_returns_text():
    class Model:
        def invoke(self, messages):
            assert messages[0]["role"] == "system"
            assert messages[-1]["content"] == "Hej"
            return SimpleNamespace(text="  Odpowiedź.  ")

    settings = Settings(provider="openai", api_key="test-only")
    service = ChatService(settings, model=Model())
    assert service.reply([{"role": "user", "content": "Hej"}]) == "Odpowiedź."


def test_raw_api_error_not_exposed():
    class Model:
        def invoke(self, messages):
            raise RuntimeError("SECRET_VALUE / private API response")

    service = ChatService(Settings(provider="openai", api_key="test-only"), Model())
    with pytest.raises(ChatError) as exc:
        service.reply([{"role": "user", "content": "Hej"}])
    assert "SECRET_VALUE" not in str(exc.value)


def test_authentication_error_has_useful_message():
    AuthenticationError = type("AuthenticationError", (Exception,), {})

    class Model:
        def invoke(self, messages):
            raise AuthenticationError("SECRET_VALUE")

    service = ChatService(Settings(provider="openai", api_key="test-only"), Model())
    with pytest.raises(ChatError, match="OPENAI_API_KEY"):
        service.reply([{"role": "user", "content": "Hej"}])


@pytest.mark.parametrize("text", ["", " ", None, []])
def test_empty_or_invalid_model_reply(text):
    class Model:
        def invoke(self, messages):
            return SimpleNamespace(text=text)

    service = ChatService(Settings(provider="openai", api_key="test-only"), Model())
    with pytest.raises(ChatError, match="pustą"):
        service.reply([{"role": "user", "content": "Hej"}])
