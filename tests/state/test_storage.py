from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier

from pydantic import ValidationError
import pytest

from storage import CampaignNotFound, GameRepository, RequestConflict, RevisionConflict, StorageError


def test_initial_state_and_restart(repo):
    initial = repo.load("demo")
    assert initial.revision == 0
    assert initial.state.character.name == "Torin"
    assert initial.state.character.hp_current == 12
    assert initial.state.doors["tower_door"].is_open is False
    repo.set_door("demo", door_id="tower_door", is_open=True, expected_revision=0, request_id="a")
    repo.set_hp("demo", hp_current=7, expected_revision=1, request_id="b")
    restored = GameRepository(repo.db_path).load("demo")
    assert restored.revision == 2
    assert restored.state.character.hp_current == 7
    assert restored.state.doors["tower_door"].is_open is True
    assert [e["kind"] for e in repo.events("demo")] == ["CampaignCreated", "DoorStateChanged", "HpAdjusted"]


def test_create_never_overwrites(repo):
    snapshot = repo.load("demo")
    with pytest.raises(StorageError):
        repo.create("demo", "Inna nazwa", snapshot.state.character, snapshot.scenario)
    assert repo.load("demo") == snapshot


def test_duplicate_request_not_applied_twice(repo):
    args = dict(door_id="tower_door", is_open=True, expected_revision=0, request_id="same")
    assert repo.set_door("demo", **args) is True
    assert repo.set_door("demo", **args) is False
    assert repo.load("demo").revision == 1
    assert len(repo.events("demo")) == 2


def test_reused_request_id_for_different_operation_fails(repo):
    repo.set_hp("demo", hp_current=7, expected_revision=0, request_id="same")
    with pytest.raises(RequestConflict):
        repo.set_hp("demo", hp_current=6, expected_revision=1, request_id="same")
    with pytest.raises(RequestConflict):
        repo.set_door("demo", door_id="tower_door", is_open=True, expected_revision=1, request_id="same")


def test_stale_write_rejected(repo):
    repo.set_hp("demo", hp_current=7, expected_revision=0, request_id="a")
    with pytest.raises(RevisionConflict):
        repo.set_hp("demo", hp_current=6, expected_revision=0, request_id="b")
    assert repo.load("demo").state.character.hp_current == 7


@pytest.mark.parametrize("hp", [-1, 13, "7", True])
def test_invalid_hp_rolls_back(repo, hp):
    with pytest.raises(ValidationError):
        repo.set_hp("demo", hp_current=hp, expected_revision=0, request_id="bad")
    assert repo.load("demo").revision == 0
    assert len(repo.events("demo")) == 1


def test_unknown_door_and_nonbool_rejected(repo):
    with pytest.raises(ValueError):
        repo.set_door("demo", door_id="missing", is_open=True, expected_revision=0, request_id="a")
    with pytest.raises(ValidationError):
        repo.set_door("demo", door_id="tower_door", is_open="true", expected_revision=0, request_id="b")
    assert repo.load("demo").revision == 0


def test_chat_pair_saved_atomically_and_idempotently(repo):
    args = dict(request_id="turn1", expected_revision=0, user_text="Hej", assistant_text="Witaj")
    assert repo.append_turn("demo", **args) == "Witaj"
    args["assistant_text"] = "Nie nadpisuj mnie"
    assert repo.append_turn("demo", **args) == "Witaj"
    state, turns = GameRepository(repo.db_path).read_bundle("demo")
    assert state.revision == 1
    assert len(turns) == 1
    assert turns[0]["user_text"] == "Hej"
    assert turns[0]["assistant_text"] == "Witaj"


def test_chat_duplicate_with_changed_input_fails(repo):
    repo.append_turn("demo", request_id="x", expected_revision=0, user_text="A", assistant_text="B")
    with pytest.raises(RequestConflict):
        repo.append_turn("demo", request_id="x", expected_revision=1, user_text="C", assistant_text="D")


def test_empty_turn_not_saved(repo):
    with pytest.raises(ValueError):
        repo.append_turn("demo", request_id="x", expected_revision=0, user_text=" ", assistant_text="B")
    assert repo.load("demo").revision == 0


def test_transaction_rolls_back_both_chat_and_revision_on_event_failure(repo, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Symulacja błędu zapisu zdarzenia")
    monkeypatch.setattr(repo, "_event", fail)
    with pytest.raises(RuntimeError):
        repo.append_turn("demo", request_id="x", expected_revision=0, user_text="A", assistant_text="B")
    snapshot, turns = repo.read_bundle("demo")
    assert snapshot.revision == 0
    assert turns == []


def test_transaction_rolls_back_world_on_event_failure(repo, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Symulacja błędu zapisu zdarzenia")
    monkeypatch.setattr(repo, "_event", fail)
    with pytest.raises(RuntimeError):
        repo.set_hp("demo", hp_current=7, request_id="x", expected_revision=0)
    assert repo.load("demo").state.character.hp_current == 12


def test_history_order_and_limit(repo):
    for i in range(4):
        repo.append_turn("demo", request_id=f"t{i}", expected_revision=i,
                         user_text=f"q{i}", assistant_text=f"a{i}")
    _, recent = repo.read_bundle("demo", limit_turns=2)
    assert [t["user_text"] for t in recent] == ["q2", "q3"]
    assert len(repo.read_bundle("demo")[1]) == 4


def test_campaigns_are_isolated(repo):
    first = repo.load("demo")
    repo.create("second", "Druga", first.state.character, first.scenario)
    repo.set_hp("second", hp_current=3, expected_revision=0, request_id="x")
    assert repo.load("demo").state.character.hp_current == 12


def test_parallel_stale_writers_one_wins(repo):
    barrier = Barrier(2)
    def write(index):
        revision = repo.load("demo").revision
        barrier.wait()
        try:
            repo.set_hp("demo", hp_current=index + 1, expected_revision=revision,
                        request_id=f"parallel-{index}")
            return "saved"
        except RevisionConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, [0, 1]))
    assert sorted(results) == ["conflict", "saved"]
    assert repo.load("demo").revision == 1


def test_missing_campaign_raises(repo):
    with pytest.raises(CampaignNotFound):
        repo.load("not_here")


@pytest.mark.parametrize("version", [0, 2])
def test_unrelated_or_future_database_not_overwritten(tmp_path, version):
    path = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE precious (value TEXT)")
        db.execute("INSERT INTO precious VALUES ('keep')")
        db.execute(f"PRAGMA user_version={version}")
    with pytest.raises(StorageError):
        GameRepository(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM precious").fetchone()[0] == "keep"
