import pytest


@pytest.fixture(autouse=True)
def clean_app_environment(monkeypatch):
    for name in (
        "LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL", "MAX_HISTORY_TURNS",
        "MAX_INPUT_CHARS", "MAX_OUTPUT_TOKENS", "REQUEST_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
