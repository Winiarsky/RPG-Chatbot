"""Inicjalizacja, kontrola i publiczny podgląd. Żadne polecenie nie wywołuje API."""
import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
from uuid import uuid4
from agents.runtime import safe_error
from game_config import load_game_settings
from scenario_loader import load_yaml
from story.models import StoryBook
from story.repository import StoryRepository, DEFAULT_STORY


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('--campaign', default='demo5')
    new = sub.add_parser('new')
    new.add_argument('--id', default=None)
    new.add_argument('--name', default='[krok 5] Wieża latarnika')
    new.add_argument('--story', type=Path, default=DEFAULT_STORY)
    sub.add_parser('list')
    show = sub.add_parser('show')
    show.add_argument('--campaign', default='demo5')
    sub.add_parser('doctor')
    check = sub.add_parser('check-template')
    check.add_argument('--story', type=Path, default=DEFAULT_STORY)
    args = p.parse_args()
    try:
        if args.command == 'doctor':
            import sys
            print('Python:', sys.version.split()[0])
            for package in ['langgraph','langgraph-checkpoint-sqlite','streamlit','langchain-openai','pydantic','PyYAML','filelock']:
                try:
                    print(package + ':', metadata.version(package))
                except metadata.PackageNotFoundError:
                    print(package + ': BRAK')
            from story.roles import mode5
            print('Tryb decyzji:', mode5())
            print('Klient LLM pochodzi z twojego chat_service.py. Zachowaj use_responses_api=True.')
            print('Nie wywołano API i nie odczytano wartości klucza.')
            return 0
        if args.command == 'check-template':
            book = load_yaml(args.story, StoryBook)
            print(f'OK: {book.id}, sceny={len(book.scenario.scenes)}, NPC={len(book.npcs)}, działania={len(book.actions)}.')
            return 0
        settings = load_game_settings()
        repo = StoryRepository(settings.db_path)
        repo.initialize()
        if args.command == 'init':
            if any(c['campaign_id']==args.campaign for c in repo.story_campaigns()):
                print('Kampania już istnieje. Nie zresetowano zapisu ani definicji.')
            else:
                repo.create_story(args.campaign, '[krok 5] Wieża latarnika', settings.character_path)
            print('Kampania:', args.campaign, '| Baza:', settings.db_path)
        elif args.command == 'new':
            cid = args.id or 'story_' + uuid4().hex[:12]
            repo.create_story(cid, args.name, settings.character_path, args.story)
            print('Utworzono:', cid)
        elif args.command == 'list':
            print(json.dumps(repo.story_campaigns(), ensure_ascii=False, indent=2))
        elif args.command == 'show':
            context, catalog, _, _ = repo.bundle4(args.campaign)
            print(json.dumps({'public_state': context, 'catalog': catalog}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print('BŁĄD:', safe_error(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
