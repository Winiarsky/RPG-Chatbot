import json
import os
import subprocess
import sys
from types import SimpleNamespace
import pytest
from config import Settings
from chat_service import ChatService
from agents.models import Intent
from rules.roles import GameMaster
from rules.models import GMDecision, RuleNeed
from .conftest import ROOT, begin, drive


class FakeModel:
    def __init__(self, response):
        self.response, self.messages, self.schema = response, None, None

    def with_structured_output(self, schema, method):
        assert method == 'function_calling'
        self.schema = schema
        return self

    def invoke(self, messages):
        self.messages = messages
        return self.response


def decision():
    return GMDecision(intent=Intent(kind='chat', definition_id=None, target_id=None, message='Pytanie.'),
        rules=RuleNeed(purpose='question', query='Jak działa przewaga?', required_capabilities=[]))


def test_live_adapter_contract_without_api():
    model = FakeModel(decision())
    transport = ChatService(Settings(provider='openai', api_key='test-not-real'), model=model)
    gm = GameMaster(transport, 'function_calling')
    answer = gm.decide('Jak działa przewaga?', [], {'revision': 1}, [])
    assert answer == decision() and model.schema is GMDecision
    assert 'PUBLIC_STATE' in model.messages[0]['content']
    assert 'help' in model.messages[0]['content']


def test_json_text_adapter_contract_without_api():
    class Model:
        def invoke(self, messages):
            return SimpleNamespace(text=decision().model_dump_json())
    transport = ChatService(Settings(provider='openai', api_key='test-not-real'), model=Model())
    answer = GameMaster(transport, 'json_text').decide('Jak działa przewaga?', [], {'revision': 1}, [])
    assert answer == decision()


@pytest.mark.parametrize('text', ['/ZASADY Jak działa przewaga?', '/rules Jak działa przewaga?', '/zasady\nJak działa przewaga?'])
def test_command_whitespace_and_case(repo, nodes, text):
    state, end = drive(nodes, begin(repo, nodes, text))
    assert state['force_rules_only'] and state['rules_origin'] == 'explicit' and end == 'done'


def test_consulted_action_cannot_be_substituted_with_another_kind(repo, nodes):
    state = begin(repo, nodes, 'Sprawdź zasady Atletyki i próbuję wyważyć drzwi')
    state.update(nodes.interpret(state))
    state.update(nodes.consult_rules(state))
    state['catalog'] += state['context']['improvisation']['interactions']
    state['intent'] = Intent(kind='action', definition_id='signal_tower_door', target_id='tower_door', message='Pukam').model_dump()
    result = nodes.prepare(state)
    assert result['rules_block'] and repo.pending_action('demo') is None


def cli(*args):
    return subprocess.run([sys.executable, str(ROOT/'manage_rules.py'), *args], cwd=ROOT,
        capture_output=True, text=True, timeout=20)


def test_doctor_offline_no_secrets(monkeypatch):
    marker = 'sk-secret-test-never-display'
    monkeypatch.setenv('OPENAI_API_KEY', marker)
    result = cli('doctor')
    assert result.returncode == 0 and marker not in result.stdout + result.stderr
    assert 'Nie wypisano klucza' in result.stdout


def test_cli_enables_existing_campaign(repo, library):
    result = cli('enable', '--campaign', 'demo')
    assert result.returncode == 0 and 'nie zresetowano' in result.stdout


def test_cli_initialization_and_list(library):
    assert cli('init').returncode == 0
    result = cli('list')
    assert result.returncode == 0
    assert json.loads(result.stdout)[0]['campaign_id'] == 'demo7b'


def test_cli_missing_library_does_not_create_campaign(tmp_path):
    result = cli('init')
    assert result.returncode == 1 and 'Brak bazy biblioteki' in result.stdout
    assert not (tmp_path/'game.sqlite3').exists()


def test_cli_capabilities_no_game_creation(tmp_path):
    result = cli('capabilities')
    assert result.returncode == 0 and 'ability_check' in result.stdout
    assert not (tmp_path/'game.sqlite3').exists()


@pytest.mark.parametrize('module', ['manage_rules', 'manage_library'])
def test_doctor_reports_missing_dependency(monkeypatch, capsys, module):
    import importlib
    import manage_rules

    original = manage_rules.metadata.version

    def version(package):
        if package == 'pypdf':
            raise manage_rules.metadata.PackageNotFoundError(package)
        return original(package)

    monkeypatch.setattr(manage_rules.metadata, 'version', version)
    assert importlib.import_module(module).main(['doctor']) == 1
    assert 'pypdf: BRAK' in capsys.readouterr().out


def test_cli_backup_preserves_campaign(repo, tmp_path, monkeypatch):
    # Recovery must work even when the optional RAG configuration is broken.
    monkeypatch.setenv('RPG7B_SEARCH_MODE', 'invalid')
    import sqlite3

    before = repo.load('demo')
    result = cli('backup')
    assert result.returncode == 0
    backups = list((tmp_path / 'backups').glob('*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT COUNT(*) FROM campaigns').fetchone()[0] == 1
    assert repo.load('demo') == before
