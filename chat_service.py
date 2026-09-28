"""Warstwa rozmowy. Nie importuje Streamlit i nie przechowuje sesji."""

from collections.abc import Sequence
import logging
import re
import traceback
from typing import Any, Literal, Protocol, TypedDict

from config import Settings
from prompts import SYSTEM_PROMPT

LOGGER = logging.getLogger(__name__)


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class InvokableModel(Protocol):
    def invoke(self, messages: list[dict[str, str]]) -> Any: ...


class ChatError(RuntimeError):
    """Komunikat błędu bez surowej odpowiedzi API i bez kluczy."""


def redact_diagnostic(text: str, api_key: str = "") -> str:
    """Maskuje znany klucz i typowe tokeny. Raport nadal wymaga przeglądu."""
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    text = re.sub(r"\bsk-[A-Za-z0-9_*.-]+", "[REDACTED]", text)
    text = re.sub(
        r"(?i)\bBearer\s+[^\s'\"<>]+", "Bearer [REDACTED]", text
    )
    # Np. niestandardowe endpointy z hasłem w URL proxy.
    text = re.sub(
        r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text
    )
    return text


def model_error(exc: Exception, settings: Settings) -> ChatError:
    """Pełniejsza diagnoza w terminalu; tylko krótki komunikat w UI."""
    error_type = type(exc).__name__
    hints = {
        "AuthenticationError": "Sprawdź klucz OPENAI_API_KEY.",
        "PermissionDeniedError": "Sprawdź uprawnienia klucza i dostęp do modelu.",
        "NotFoundError": "Sprawdź nazwę modelu i adres API.",
        "RateLimitError": "Sprawdź limity i rozliczenia API.",
        "APITimeoutError": "Przekroczono czas oczekiwania na API.",
        "APIConnectionError": "Sprawdź połączenie, proxy i certyfikaty TLS.",
        "BadRequestError": "API odrzuciło parametry żądania.",
        "UnprocessableEntityError": "API nie mogło przetworzyć żądania.",
        "InternalServerError": "Serwer API zwrócił błąd 5xx.",
        "APIStatusError": "API zwróciło status błędu.",
        "ModuleNotFoundError": "Brakuje importowanego modułu. Szczegóły w terminalu.",
        "ImportError": "Nie udał się import biblioteki. Szczegóły w terminalu.",
        "TypeError": "Błąd typu lub argumentów wywołania. Szczegóły w terminalu.",
        "AttributeError": "Brak oczekiwanego atrybutu. Szczegóły w terminalu.",
        "ValueError": "Nieprawidłowa wartość lub konfiguracja. Szczegóły w terminalu.",
    }
    # Rozpoznaj też podklasy zamiast wyłącznie dokładnej nazwy wyjątku.
    hint = next(
        (hints[cls.__name__] for cls in type(exc).__mro__ if cls.__name__ in hints),
        "Nie udało się uzyskać odpowiedzi modelu.",
    )
    metadata = [f"Typ: {error_type}"]
    for label, attribute in (
        ("HTTP", "status_code"), ("Kod API", "code"), ("Request ID", "request_id")
    ):
        value = getattr(exc, attribute, None)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and value != "":
            metadata.append(f"{label}: {str(value)[:200]}")

    # Zbieramy stos bez zmiennych lokalnych i bez linijek kodu źródłowego.
    # Nie wypisujemy obiektów request/response, nagłówków ani historii czatu.
    lines = [
        "=== RPG_DM_DIAGNOSTICS ===",
        f"Provider: {settings.provider}; model: {settings.model}",
        *metadata,
    ]
    current: BaseException | None = exc
    seen: set[int] = set()
    for _ in range(5):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        lines.append(f"{type(current).__module__}.{type(current).__name__}:")
        # Zamaskuj przed skróceniem, aby nie pozostawić fragmentu klucza.
        lines.append(redact_diagnostic(str(current), settings.api_key)[:4000])
        for frame in traceback.extract_tb(current.__traceback__)[-12:]:
            lines.append(f"  {frame.filename}:{frame.lineno} w {frame.name}")
        cause = current.__cause__
        if cause is None and not current.__suppress_context__:
            cause = current.__context__
        current = cause
    lines.append("=== KONIEC DIAGNOSTYKI ===")
    LOGGER.error("%s", redact_diagnostic("\n".join(lines), settings.api_key))
    summary = (
        hint + "\n\n" + " | ".join(metadata)
        + "\nSzczegóły: terminal, w którym uruchomiono Streamlit."
    )
    return ChatError(redact_diagnostic(summary, settings.api_key))


def prepare_messages(
    history: Sequence[ChatMessage], settings: Settings
) -> list[dict[str, str]]:
    # Historia to pełne pary user/assistant oraz nowa deklaracja user.
    if not history or len(history) % 2 != 1:
        raise ValueError("Historia musi kończyć się nową wiadomością gracza.")
    for index, message in enumerate(history):
        expected = "user" if index % 2 == 0 else "assistant"
        content = message.get("content")
        if message.get("role") != expected:
            raise ValueError("Nieprawidłowa kolejność ról w historii.")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Wiadomość nie może być pusta.")
        if expected == "user" and len(content) > settings.max_input_chars:
            raise ValueError("Wiadomość gracza przekracza limit długości.")

    # Nie odcinamy wiadomości w połowie i zawsze zaczynamy od user.
    recent = history[-(2 * settings.max_history_turns - 1):]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *[{"role": item["role"], "content": item["content"]} for item in recent],
    ]


class ChatService:
    def __init__(
        self, settings: Settings, model: InvokableModel | None = None
    ) -> None:
        self.settings = settings
        self.model = model
        if settings.provider == "openai" and self.model is None:
            # Import dopiero w trybie API: logikę mock testujemy niezależnie.
            from langchain_openai import ChatOpenAI

            self.model = ChatOpenAI(
                model=settings.model,
                api_key=settings.api_key,
                base_url="https://api.openai.com/v1",
                timeout=settings.request_timeout,
                max_retries=1,
                max_tokens=settings.max_output_tokens,
                use_responses_api=True,
            )

    def reply(self, history: Sequence[ChatMessage]) -> str:
        messages = prepare_messages(history, self.settings)
        if self.settings.provider == "mock":
            turns = sum(item["role"] == "user" for item in messages)
            return (
                "**MOCK — bez wywołania API.**\n\n"
                f"Liczba deklaracji gracza w kontekście: {turns}. "
                "To odpowiedź testowa, nie narracja ani rozstrzygnięcie.\n\n"
                f"Ostatnia deklaracja: {history[-1]['content']}"
            )

        if self.model is None:
            raise ChatError("Model nie został skonfigurowany.")
        try:
            result = self.model.invoke(messages)
        except Exception as exc:
            raise model_error(exc, self.settings) from None

        # AIMessage.text w LangChain 1.x zwraca tekst odpowiedzi.
        text = getattr(result, "text", "")
        if not isinstance(text, str) or not text.strip():
            raise ChatError("Model zwrócił pustą odpowiedź tekstową.")
        return text.strip()
