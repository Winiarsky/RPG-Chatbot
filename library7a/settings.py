"""Independent configuration; offline commands do not initialize ChatService."""
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import dotenv_values
from .models import RULESET

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class LibrarySettings:
    db_path: Path
    ruleset_id: str = RULESET
    top_k: int = 6
    context_chars: int = 16000
    embedding_model: str = "text-embedding-3-small"
    dimensions: int = 512
    output_mode: str = "function_calling"
    output_tokens: int = 4096

    def __post_init__(self):
        if not self.ruleset_id.strip():
            raise ValueError("Brak RAG7_RULESET_ID.")
        if not 1 <= self.top_k <= 12 or not 4000 <= self.context_chars <= 50000:
            raise ValueError("Niepoprawny limit kontekstu lub top_k.")
        if not 64 <= self.dimensions <= 3072:
            raise ValueError("RAG7_EMBEDDING_DIMENSIONS: zakres 64–3072.")
        if self.output_mode not in {"function_calling", "json_text"}:
            raise ValueError("RAG7_OUTPUT_MODE: function_calling lub json_text.")
        if not 512 <= self.output_tokens <= 8192:
            raise ValueError("RAG7_OUTPUT_TOKENS: zakres 512–8192.")

    @property
    def embedding_spec(self):
        return {"provider": "openai", "model": self.embedding_model,
                "dimensions": self.dimensions, "input_format": "section-text-v1",
                "base_url": "https://api.openai.com/v1"}

def load_library_settings():
    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    def text(key, default):
        v = values.get(key)
        return str(default if v is None else v).strip()
    def number(key, default):
        try:
            return int(text(key, default))
        except ValueError:
            raise ValueError(f"{key} musi być liczbą całkowitą.") from None
    p = Path(text("RAG7_DB_PATH", "data/library7a.sqlite3")).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    return LibrarySettings(db_path=p.resolve(),
        ruleset_id=text("RAG7_RULESET_ID", RULESET),
        top_k=number("RAG7_TOP_K", 6), context_chars=number("RAG7_CONTEXT_CHARS", 16000),
        embedding_model=text("RAG7_EMBEDDING_MODEL", "text-embedding-3-small"),
        dimensions=number("RAG7_EMBEDDING_DIMENSIONS", 512),
        output_mode=text("RAG7_OUTPUT_MODE", "function_calling"),
        output_tokens=number("RAG7_OUTPUT_TOKENS", 4096))
