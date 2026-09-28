import pytest

from config import Settings, load_settings


def test_default_mock_without_key(tmp_path):
    settings = load_settings(tmp_path / "missing.env")
    assert settings.provider == "mock"
    assert settings.api_key == ""


def test_env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("LLM_PROVIDER=mock\nMAX_HISTORY_TURNS=4\n", encoding="utf-8")
    assert load_settings(path).max_history_turns == 4


def test_environment_has_priority(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("MAX_HISTORY_TURNS=4\n", encoding="utf-8")
    monkeypatch.setenv("MAX_HISTORY_TURNS", "2")
    assert load_settings(path).max_history_turns == 2


def test_openai_requires_key():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        Settings(provider="openai")


def test_secret_not_in_repr():
    assert "SECRET_VALUE" not in repr(Settings(api_key="SECRET_VALUE"))


def test_unknown_provider():
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        Settings(provider="invalid")


def test_model_must_not_be_empty():
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        Settings(model="")


@pytest.mark.parametrize("kwargs", [
    {"max_history_turns": 0}, {"max_history_turns": 51},
    {"max_input_chars": 0}, {"max_output_tokens": 0}, {"request_timeout": 0},
])
def test_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_non_numeric_value(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_HISTORY_TURNS", "SECRET_INVALID_VALUE")
    with pytest.raises(ValueError) as exc:
        load_settings(tmp_path / "missing.env")
    assert "liczbą całkowitą" in str(exc.value)
    assert "SECRET_INVALID_VALUE" not in str(exc.value)
