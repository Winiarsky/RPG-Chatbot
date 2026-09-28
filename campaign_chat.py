"""Adapter wiadomości systemowej współdzielony przez role agentów."""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextualModel:
    """Dekorator: wymienia wiadomość systemową tuż przed model.invoke()."""
    model: Any
    system_prompt: str

    def invoke(self, messages: list[dict[str, str]]) -> Any:
        # Nie modyfikujemy współdzielonej historii ani instancji modelu.
        if not messages or messages[0]["role"] != "system":
            raise ValueError("Adapter oczekuje wiadomości systemowej na początku.")
        enriched = [
            {"role": "system", "content": self.system_prompt},
            *[dict(message) for message in messages[1:]],
        ]
        return self.model.invoke(enriched)
