"""Włączenie profilu, nowa kampania i publiczny podgląd; bez wywołań API."""
import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
from uuid import uuid4
from agents4.runtime import safe_error
from game_config import load_game_settings
from scenario_loader import load_yaml
from story5.models import StoryBook
from story5.repository import DEFAULT_STORY
from improv6.models import ImprovisationProfile
from improv6.repository import ImprovisationRepository, DEFAULT_PROFILE


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('init', 'enable'):
        q = sub.add_parser(name)
        q.add_argument('--campaign', default='demo6' if name == 'init' else 'demo5')
        q.add_argument('--profile', type=Path, default=DEFAULT_PROFILE)
    q = sub.add_parser('new')
    q.add_argument('--id', default=None)
    q.add_argument('--name', default='[krok 6] Wieża — improwizacja')
    q.add_argument('--story', type=Path, default=DEFAULT_STORY)
    q.add_argument('--profile', type=Path, default=DEFAULT_PROFILE)
    q = sub.add_parser('show')
    q.add_argument('--campaign', default='demo6')
    sub.add_parser('list')
    sub.add_parser('doctor')
    q = sub.add_parser('check-profile')
    q.add_argument('--profile', type=Path, default=DEFAULT_PROFILE)
    q.add_argument('--story', type=Path, default=DEFAULT_STORY)
    args = p.parse_args()
    try:
        if args.command == 'doctor':
            for package in ('pydantic', 'PyYAML', 'langgraph', 'langgraph-checkpoint-sqlite', 'streamlit', 'langchain-openai'):
                try:
                    print(package + ':', metadata.version(package))
                except metadata.PackageNotFoundError:
                    print(package + ': BRAK')
            from story5.roles import mode5
            print('Format decyzji:', mode5())
            print('Zachowano klienta z chat_service.py; użyj swojej poprawki use_responses_api=True.')
            print('Nie wywołano API ani nie wypisano sekretów.')
            return 0
        if args.command == 'check-profile':
            book = load_yaml(args.story, StoryBook)
            profile = load_yaml(args.profile, ImprovisationProfile)
            ImprovisationRepository.validate_profile(book, profile)
            print(f'OK: profil {profile.id}; interakcje: {len(profile.signals)}.')
            return 0
        game = load_game_settings()
        repo = ImprovisationRepository(game.db_path)
        repo.initialize()
        if args.command in {'init', 'enable'}:
            if args.command == 'init' and not any(c['campaign_id'] == args.campaign for c in repo.story_campaigns()):
                repo.create_story(args.campaign, '[krok 6] Wieża — improwizacja', game.character_path)
            added = repo.enable(args.campaign, args.profile)
            print('Włączono profil.' if added else 'Profil był już włączony; niczego nie zresetowano.')
            print('Kampania:', args.campaign, '| Baza:', game.db_path)
        elif args.command == 'new':
            # Walidacja przed tworzeniem zapisu.
            book = load_yaml(args.story, StoryBook)
            profile = load_yaml(args.profile, ImprovisationProfile)
            repo.validate_profile(book, profile)
            cid = args.id or 'improv_' + uuid4().hex[:12]
            repo.create_story(cid, args.name, game.character_path, args.story)
            repo.enable(cid, args.profile)
            print('Utworzono:', cid)
        elif args.command == 'list':
            print(json.dumps(repo.enabled_campaigns(), ensure_ascii=False, indent=2))
        else:
            context, catalog, _, _ = repo.bundle4(args.campaign)
            print(json.dumps({'public_state': context, 'catalog': catalog}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print('BŁĄD:', safe_error(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
