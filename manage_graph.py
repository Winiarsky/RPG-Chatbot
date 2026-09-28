"""CLI: deklaracja -> LangGraph -> trwałe oczekiwanie -> wynik -> narrator."""
import argparse
import json
from importlib.metadata import version, PackageNotFoundError
import sys


def doctor():
    print('Python:', sys.version.split()[0])
    for name in ('langgraph', 'langgraph-checkpoint', 'langgraph-checkpoint-sqlite',
                 'langchain-core', 'langchain-openai', 'streamlit', 'filelock', 'pydantic'):
        try:
            print(f'{name}: {version(name)}')
        except PackageNotFoundError:
            print(f'{name}: BRAK')
    from config import load_settings
    from game_config import load_game_settings
    from agents4.roles import intent_mode
    settings = load_settings()
    print('Provider:', settings.provider, '| model:', settings.model, '| Intent:', intent_mode())
    print('Klucz ustawiony:', bool(settings.api_key))
    print('Baza gry i checkpointów:', load_game_settings().db_path)
    print('Nie wykonano wywołania API.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    for name in ('init', 'start', 'show', 'resume', 'retry', 'abort'):
        p = sub.add_parser(name)
        p.add_argument('--campaign', default='demo')
        if name == 'start':
            p.add_argument('text')
            p.add_argument('--turn-id', default=None)
        elif name not in {'show', 'init'}:
            p.add_argument('turn_id')
        if name == 'resume':
            p.add_argument('--interrupt-id', required=True)
            choice = p.add_mutually_exclusive_group(required=True)
            choice.add_argument('--manual', nargs='+', type=int)
            choice.add_argument('--auto', action='store_true')
            choice.add_argument('--cancel', action='store_true')
            choice.add_argument('--retry-narration', action='store_true')
            choice.add_argument('--fallback', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'doctor':
            doctor()
            return 0
        from config import load_settings
        from game_config import load_game_settings
        from game_service import ensure_demo
        from agents4.repository import GraphRepository
        from agents4.runtime import make_controller
        settings = load_game_settings()
        repo = GraphRepository(settings.db_path)
        if args.campaign == 'demo':
            ensure_demo(repo, settings)
        repo.ensure_pack(args.campaign)
        if args.command == 'init':
            print(f'Gotowe: {args.campaign}; baza: {repo.db_path}. Nie zresetowano zapisu.')
            return 0
        controller = make_controller(repo, load_settings())
        if args.command == 'start':
            output = controller.start(args.campaign, args.text, turn_id=args.turn_id)
        elif args.command == 'show':
            run = repo.active_run(args.campaign)
            if run is None:
                recent = repo.recent_runs(args.campaign, 1)
                run = recent[0] if recent else None
            output = controller.view(args.campaign, run['turn_id']) if run else {'message': 'Brak deklaracji kroku 4.'}
        elif args.command in {'retry', 'abort'}:
            output = getattr(controller, args.command)(args.campaign, args.turn_id)
        else:
            if args.manual is not None:
                value = {'choice': 'manual', 'dice': args.manual}
            else:
                value = {'choice': 'app' if args.auto else 'cancel' if args.cancel else
                                   'retry' if args.retry_narration else 'fallback'}
            output = controller.resume(args.campaign, args.turn_id, args.interrupt_id, value)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        from agents4.runtime import safe_error
        print('Błąd:', safe_error(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
