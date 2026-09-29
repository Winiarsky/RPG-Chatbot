"""Data contracts. Source coordinates are assigned by importers, never by an LLM."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

RULESET = "dnd_2024_srd_5_2_1"

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Source(StrictModel):
    document_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
    title: str = Field(min_length=1, max_length=300)
    ruleset_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=80)
    language: str = Field(default="en", pattern=r"^[a-z]{2,3}$")
    kind: Literal["rules"] = "rules"
    source_url: str = Field(default="", max_length=1500)
    license: str = Field(min_length=1, max_length=200)
    attribution: str = Field(min_length=1, max_length=2000)
    coverage: str = Field(min_length=1, max_length=2000)

    @field_validator("source_url")
    @classmethod
    def http_only(cls, value):
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("source_url musi być adresem HTTP(S) lub pustym tekstem.")
        return value

class Section(StrictModel):
    key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=20, max_length=500_000)
    pdf_page_start: int | None = Field(default=None, ge=1)
    pdf_page_end: int | None = Field(default=None, ge=1)
    printed_page: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def pages(self):
        a, b = self.pdf_page_start, self.pdf_page_end
        if (a is None) != (b is None) or (a is not None and b < a):
            raise ValueError("Podaj obie strony PDF w poprawnej kolejności albo obie null.")
        return self

class Manifest(StrictModel):
    source: Source
    file: str = Field(min_length=1, max_length=500)
    format: Literal["sections_json", "markdown", "pdf"]
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

class Chunk(StrictModel):
    chunk_id: str
    source: Source
    section_key: str
    section: str
    text: str
    file_sha256: str
    revision: str
    pdf_page_start: int | None = None
    pdf_page_end: int | None = None
    printed_page: str | None = None
    part: int
    reviewed: bool = False

class Hit(StrictModel):
    chunk: Chunk
    score: float
    lexical_rank: int | None = None
    semantic_rank: int | None = None

class EvidenceRef(StrictModel):
    chunk_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=12, max_length=1200)

class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=1600)
    basis: Literal["rule", "inference"]
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=4)

class AnswerDraft(StrictModel):
    status: Literal["supported", "insufficient", "conflicting"]
    claims: list[Claim] = Field(max_length=10)
    unresolved_questions: list[str] = Field(max_length=10)
    search_query: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def coherent(self):
        if self.status == "supported" and (not self.claims or self.unresolved_questions):
            raise ValueError("supported wymaga twierdzeń i braku nierozwiązanych części pytania.")
        if self.status != "supported" and not self.unresolved_questions:
            raise ValueError("Podaj brakujące informacje lub charakter sprzeczności.")
        if self.status == "conflicting" and len({e.chunk_id for c in self.claims for e in c.evidence}) < 2:
            raise ValueError("conflicting wymaga co najmniej dwóch fragmentów dowodowych.")
        if self.status != "insufficient" and self.search_query:
            raise ValueError("Przeformułowanie jest dostępne tylko dla insufficient.")
        return self

class Citation(StrictModel):
    number: int
    chunk: Chunk

class LibraryAnswer(StrictModel):
    question: str
    ruleset_id: str
    status: Literal["supported", "insufficient", "conflicting", "retrieved_only"]
    claims: list[Claim]
    citations: list[Citation]
    retrieved: list[Hit]
    unresolved_questions: list[str]
    queries: list[str]
    corpus_revision: str
    generation: Literal["none", "llm"]
    # Identity and literal support spans are checked. Entailment is NOT proven.
    citation_checks: str = "Sprawdzono ID i obecność cytatu; nie jest to dowód poprawności interpretacji."
