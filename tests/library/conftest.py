import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import pytest
from library.settings import LibrarySettings,ROOT
from library.store import LibraryStore
from library.importers import load_manifest

@pytest.fixture
def cfg(tmp_path):
    return LibrarySettings(db_path=tmp_path/'library.sqlite3',dimensions=64)

@pytest.fixture
def store(cfg):
    s=LibraryStore(cfg.db_path,create=True)
    _,cs,_=load_manifest(ROOT/'materials/starter/manifest.yaml')
    s.import_chunks(cs,reviewed=True)
    return s
