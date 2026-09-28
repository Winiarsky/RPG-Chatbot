"""CLI etapu 3. Nie tworzy klienta LLM i nie potrzebuje klucza API."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from pydantic import ValidationError
from game_config import load_game_settings
from game_service import ensure_demo
from storage import StorageError
from mechanics3.repository import ActionRepository, DEFAULT_PACK


def backup_database(db_path: Path) -> Path:
    """SQLite online backup, zanim konstruktor rozszerzy bazę. Nie nadpisuje kopii."""
    path = db_path.resolve()
    if not path.is_file():
        raise ValueError('Baza jeszcze nie istnieje; najpierw utwórz kampanię.')
    folder = path.parent / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = folder / f'{path.stem}-backup-{stamp}-{uuid4().hex[:8]}.sqlite3'
    with target.open('xb'):
        pass
    source = destination = None
    try:
        source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        destination = sqlite3.connect(target)
        source.backup(destination)
    except BaseException:
        if destination is not None:
            destination.close()
            destination = None
        target.unlink(missing_ok=True)
        raise
    finally:
        if source is not None:
            source.close()
        if destination is not None:
            destination.close()
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('backup')
    for name in ['init', 'show', 'prepare', 'resolve', 'cancel']:
        cmd = sub.add_parser(name)
        cmd.add_argument('--campaign', default='demo')
        if name == 'init':
            cmd.add_argument('--rules', type=Path, default=DEFAULT_PACK)
        if name == 'prepare':
            cmd.add_argument('definition_id', choices=['force_door', 'observe_scene'])
            cmd.add_argument('--action-id', default=None)
        if name in {'resolve', 'cancel'}:
            cmd.add_argument('action_id')
        if name == 'resolve':
            group = cmd.add_mutually_exclusive_group(required=True)
            group.add_argument('--manual', nargs='+', type=int, help='Surowe kości, bez premii.')
            group.add_argument('--auto', action='store_true')
    args = parser.parse_args(argv)
    try:
        settings = load_game_settings()
        if args.command == 'backup':
            print(f'Kopia: {backup_database(settings.db_path)}')
            return 0
        repo = ActionRepository(settings.db_path)
        if args.command == 'init':
            if args.campaign == 'demo':
                ensure_demo(repo, settings)
            pack = repo.ensure_pack(args.campaign, args.rules)
            print(f'Przypięty pakiet: {pack.id}; kampania: {args.campaign}; baza: {settings.db_path}')
            return 0
        if args.command == 'show':
            output = {'campaign': args.campaign, 'progress': repo.progress(args.campaign),
                      'pending': repo.pending_action(args.campaign), 'actions': repo.recent_actions(args.campaign)}
        elif args.command == 'prepare':
            output = repo.prepare_action(args.campaign, args.definition_id,
                        action_id=args.action_id or uuid4().hex,
                        expected_revision=repo.load(args.campaign).revision)
        elif args.command == 'resolve':
            output = repo.resolve_action(args.campaign, args.action_id,
                        source='manual' if args.manual is not None else 'app', dice=args.manual)
        else:
            output = repo.cancel_action(args.campaign, args.action_id)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if output.get('status') == 'invalidated':
            print('Stan świata zmienił się przed rzutem. Próba unieważniona bez losowania.')
            return 2
        return 0
    except ValidationError:
        print('Błąd: dane nie pasują do schematu (np. kości poza zakresem 1–20).')
        return 1
    except (StorageError, ValueError, OSError, sqlite3.Error) as exc:
        print(f'Błąd: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
