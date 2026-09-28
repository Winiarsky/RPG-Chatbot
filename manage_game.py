"""Lokalne narzędzia diagnostyczne. Komendy zmiany stanu nie są akcjami gracza."""
import argparse
import json
import sqlite3
from uuid import uuid4

from pydantic import ValidationError

from game_config import load_game_settings
from game_service import create_campaign, ensure_demo
from public_context import build_public_context
from storage import GameRepository, StorageError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Utwórz demo tylko wtedy, gdy nie istnieje.")
    sub.add_parser("list", help="Lista zapisanych kampanii.")
    new = sub.add_parser("new", help="Nowa kampania z bieżących szablonów YAML.")
    new.add_argument("--id", required=True)
    new.add_argument("--name", default="Nowa przygoda")
    for name in ("show", "events", "history", "open-door", "close-door", "set-hp"):
        command = sub.add_parser(name)
        command.add_argument("--campaign", default="demo")
        if name in {"open-door", "close-door", "set-hp"}:
            command.add_argument("--request-id", default=None,
                                 help="Opcjonalny stały identyfikator do testu ponowienia.")
        if name in {"open-door", "close-door"}:
            command.add_argument("--door", default="tower_door")
        if name == "set-hp":
            command.add_argument("value", type=int)
    args = parser.parse_args()
    try:
        settings = load_game_settings()
        repo = GameRepository(settings.db_path)
        if args.command == "init":
            snapshot = ensure_demo(repo, settings)
            print(f"Kampania: {snapshot.campaign_id}; rewizja: {snapshot.revision}")
            print(f"Baza: {repo.db_path}")
            print("Istniejący zapis nie jest resetowany.")
            return
        if args.command == "list":
            result = repo.list_campaigns()
        elif args.command == "new":
            snapshot = create_campaign(repo, settings, args.id, args.name)
            result = {"campaign_id": snapshot.campaign_id, "revision": snapshot.revision}
        elif args.command == "show":
            result = build_public_context(repo.load(args.campaign))
        elif args.command == "events":
            result = repo.events(args.campaign)
        elif args.command == "history":
            _, result = repo.read_bundle(args.campaign)
        else:
            snapshot = repo.load(args.campaign)
            request_id = args.request_id or uuid4().hex
            common = dict(expected_revision=snapshot.revision, request_id=request_id)
            if args.command == "set-hp":
                changed = repo.set_hp(args.campaign, hp_current=args.value, **common)
            else:
                changed = repo.set_door(args.campaign, door_id=args.door,
                                        is_open=args.command == "open-door", **common)
            print("Zapisano korektę testową." if changed else "Żądanie było już zapisane; bez powtórzenia.")
            print(f"request_id: {request_id}")
            result = build_public_context(repo.load(args.campaign))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValidationError as exc:
        # Wyświetl same ścieżki i typy błędów, bez zawartości tajnych pól.
        locations = [".".join(map(str, e["loc"])) + ": " + e["type"]
                     for e in exc.errors(include_input=False)]
        parser.exit(1, "Błąd walidacji: " + "; ".join(locations) + "\nNie zapisano operacji.\n")
    except (StorageError, ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"Błąd: {exc}\n")


if __name__ == "__main__":
    main()
