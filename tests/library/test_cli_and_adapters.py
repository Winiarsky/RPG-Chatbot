import json
import subprocess
import sys
import os
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
import pytest
from library.settings import ROOT,load_library_settings
from library.models import AnswerDraft
from library.search import Retriever
from library.providers import LangChainWriter


def run_cli(tmp_path,*args):
    env={**os.environ,'RAG7_DB_PATH':str(tmp_path/'cli.sqlite3')}
    return subprocess.run([sys.executable,str(ROOT/'manage_library.py'),*args],cwd=ROOT,
                          text=True,capture_output=True,env=env,timeout=25)


def test_cli_init_search_reopen(tmp_path):
    a=run_cli(tmp_path,'init'); assert a.returncode==0,a.stderr
    b=run_cli(tmp_path,'search','Jak działa przewaga?');assert b.returncode==0,b.stderr
    hits=json.loads(b.stdout); assert hits[0]['chunk']['section_key']=='advantage'
    c=run_cli(tmp_path,'init');assert 'unchanged' in c.stdout


def test_cli_starter_migration_requires_explicit_replace(tmp_path,monkeypatch):
    import library.importers as importers
    from library.store import LibraryStore
    store=LibraryStore(tmp_path/'cli.sqlite3',create=True)
    with monkeypatch.context() as patch:
        patch.setattr(importers,'CHUNKER_VERSION','paragraphs-v1')
        _,old_chunks,_=importers.load_manifest(ROOT/'materials/starter/manifest.yaml')
    store.import_chunks(old_chunks,reviewed=True)
    old_revision=store.documents()[0]['revision']

    result=run_cli(tmp_path,'init')
    assert result.returncode==1 and '--replace' in result.stderr
    assert store.documents()[0]['revision']==old_revision

    result=run_cli(tmp_path,'init','--replace')
    assert result.returncode==0 and 'replaced' in result.stdout
    document=store.documents()[0]
    assert document['revision']!=old_revision
    assert document['reviewed'] is True and document['chunks']==16


def test_cli_hybrid_requires_consent_before_api(tmp_path):
    run_cli(tmp_path,'init')
    result=run_cli(tmp_path,'search','Help','--mode','hybrid')
    assert result.returncode==1 and '--allow-api' in result.stderr


def test_cli_review_requires_confirmation(tmp_path):
    run_cli(tmp_path,'init')
    r=run_cli(tmp_path,'review','srd521_starter','--revoke')
    assert r.returncode==1 and '--confirm' in r.stderr


def test_cli_offline_answer_label(tmp_path):
    run_cli(tmp_path,'init')
    r=run_cli(tmp_path,'ask','Jak działa przewaga?','--json')
    assert r.returncode==0
    assert json.loads(r.stdout)['status']=='retrieved_only'


def test_doctor_no_api_or_secrets(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','sk-test-do-not-show')
    r=run_cli(tmp_path,'doctor')
    assert r.returncode==0 and 'sk-test-do-not-show' not in r.stdout+r.stderr


def test_settings_validation(tmp_path,monkeypatch):
    monkeypatch.setenv('RAG7_EMBEDDING_DIMENSIONS','potato')
    with pytest.raises(ValueError,match='całkowitą'):
        load_library_settings()


def test_json_text_and_tool_mode_send_only_rules(store,cfg):
    payload={'status':'insufficient','claims':[],'unresolved_questions':['Brak szczegółowej reguły.'],'search_query':None}
    class M:
        def __init__(self):self.messages=[];self.method=None
        def with_structured_output(self,schema,method):self.method=method;return self
        def invoke(self,messages):
            self.messages=messages
            return AnswerDraft.model_validate(payload) if self.method else SimpleNamespace(text=json.dumps(payload))
    for mode in ('json_text','function_calling'):
        model=M();writer=LangChainWriter(replace(cfg,output_mode=mode),transport=SimpleNamespace(model=model))
        d=writer.draft('Help',Retriever(store,cfg).search('Help'),cfg.ruleset_id)
        assert d.status=='insufficient'
        packet=json.loads(model.messages[1]['content'])
        assert set(packet)=={'ruleset_id','question','evidence'}
        assert all('text' in e and 'chunk_id' in e for e in packet['evidence'])
        assert 'campaign' not in packet and 'gm_only' not in packet


def test_no_secret_names_in_starter():
    content=(ROOT/'materials/starter/sections.json').read_text(encoding='utf-8')
    assert 'Marta' not in content and 'tower_door' not in content


def test_api_embedding_module_does_not_initialize_client_on_import():
    # Import above succeeded with langchain_openai absent: lazy adapter construction.
    import library.providers as p
    assert hasattr(p,'OpenAIEmbedder')
