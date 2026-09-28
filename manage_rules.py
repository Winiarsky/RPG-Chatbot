"""Zarządzanie kampaniami, biblioteką zasad i przebiegiem rozgrywki."""
import argparse
import importlib.metadata as metadata
import json
from uuid import uuid4
from agents4.runtime import safe_error
from game_config import load_game_settings
from library7a.settings import load_library_settings
from library7a.store import LibraryStore
from rules7b.repository import RulesRepository
from rules7b.settings import load_integration_settings


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    sub.add_parser('capabilities')
    sub.add_parser('backup', help='Kopia SQLite bez zmiany zapisu kampanii.')
    sub.add_parser('list')
    for name in ('init', 'enable', 'show', 'consultations'):
        q = sub.add_parser(name)
        q.add_argument('--campaign', default='demo7b' if name != 'enable' else 'demo6')
        if name == 'consultations':
            q.add_argument('--turn')
    q = sub.add_parser('new')
    q.add_argument('--id')
    q.add_argument('--name', default='Wieża — biblioteka zasad')
    q = sub.add_parser('say')
    q.add_argument('text')
    q.add_argument('--campaign', default='demo7b')
    q.add_argument('--turn')
    for name in ('status', 'retry', 'abort', 'resume'):
        q = sub.add_parser(name)
        q.add_argument('turn')
        q.add_argument('--campaign', default='demo7b')
        if name == 'resume':
            q.add_argument('--choice', required=True,
                choices=['manual', 'app', 'cancel', 'confirm', 'retry', 'stop', 'fallback'])
            q.add_argument('--dice', type=int, nargs='+')
    return p


def check_library(cfg):
    store = LibraryStore(cfg.db_path)
    if not store.chunks(cfg.ruleset_id):
        raise ValueError('Brak zatwierdzonych materiałów tej wersji. Uruchom manage_library.py init lub sprawdź import.')


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'backup':
            from manage_checks import backup_database
            print('Kopia:', backup_database(load_game_settings().db_path))
            return 0
        lib, integration = load_library_settings(), load_integration_settings()
        if args.command == 'doctor':
            missing = []
            for package in ('langgraph', 'langgraph-checkpoint-sqlite', 'langchain-openai',
                            'streamlit', 'pydantic', 'PyYAML', 'filelock', 'pypdf', 'python-dotenv'):
                try:
                    print(package + ':', metadata.version(package))
                except metadata.PackageNotFoundError:
                    print(package + ': BRAK')
                    missing.append(package)
            print('Biblioteka:', lib.db_path)
            print('Pakiet zasad:', lib.ruleset_id)
            print('Wyszukiwanie:', integration.search_mode, '| Generowanie:', integration.generation)
            print('Klient zachowany: chat_service.py. Nie wypisano klucza i nie wywołano API.')
            return 1 if missing else 0
        if args.command == 'capabilities':
            from rules7b.capabilities import CAPABILITIES
            print(json.dumps({k: sorted(v) for k, v in CAPABILITIES.items()}, ensure_ascii=False, indent=2))
            print('To zakres implementacji, nie wszystkie dozwolone działania D&D. Help i dynamiczne efekty nie są dodawane przez RAG.')
            return 0
        game = load_game_settings()
        if game.db_path.resolve() == lib.db_path.resolve():
            raise ValueError('Baza biblioteki i kampanii muszą mieć różne ścieżki.')
        if args.command in {'enable', 'init', 'new'}:
            check_library(lib)
        repo = RulesRepository(game.db_path)
        repo.initialize()
        if args.command in {'enable', 'init', 'new'}:
            cid = (args.id or 'rules_' + uuid4().hex[:12]) if args.command == 'new' else args.campaign
            exists = any(c['campaign_id'] == cid for c in repo.story_campaigns())
            if args.command == 'new' or (args.command == 'init' and not exists):
                repo.create_story(cid, args.name if args.command == 'new' else 'Wieża', game.character_path)
                repo.enable(cid)
            changed = repo.enable_rules(cid, lib.ruleset_id)
            print('Włączono 7B.' if changed else '7B było już włączone; zapisu nie zresetowano.')
            print('Kampania:', cid)
        elif args.command == 'list':
            print(json.dumps(repo.enabled_campaigns(), ensure_ascii=False, indent=2))
        elif args.command == 'show':
            public, catalog, _, _ = repo.bundle4(args.campaign)
            print(json.dumps({'public_state': public, 'actions': catalog}, ensure_ascii=False, indent=2))
        elif args.command == 'consultations':
            records = repo.consultation(args.campaign, args.turn) if args.turn else repo.consultations(args.campaign)
            print(json.dumps(records, ensure_ascii=False, indent=2))
        else:
            from config import load_settings
            from rules7b.runtime import make_controller
            ctl = make_controller(repo, load_settings(), lib, integration)
            if args.command == 'say':
                view = ctl.start(args.campaign, args.text, turn_id=args.turn)
            elif args.command == 'status':
                view = ctl.view(args.campaign, args.turn)
            elif args.command in {'retry', 'abort'}:
                view = getattr(ctl, args.command)(args.campaign, args.turn)
            else:
                before = ctl.view(args.campaign, args.turn)
                if not before['interrupts']:
                    raise ValueError('Ta deklaracja nie oczekuje na decyzję.')
                payload = {'choice': args.choice}
                if args.dice is not None:
                    payload['dice'] = args.dice
                view = ctl.resume(args.campaign, args.turn, before['interrupts'][0]['id'], payload)
            print(json.dumps(view, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print('BŁĄD:', safe_error(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
