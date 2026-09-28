"""Rzeczywisty LangGraph + SQLite + import 7A; jawne atrapy modeli, bez API."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory


class TestWriter:
    """Wyłącznie test przepływu i odwołań, nie model odpowiadający na zasady."""
    def draft(self, question, hits, ruleset):
        from library7a.models import AnswerDraft, Claim, EvidenceRef
        if 'Fireball' in question:
            return AnswerDraft(status='insufficient', claims=[],
                unresolved_questions=['TEST ATRAPY: w pakiecie startowym nie ma opisu tego czaru.'])
        preferred = 'help' if 'Help' in question else 'advantage' if 'przewag' in question else 'ability_checks'
        chunk = next((h.chunk for h in hits if h.chunk.section_key == preferred), hits[0].chunk)
        return AnswerDraft(status='supported', claims=[Claim(text='TEST ATRAPY — sprawdzamy cytowanie, nie jakość rozumowania.',
            basis='rule', evidence=[EvidenceRef(chunk_id=chunk.chunk_id, quote=chunk.text[:160])])],
            unresolved_questions=[])


class TestConsultant:
    def __init__(self, store, cfg):
        from library7a.librarian import Librarian
        from library7a.search import Retriever
        self.reader = Librarian(Retriever(store, cfg), TestWriter())
        self.calls = []

    def consult(self, query, ruleset):
        assert self.reader.retriever.settings.ruleset_id == ruleset
        self.calls.append((query, ruleset))
        return self.reader.consult(query, generate=True)


def make_controller(repo, consultant):
    from config import Settings
    from chat_service import ChatService
    from story5.roles import StoryKeeper
    from improv6.roles import Narrator
    from rules7b.roles import GameMaster, Improviser
    from rules7b.runtime import RulesController
    t = ChatService(Settings(provider='mock'))
    return RulesController(repo, GameMaster(t), Narrator(t), StoryKeeper(t), Improviser(t), consultant)


def main():
    try:
        import langgraph.graph
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        print('BRAK ZALEŻNOŚCI: test prawdziwego LangGraph NIE został wykonany. Zainstaluj requirements.txt.')
        return 2
    for key in ('LANGSMITH_TRACING', 'LANGCHAIN_TRACING', 'LANGCHAIN_TRACING_V2'):
        os.environ[key] = 'false'
    from library7a.settings import ROOT, LibrarySettings
    from library7a.store import LibraryStore
    from library7a.importers import load_manifest
    from rules7b.repository import RulesRepository
    with TemporaryDirectory(prefix='rpg7b-') as directory:
        cfg = LibrarySettings(db_path=Path(directory)/'library.sqlite3')
        store = LibraryStore(cfg.db_path, create=True)
        _, chunks, _ = load_manifest(ROOT/'materials7a/starter/manifest.yaml')
        store.import_chunks(chunks, reviewed=True)
        repo = RulesRepository(Path(directory)/'game.sqlite3')
        repo.initialize()
        for cid in ('smoke', 'roll_case'):
            repo.create_story(cid, 'Test 7B', ROOT/'characters/torin.yaml')
            repo.enable(cid)
            repo.enable_rules(cid, cfg.ruleset_id)
        consultant = TestConsultant(store, cfg)
        ctl = make_controller(repo, consultant)
        before = repo.load('smoke').state.model_dump()
        view = ctl.start('smoke', '/zasady Jak działa przewaga?', turn_id='question')
        assert view['run']['status'] == 'done' and view['rules']['answer']['status'] == 'supported'
        assert before == repo.load('smoke').state.model_dump()
        assert repo.progress('smoke')['elapsed_seconds'] == 0
        assert len(consultant.calls) == 1
        # Zakończona deklaracja ani odczyt źródeł nie powtarzają konsultacji.
        ctl.start('smoke', '/zasady Jak działa przewaga?', turn_id='question')
        assert len(consultant.calls) == 1
        ctl.start('smoke', 'pukam', turn_id='knock')
        assert repo.load('smoke').state.doors['tower_door'].is_open
        assert repo.load('smoke').state.scene_id == 'tower_entrance'
        assert repo.progress('smoke')['elapsed_seconds'] == 6
        assert len(consultant.calls) == 1
        ctl.start('smoke', 'Pomagam towarzyszowi wyważyć drzwi', turn_id='help')
        assert repo.pending_action('smoke') is None
        assert repo.progress('smoke')['elapsed_seconds'] == 6
        assert repo.consultation('smoke', 'help')['origin'] == 'improviser'
        view = ctl.start('roll_case', 'Sprawdź zasady Atletyki i próbuję wyważyć drzwi', turn_id='roll')
        assert view['interrupts'][0]['payload']['kind'] == 'roll', view
        calls = len(consultant.calls)
        # Nowe instancje repozytorium i kontrolera, odczyt trwałego checkpointu.
        restarted = make_controller(RulesRepository(repo.db_path), consultant)
        view = restarted.view('roll_case', 'roll')
        end = restarted.resume('roll_case', 'roll', view['interrupts'][0]['id'], {'choice':'manual', 'dice':[10]})
        assert end['run']['status'] == 'done'
        assert repo.load('roll_case').state.doors['tower_door'].is_open
        assert len(consultant.calls) == calls
        restarted.resume('roll_case', 'roll', view['interrupts'][0]['id'], {'choice':'manual', 'dice':[10]})
        assert repo.progress('roll_case')['elapsed_seconds'] == 60
        assert len(consultant.calls) == calls
    print('OK: rzeczywisty LangGraph + SQLite + biblioteka zasad + restart oczekującego rzutu.')
    print('Modele są jawnymi atrapami. Nie wykonano API ani testu jakości odpowiedzi prawdziwego LLM.')
    print('Bazy były tymczasowe. Nie zmieniono twojej kampanii.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'TEST NIE PRZESZEDŁ: {type(exc).__name__}: {exc}')
        raise SystemExit(1)
