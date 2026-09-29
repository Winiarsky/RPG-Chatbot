"""Rzeczywisty LangGraph + SQLite, ale bez API, na tymczasowym zapisie."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import sys


def main():
    try:
        import langgraph.graph
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        print('BRAK ZALEŻNOŚCI: zainstaluj biblioteki kroku 4. Test LangGraph NIE został wykonany.')
        return 2
    from config import Settings
    from chat_service import ChatService
    from story.roles import StoryKeeper
    from improvisation.roles import GameMaster, Narrator, Improviser
    from improvisation.repository import ImprovisationRepository
    from improvisation.runtime import RecoveryController
    root = Path(__file__).resolve().parents[2]
    for key in ('LANGSMITH_TRACING', 'LANGCHAIN_TRACING', 'LANGCHAIN_TRACING_V2'):
        os.environ[key] = 'false'
    def controller(repo):
        t = ChatService(Settings(provider='mock'))
        return RecoveryController(repo, GameMaster(t, 'function_calling'), Narrator(t),
            StoryKeeper(t, 'function_calling'), Improviser(t, 'function_calling'))
    try:
        with TemporaryDirectory(prefix='rpg-step6-') as temp:
            path = Path(temp) / 'game.sqlite3'
            repo = ImprovisationRepository(path)
            repo.initialize()
            repo.create_story('smoke6', 'Próba', root/'characters/torin.yaml')
            repo.enable('smoke6')
            ctl = controller(repo)
            view = ctl.start('smoke6', 'pukam', turn_id='knock')
            assert view['run']['status'] == 'done', view['run']
            state = repo.load('smoke6').state
            assert state.doors['tower_door'].is_open
            assert state.scene_id == 'tower_entrance'
            assert repo.progress('smoke6')['elapsed_seconds'] == 6
            # Ponowienie tego samego żądania nie wykonuje skutków ponownie.
            controller(ImprovisationRepository(path)).start('smoke6', 'pukam', turn_id='knock')
            assert repo.progress('smoke6')['elapsed_seconds'] == 6
            view = ctl.start('smoke6', 'Pytam Martę o latarnika', turn_id='talk')
            assert view['interrupts'][0]['payload']['kind'] == 'story_confirm'
            # Zamknij połączenie i użyj nowego kontrolera/repozytorium: checkpoint musi istnieć.
            ctl = controller(ImprovisationRepository(path))
            again = ctl.view('smoke6', 'talk')
            iid = again['interrupts'][0]['id']
            view = ctl.resume('smoke6', 'talk', iid, {'choice': 'confirm'})
            assert view['run']['status'] == 'done'
            assert 'journal_hint' in repo.story('smoke6')[1].known_facts
            assert repo.load('smoke6').state.scene_id == 'tower_entrance'
            view = ctl.start('smoke6', 'Wchodzę do wieży', turn_id='enter')
            ctl.resume('smoke6', 'enter', view['interrupts'][0]['id'], {'choice':'confirm'})
            assert repo.load('smoke6').state.scene_id == 'vestibule'
            view = ctl.start('smoke6', 'Otrzepuję płaszcz', turn_id='emote')
            assert view['run']['status'] == 'done'
            assert repo.progress('smoke6')['elapsed_seconds'] == 46
        print('OK: rzeczywisty LangGraph + SQLite; pukanie, kontakt, ponowienie, restart, rozmowa i osobne wejście. Bez API.')
        return 0
    except Exception as exc:
        print(f'TEST NIE PRZESZEDŁ: {type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
