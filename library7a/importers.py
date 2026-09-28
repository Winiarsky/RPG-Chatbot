"""Local-file ingestion. No crawler, OCR, pickle, remote execution, or game files."""
import hashlib
import json
import re
from io import BytesIO
from pathlib import Path
import yaml
from .models import Manifest, Section, Chunk

MAX_FILE_BYTES = 50 * 1024 * 1024
CHUNK_CHARS = 3500
CHUNKER_VERSION = "paragraphs-v2"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(value) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def read_limited(path: Path, limit=MAX_FILE_BYTES) -> bytes:
    with path.open("rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Za duży plik {path.name}; limit {limit} bajtów.")
    return data


def paragraphs(text: str, limit=CHUNK_CHARS):
    """Keep paragraphs together; oversize ones use overlapping word windows."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    pending = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) > limit:
            if pending:
                yield pending
                pending = ""
            words, buf = para.split(), ""
            for word in words:
                if len(word) > limit:
                    raise ValueError("Fragment zawiera zbyt długi token; sprawdź ekstrakcję.")
                if buf and len(buf) + len(word) + 1 > limit:
                    yield buf
                    # Small overlap, bounded even with pathological input.
                    overlap = " ".join(buf.split()[-25:])
                    buf = overlap if len(overlap) + len(word) + 1 <= limit else ""
                buf = (buf + " " + word).strip()
            if buf:
                yield buf
        elif pending and len(pending) + len(para) + 2 > limit:
            yield pending
            pending = para
        else:
            pending = (pending + "\n\n" + para).strip()
    if pending:
        yield pending


def markdown_sections(text: str):
    sections, lines, heading, num = [], [], "Wstęp", 0
    def emit():
        nonlocal num
        body = "\n".join(lines).strip()
        if len(body) >= 20:
            num += 1
            sections.append(Section(key=f"md-{num:04d}", title=heading, text=body))
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*$", line)
        if match:
            emit()
            lines = []
            heading = match.group(1).strip()
        else:
            lines.append(line)
    emit()
    return sections


def pdf_sections(raw: bytes):
    from pypdf import PdfReader
    # Extract from the same bytes whose digest identifies this revision.
    reader = PdfReader(BytesIO(raw))
    if reader.is_encrypted:
        raise ValueError("PDF jest zaszyfrowany; użyj jawnego, dostępnego legalnie pliku.")
    if not 1 <= len(reader.pages) <= 1000:
        raise ValueError("PDF: obsługujemy 1–1000 stron.")
    sections, skipped = [], []
    for number, page in enumerate(reader.pages, 1):
        # Pagewise text, deliberately not advertised as semantic PDF section parsing.
        text = (page.extract_text() or "").replace("\x00", "").strip()
        if len(text) < 30:
            skipped.append(number)
            continue
        sections.append(Section(key=f"pdf-{number:04d}", title=f"PDF / strona {number}",
                                text=text, pdf_page_start=number, pdf_page_end=number))
    if not sections:
        raise ValueError("PDF nie zawiera użytecznego tekstu. OCR nie jest włączony.")
    return sections, skipped


def load_manifest(path: Path):
    path = path.resolve()
    data = yaml.safe_load(read_limited(path, 200_000).decode("utf-8"))
    manifest = Manifest.model_validate(data)
    target = (path.parent / manifest.file).resolve()
    if not target.is_relative_to(path.parent):
        raise ValueError("Plik materiału musi leżeć pod katalogiem manifestu (bez ../ i symlinków na zewnątrz).")
    raw = read_limited(target)
    file_hash = digest(raw)
    if manifest.sha256 and manifest.sha256 != file_hash:
        raise ValueError("SHA-256 materiału nie zgadza się z manifestem.")
    warnings = []
    if manifest.format == "sections_json":
        items = json.loads(raw)
        if not isinstance(items, list):
            raise ValueError("sections_json musi być listą sekcji.")
        sections = [Section.model_validate(item) for item in items]
    elif manifest.format == "markdown":
        sections = markdown_sections(raw.decode("utf-8-sig"))
    else:
        sections, skipped = pdf_sections(raw)
        warnings.append("PDF: podział po stronach, kolejność kolumn/tabele wymagają ręcznej kontroli.")
        if skipped:
            warnings.append(f"Pominięto strony bez użytecznego tekstu: {skipped}")
    if not sections or len(sections) > 10000:
        raise ValueError("Nie znaleziono sekcji albo materiał przekracza limit 10000 sekcji.")
    if len({s.key for s in sections}) != len(sections):
        raise ValueError("Powtórzone identyfikatory sekcji.")
    revision = canonical_hash({"source": manifest.source.model_dump(), "file": file_hash,
                              "chunker": CHUNKER_VERSION, "format": manifest.format})
    chunks = []
    for section in sections:
        for i, text in enumerate(paragraphs(section.text), 1):
            cid = "c_" + canonical_hash([manifest.source.document_id, revision, section.key, i, text])[:32]
            chunks.append(Chunk(chunk_id=cid, source=manifest.source, section_key=section.key,
                section=section.title, text=text, file_sha256=file_hash, revision=revision,
                pdf_page_start=section.pdf_page_start, pdf_page_end=section.pdf_page_end,
                printed_page=section.printed_page, part=i))
    if len(chunks) > 20000:
        raise ValueError("Za dużo fragmentów dla lokalnego prototypu (limit 20000).")
    return manifest, chunks, warnings
