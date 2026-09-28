"""SQLite: snapshot świata + pełne pary czatu + dziennik zmian.

Każdy zapis ma transakcję, kontrolę rewizji i klucz ponowienia.
Dziennik jest audytem, nie kompletnym mechanizmem odtwarzania event sourcing.
Połączenia są krótkotrwałe; żadne nie czeka na wynik LLM.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from schemas import CampaignSnapshot, CharacterState, ScenarioDefinition
from scenario_loader import initial_state


class StorageError(RuntimeError):
    pass


class CampaignNotFound(StorageError):
    pass


class RevisionConflict(StorageError):
    pass


class RequestConflict(StorageError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def signature(kind: str, request: dict) -> str:
    return hashlib.sha256(encode([kind, request]).encode("utf-8")).hexdigest()


class GameRepository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self, *, write: bool = False, read: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.db_path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            elif read:
                db.execute("BEGIN")
            yield db
            if db.in_transaction:
                db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connection(write=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 1:
                return
            if version != 0:
                raise StorageError(f"Nieobsługiwana wersja bazy: {version}. Nie zmieniono danych.")
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if tables:
                raise StorageError("Plik zawiera inną bazę danych. Wybierz nowy GAME_DB_PATH.")
            db.execute("""CREATE TABLE campaigns (
                campaign_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 0),
                state_json TEXT NOT NULL, scenario_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE game_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                request_id TEXT NOT NULL, request_signature TEXT NOT NULL,
                revision INTEGER NOT NULL, kind TEXT NOT NULL,
                payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(campaign_id, request_id), UNIQUE(campaign_id, revision)
            )""")
            db.execute("""CREATE TABLE chat_turns (
                campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                request_id TEXT NOT NULL, revision INTEGER NOT NULL,
                user_text TEXT NOT NULL, assistant_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(campaign_id, request_id), UNIQUE(campaign_id, revision)
            )""")
            db.execute("PRAGMA user_version = 1")

    @staticmethod
    def _load(db: sqlite3.Connection, campaign_id: str) -> CampaignSnapshot:
        row = db.execute("SELECT * FROM campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
        if row is None:
            raise CampaignNotFound(f"Nie znaleziono kampanii: {campaign_id}.")
        return CampaignSnapshot.model_validate({
            "campaign_id": row["campaign_id"], "name": row["name"],
            "revision": row["revision"], "state": json.loads(row["state_json"]),
            "scenario": json.loads(row["scenario_json"]),
        })

    def load(self, campaign_id: str) -> CampaignSnapshot:
        with self.connection() as db:
            return self._load(db, campaign_id)

    def list_campaigns(self) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT campaign_id, name, revision FROM campaigns ORDER BY created_at, campaign_id"
            )]

    def create(self, campaign_id: str, name: str, character: CharacterState,
               scenario: ScenarioDefinition) -> CampaignSnapshot:
        snapshot = CampaignSnapshot(campaign_id=campaign_id, name=name, revision=0,
                                    state=initial_state(character, scenario), scenario=scenario)
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM campaigns WHERE campaign_id=?", (campaign_id,)).fetchone():
                raise StorageError("Kampania o tym ID już istnieje. Nie nadpisano jej.")
            now = utc_now()
            db.execute("INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, ?, ?)", (
                campaign_id, name, 0, snapshot.state.model_dump_json(),
                scenario.model_dump_json(), now, now,
            ))
            self._event(db, campaign_id, "__created__", "", 0, "CampaignCreated",
                        {"scenario_id": scenario.id, "character_id": character.id})
        return snapshot

    @staticmethod
    def _event(db, campaign_id, request_id, request_signature, revision, kind, payload):
        db.execute("""INSERT INTO game_events
            (campaign_id, request_id, request_signature, revision, kind, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""", (
                campaign_id, request_id, request_signature, revision, kind, encode(payload), utc_now()
            ))

    @staticmethod
    def _already_done(db, campaign_id: str, request_id: str, request_signature: str) -> bool:
        if not request_id.strip() or len(request_id) > 128:
            raise ValueError("request_id musi mieć 1–128 znaków.")
        row = db.execute(
            "SELECT request_signature FROM game_events WHERE campaign_id=? AND request_id=?",
            (campaign_id, request_id),
        ).fetchone()
        if row is None:
            return False
        if row[0] != request_signature:
            raise RequestConflict("Ten identyfikator żądania był już użyty do innej operacji.")
        return True

    @staticmethod
    def _check_revision(snapshot: CampaignSnapshot, expected_revision: int):
        if snapshot.revision != expected_revision:
            raise RevisionConflict(
                "Stan kampanii zmienił się w innym oknie lub poleceniu. "
                "Odśwież dane przed ponowieniem."
            )

    def _edit(self, campaign_id: str, *, expected_revision: int, request_id: str,
              kind: str, request: dict) -> bool:
        request_signature = signature(kind, request)
        with self.connection(write=True) as db:
            if self._already_done(db, campaign_id, request_id, request_signature):
                return False
            snapshot = self._load(db, campaign_id)
            self._check_revision(snapshot, expected_revision)
            data = snapshot.model_dump()
            if kind == "DoorStateChanged":
                object_id = request["door_id"]
                if object_id not in data["state"]["doors"]:
                    raise ValueError("Nie ma drzwi o podanym identyfikatorze.")
                before = data["state"]["doors"][object_id]["is_open"]
                data["state"]["doors"][object_id]["is_open"] = request["is_open"]
            elif kind == "HpAdjusted":
                before = data["state"]["character"]["hp_current"]
                data["state"]["character"]["hp_current"] = request["hp_current"]
            else:
                raise ValueError("Nieobsługiwana operacja zmiany stanu.")
            data["revision"] += 1
            # Pełna ponowna walidacja, nie model_copy(update=...), które jej nie wykonuje.
            updated = CampaignSnapshot.model_validate(data)
            db.execute("""UPDATE campaigns SET state_json=?, revision=?, updated_at=?
                          WHERE campaign_id=?""", (
                updated.state.model_dump_json(), updated.revision, utc_now(), campaign_id
            ))
            self._event(db, campaign_id, request_id, request_signature, updated.revision,
                        kind, {"source": "manual_test", "before": before, **request})
        return True

    def set_door(self, campaign_id: str, *, door_id: str, is_open: bool,
                 expected_revision: int, request_id: str) -> bool:
        return self._edit(campaign_id, expected_revision=expected_revision, request_id=request_id,
                          kind="DoorStateChanged", request={"door_id": door_id, "is_open": is_open})

    def set_hp(self, campaign_id: str, *, hp_current: int,
               expected_revision: int, request_id: str) -> bool:
        return self._edit(campaign_id, expected_revision=expected_revision, request_id=request_id,
                          kind="HpAdjusted", request={"hp_current": hp_current})

    def read_bundle(self, campaign_id: str, *, limit_turns: int | None = None):
        """Spójny odczyt świata i rozmowy w jednej transakcji odczytu."""
        if limit_turns is not None and limit_turns < 1:
            raise ValueError("limit_turns musi być dodatni.")
        with self.connection(read=True) as db:
            snapshot = self._load(db, campaign_id)
            query = "SELECT * FROM chat_turns WHERE campaign_id=? ORDER BY revision DESC"
            params: tuple = (campaign_id,)
            if limit_turns is not None:
                query += " LIMIT ?"
                params += (limit_turns,)
            turns = [dict(row) for row in db.execute(query, params)]
            turns.reverse()
            return snapshot, turns

    def get_turn(self, campaign_id: str, request_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM chat_turns WHERE campaign_id=? AND request_id=?",
                             (campaign_id, request_id)).fetchone()
            return dict(row) if row else None

    def append_turn(self, campaign_id: str, *, request_id: str, expected_revision: int,
                    user_text: str, assistant_text: str) -> str:
        if not user_text.strip() or not assistant_text.strip():
            raise ValueError("Nie zapisujemy pustych wiadomości.")
        # Tożsamością żądania jest deklaracja; ponowne wygenerowanie odpowiedzi
        # nie może nadpisać wcześniej zatwierdzonej odpowiedzi dla tego request_id.
        request_signature = signature("ChatTurnSaved", {"user_text": user_text})
        with self.connection(write=True) as db:
            if self._already_done(db, campaign_id, request_id, request_signature):
                return db.execute(
                    "SELECT assistant_text FROM chat_turns WHERE campaign_id=? AND request_id=?",
                    (campaign_id, request_id),
                ).fetchone()[0]
            snapshot = self._load(db, campaign_id)
            self._check_revision(snapshot, expected_revision)
            revision = snapshot.revision + 1
            now = utc_now()
            db.execute("INSERT INTO chat_turns VALUES (?, ?, ?, ?, ?, ?)",
                       (campaign_id, request_id, revision, user_text, assistant_text, now))
            db.execute("UPDATE campaigns SET revision=?, updated_at=? WHERE campaign_id=?",
                       (revision, now, campaign_id))
            self._event(db, campaign_id, request_id, request_signature, revision, "ChatTurnSaved",
                        {"turn_id": request_id, "context_revision": snapshot.revision})
        return assistant_text

    def events(self, campaign_id: str, limit: int = 100) -> list[dict]:
        with self.connection() as db:
            self._load(db, campaign_id)
            rows = db.execute("""SELECT revision, kind, payload_json, created_at
                  FROM game_events WHERE campaign_id=? ORDER BY revision DESC LIMIT ?""",
                              (campaign_id, limit)).fetchall()
            return [{"revision": r["revision"], "kind": r["kind"],
                     "payload": json.loads(r["payload_json"]), "created_at": r["created_at"]}
                    for r in reversed(rows)]
