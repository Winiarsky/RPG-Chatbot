import pytest
st=pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest
from library7a.settings import ROOT
from library7a.store import LibraryStore
from library7a.importers import load_manifest


def test_offline_ui(tmp_path,monkeypatch):
    db=tmp_path/'ui.sqlite3'
    store=LibraryStore(db,create=True)
    _,cs,_=load_manifest(ROOT/'materials7a/starter/manifest.yaml')
    store.import_chunks(cs,reviewed=True)
    monkeypatch.setenv('RAG7_DB_PATH',str(db))
    app=AppTest.from_file(str(ROOT/'library_app.py')).run(timeout=20)
    assert not app.exception
    app.button[0].click().run(timeout=20)
    assert not app.exception
    assert any('retrieved_only' in v.value for v in app.subheader)
