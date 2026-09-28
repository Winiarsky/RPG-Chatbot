"""Shared offline defaults; individual suites override paths using tmp_path."""
import pytest


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch, tmp_path):
    defaults = {
        'LLM_PROVIDER': 'mock', 'OPENAI_API_KEY': '',
        'OPENAI_MODEL': 'gpt-4.1-mini', 'MAX_HISTORY_TURNS': '12',
        'MAX_INPUT_CHARS': '4000', 'MAX_OUTPUT_TOKENS': '800',
        'REQUEST_TIMEOUT_SECONDS': '45',
        'GAME_DB_PATH': str(tmp_path / 'game.sqlite3'),
        'RAG7_DB_PATH': str(tmp_path / 'library.sqlite3'),
        'RAG7_RULESET_ID': 'dnd_2024_srd_5_2_1',
        'RAG7_TOP_K': '6', 'RAG7_CONTEXT_CHARS': '16000',
        'RAG7_OUTPUT_MODE': 'function_calling', 'RAG7_OUTPUT_TOKENS': '4096',
        'RAG7_EMBEDDING_MODEL': 'text-embedding-3-small', 'RAG7_EMBEDDING_DIMENSIONS': '512',
        'RPG4_INTENT_MODE': 'function_calling', 'RPG5_INTENT_MODE': 'function_calling',
        'RPG7B_SEARCH_MODE': 'lexical', 'RPG7B_GENERATION': 'auto',
        'RPG7B_ALLOW_EMBEDDINGS_API': 'false',
        'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING': 'false',
        'LANGCHAIN_TRACING_V2': 'false',
    }
    for name, value in defaults.items():
        monkeypatch.setenv(name, value)
