"""API library serialization with fake text model; no remote call."""
import pytest
pytest.importorskip('langchain_core')
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from types import SimpleNamespace
from dataclasses import replace
from library.providers import LangChainWriter
from library.search import Retriever


def test_real_langchain_message_adapter(store,cfg):
    model=FakeListChatModel(responses=['{"status":"insufficient","claims":[],"unresolved_questions":["Brak pełnej odpowiedzi."],"search_query":null}'])
    w=LangChainWriter(replace(cfg,output_mode='json_text'),SimpleNamespace(model=model))
    answer=w.draft('Help',Retriever(store,cfg).search('Help'),cfg.ruleset_id)
    assert answer.status=='insufficient'
