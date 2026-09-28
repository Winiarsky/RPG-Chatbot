"""Konfiguracja bez zależności od interfejsu i dostawcy modelu."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    provider: str = "mock"
    model: str = "gpt-4.1-mini"
    api_key: str = field(default="", repr=False)
    max_history_turns: int = 12
    max_input_chars: int = 4000
    max_output_tokens: int = 800
    request_timeout: int = 45

    def __post_init__(self) -> None:
        if self.provider not in {"mock", "openai"}:
            raise ValueError("LLM_PROVIDER musi mieć wartość mock albo openai.")
        if self.provider == "openai" and not self.api_key:
            raise ValueError("Ustaw OPENAI_API_KEY w .env lub środowisku.")
        if not self.model.strip():
            raise ValueError("OPENAI_MODEL nie może być puste.")
        for name, value, minimum, maximum in (
            ("MAX_HISTORY_TURNS", self.max_history_turns, 1, 50),
            ("MAX_INPUT_CHARS", self.max_input_chars, 1, 10000),
            ("MAX_OUTPUT_TOKENS", self.max_output_tokens, 64, 8192),
            ("REQUEST_TIMEOUT_SECONDS", self.request_timeout, 1, 180),
        ):
            if not minimum <= value <= maximum:
                raise ValueError(f"{name}: dozwolony zakres {minimum}–{maximum}.")


def load_settings(env_path: Path | None = None) -> Settings:
    # Zmienne systemowe mają pierwszeństwo. Nie modyfikujemy os.environ.
    values = {
        **dotenv_values(env_path if env_path is not None else ROOT / ".env"),
        **os.environ,
    }

    def text(name: str, default: str) -> str:
        value = values.get(name)
        return default if value is None else str(value).strip()

    def number(name: str, default: int) -> int:
        try:
            return int(text(name, str(default)))
        except ValueError:
            raise ValueError(f"{name} musi być liczbą całkowitą.") from None

    return Settings(
        provider=text("LLM_PROVIDER", "mock").lower(),
        model=text("OPENAI_MODEL", "gpt-4.1-mini"),
        api_key=text("OPENAI_API_KEY", ""),
        max_history_turns=number("MAX_HISTORY_TURNS", 12),
        max_input_chars=number("MAX_INPUT_CHARS", 4000),
        max_output_tokens=number("MAX_OUTPUT_TOKENS", 800),
        request_timeout=number("REQUEST_TIMEOUT_SECONDS", 45),
    )
