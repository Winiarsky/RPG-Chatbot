from pathlib import Path
import sys
import pytest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from config import Settings
from chat_service import ChatService
from library7a.settings import LibrarySettings
from library7a.store import LibraryStore
from library7a.importers import load_manifest
from rules7b.repository import RulesRepository
from rules7b.nodes import RulesNodes
from rules7b.roles import GameMaster, Improviser
from story5.roles import StoryKeeper
from improv6.roles import Narrator
from smoke import TestConsultant as Consultant


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for key, value in {'LLM_PROVIDER': 'mock', 'OPENAI_API_KEY': '',
        'GAME_DB_PATH': str(tmp_path/'game.sqlite3'),
        'GAME_CHARACTER_PATH': str(ROOT/'characters/torin.yaml'), 'RAG7_DB_PATH': str(tmp_path/'library.sqlite3'),
        'RPG7B_SEARCH_MODE': 'lexical', 'RPG7B_GENERATION': 'auto', 'RPG7B_ALLOW_EMBEDDINGS_API': 'false',
        'RAG7_RULESET_ID': 'dnd_2024_srd_5_2_1', 'RPG5_INTENT_MODE': 'function_calling',
        'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'}.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def library(tmp_path):
    cfg = LibrarySettings(db_path=tmp_path/'library.sqlite3')
    store = LibraryStore(cfg.db_path, create=True)
    _, chunks, _ = load_manifest(ROOT/'materials7a/starter/manifest.yaml')
    store.import_chunks(chunks, reviewed=True)
    return store, cfg


@pytest.fixture
def repo(tmp_path):
    r = RulesRepository(tmp_path/'game.sqlite3')
    r.initialize()
    r.create_story('demo', 'Próba', ROOT/'characters/torin.yaml')
    r.enable('demo')
    r.enable_rules('demo', 'dnd_2024_srd_5_2_1')
    return r


@pytest.fixture
def consultant(library):
    return Consultant(*library)


@pytest.fixture
def nodes(repo, consultant):
    transport = ChatService(Settings(provider='mock'))
    return RulesNodes(repo, GameMaster(transport), Narrator(transport), StoryKeeper(transport),
        Improviser(transport), consultant)


def begin(repo, nodes, text, tid='turn'):
    repo.new_run('demo', tid, text, 4000)
    state = {'campaign_id': 'demo', 'turn_id': tid, 'user_text': text}
    state.update(nodes.load_context(state))
    return state


def drive(nodes, state, start='interpret'):
    """Testuje węzły i routing, nie udaje wykonania rzeczywistego LangGraph."""
    from rules7b.routing import after_intent, after_recovery, after_consult, after_prepared
    routers = {'interpret': after_intent, 'recover': after_recovery, 'consult_rules': after_consult,
        'prepare': after_prepared, 'reconsider': lambda s: s['rules_after'],
        'narrate': lambda s: 'await_narrator' if s.get('narration_error') else 'finish',
        'resolve': lambda s: 'narrate' if s['action']['status'] == 'resolved' else 'fixed_reply',
        'commit_story': lambda s: 'narrate' if s['action']['status'] == 'resolved' else 'fixed_reply'}
    edges = {'rules_reply': 'finish', 'fixed_reply': 'finish', 'fallback': 'finish',
        'plan_story': 'commit_story', 'stop_rules': 'rules_reply'}
    current = start
    for _ in range(40):
        if current.startswith('await_'):
            return state, current
        state.update(getattr(nodes, current)(state))
        if current == 'finish':
            return state, 'done'
        current = routers[current](state) if current in routers else edges[current]
    raise AssertionError('Pętla w teście węzłów')
