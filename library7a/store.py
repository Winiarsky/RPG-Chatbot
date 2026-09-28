"""Dedicated library database. Refuses to initialize over a campaign database."""
import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .models import Chunk
from .importers import canonical_hash

SCHEMA_VERSION = "1"

class LibraryStore:
    def __init__(self, path: Path, create=False):
        self.path = Path(path).resolve()
        if not self.path.exists() and not create:
            raise ValueError("Brak bazy biblioteki. Uruchom: python manage_library.py init")
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "lib7_meta" not in tables:
                raise ValueError("To nie baza biblioteki. Nie ustawiaj RAG7_DB_PATH na bazę kampanii.")
            if not tables:
                if not create:
                    raise ValueError("Niezainicjalizowana biblioteka.")
                con.executescript('''
                CREATE TABLE lib7_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE lib7_documents(
                    document_id TEXT PRIMARY KEY, revision TEXT NOT NULL, source_json TEXT NOT NULL,
                    file_sha256 TEXT NOT NULL, reviewed INTEGER NOT NULL CHECK(reviewed IN(0,1)));
                CREATE TABLE lib7_chunks(
                    chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES lib7_documents(document_id) ON DELETE CASCADE,
                    data_json TEXT NOT NULL);
                CREATE TABLE lib7_vectors(
                    chunk_id TEXT NOT NULL REFERENCES lib7_chunks(chunk_id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL, vector_json TEXT NOT NULL,
                    PRIMARY KEY(chunk_id, fingerprint));
                ''')
                con.execute("INSERT INTO lib7_meta VALUES ('version',?)", (SCHEMA_VERSION,))
            version = con.execute("SELECT value FROM lib7_meta WHERE key='version'").fetchone()
            if not version or version[0] != SCHEMA_VERSION:
                raise ValueError("Nieobsługiwana wersja bazy biblioteki.")

    @contextmanager
    def connection(self):
        con = sqlite3.connect(str(self.path), timeout=20)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def import_chunks(self, chunks: list[Chunk], reviewed=False, replace=False):
        if not chunks:
            raise ValueError("Nie można importować pustego dokumentu.")
        head = chunks[0]
        if any(c.source != head.source or c.revision != head.revision for c in chunks):
            raise ValueError("Import musi dotyczyć jednej wersji jednego dokumentu.")
        if len({c.chunk_id for c in chunks}) != len(chunks):
            raise ValueError("Powtórzone ID fragmentów.")
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT revision FROM lib7_documents WHERE document_id=?",
                              (head.source.document_id,)).fetchone()
            if old and old[0] == head.revision:
                return "unchanged"
            if old and not replace:
                raise ValueError("Dokument zmienił się. Użyj nowego document_id lub jawnego --replace.")
            if old:
                con.execute("DELETE FROM lib7_documents WHERE document_id=?", (head.source.document_id,))
            con.execute("INSERT INTO lib7_documents VALUES (?,?,?,?,?)", (
                head.source.document_id, head.revision, head.source.model_dump_json(),
                head.file_sha256, int(reviewed)))
            con.executemany("INSERT INTO lib7_chunks VALUES (?,?,?)", [
                (c.chunk_id, c.source.document_id, c.model_dump_json()) for c in chunks])
        return "replaced" if old else "imported"

    def documents(self):
        with self.connection() as con:
            return [dict(document_id=r[0], revision=r[1], source=json.loads(r[2]),
                         file_sha256=r[3], reviewed=bool(r[4]), chunks=r[5])
                    for r in con.execute('''SELECT d.*, COUNT(c.chunk_id) FROM lib7_documents d
                        LEFT JOIN lib7_chunks c ON c.document_id=d.document_id
                        GROUP BY d.document_id ORDER BY d.document_id''')]

    def review(self, document_id: str, approve: bool):
        with self.connection() as con:
            if not con.execute("UPDATE lib7_documents SET reviewed=? WHERE document_id=?",
                               (int(approve), document_id)).rowcount:
                raise ValueError("Nie ma takiego dokumentu.")

    def chunks(self, ruleset: str, include_unreviewed=False):
        with self.connection() as con:
            rows = con.execute('''SELECT c.data_json, d.reviewed FROM lib7_chunks c
                JOIN lib7_documents d ON d.document_id=c.document_id ORDER BY c.chunk_id''').fetchall()
        found = []
        for text, reviewed in rows:
            c = Chunk.model_validate_json(text)
            if c.source.ruleset_id == ruleset and (reviewed or include_unreviewed):
                found.append(c.model_copy(update={"reviewed": bool(reviewed)}))
        return found

    def show(self, chunk_id: str):
        with self.connection() as con:
            row = con.execute('''SELECT c.data_json, d.reviewed FROM lib7_chunks c JOIN lib7_documents d
                ON d.document_id=c.document_id WHERE c.chunk_id=?''', (chunk_id,)).fetchone()
        if not row:
            raise ValueError("Nie ma takiego fragmentu (mógł zostać zastąpiony nowszym importem).")
        return Chunk.model_validate_json(row[0]).model_copy(update={"reviewed": bool(row[1])})

    def vectors(self, fingerprint):
        with self.connection() as con:
            return {cid: json.loads(v) for cid, v in con.execute(
                "SELECT chunk_id, vector_json FROM lib7_vectors WHERE fingerprint=?", (fingerprint,))}

    def save_vectors(self, chunks, vectors, fingerprint, dimensions):
        if len(chunks) != len(vectors):
            raise ValueError("Dostawca zwrócił inną liczbę wektorów niż fragmentów.")
        normalized = [normalize_vector(v, dimensions) for v in vectors]
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            for c, v in zip(chunks, normalized):
                row = con.execute("SELECT data_json FROM lib7_chunks WHERE chunk_id=?", (c.chunk_id,)).fetchone()
                if not row or Chunk.model_validate_json(row[0]).revision != c.revision:
                    raise ValueError("Dokument zmienił się podczas indeksowania. Powtórz indeksowanie.")
                con.execute("INSERT OR REPLACE INTO lib7_vectors VALUES (?,?,?)",
                            (c.chunk_id, fingerprint, json.dumps(v, allow_nan=False)))


def normalize_vector(values, dimensions):
    if len(values) != dimensions or any(isinstance(v, bool) for v in values):
        raise ValueError("Niepoprawny wymiar lub typ wektora.")
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError("Wektor zawiera wartości inne niż liczby skończone.")
    norm = math.sqrt(sum(v * v for v in values))
    if not norm or not math.isfinite(norm):
        raise ValueError("Wektor zerowy lub niepoprawny.")
    return [v / norm for v in values]


def corpus_revision(chunks):
    return canonical_hash(sorted((c.chunk_id, c.revision) for c in chunks))
