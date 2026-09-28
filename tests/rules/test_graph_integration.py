"""Oddzielne prawdziwe wykonanie grafu; bez biblioteki nie udaje wyniku pozytywnego."""
import pytest
pytest.importorskip('langgraph.graph')
pytest.importorskip('langgraph.checkpoint.sqlite')
from smoke import main


def test_real_graph_smoke():
    assert main() == 0
