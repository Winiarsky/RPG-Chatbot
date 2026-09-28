import json
import os
import subprocess
import sys

from mechanics3.chat import public_context3
from mechanics3.repository import ActionRepository
from .conftest import ROOT, prepare


def test_secret_filter_and_pending_context(repo):
    prepare(repo)
    snap, _, mechanics = repo.context_bundle('demo')
    context = public_context3(snap, mechanics)
    content = json.dumps(context, ensure_ascii=False)
    assert 'gm_only' not in content and 'mosiężny klucz' not in content
    assert context['mechanics']['pending_roll']['plan']['bonus'] == 5


def test_cli_separate_processes(repo):
    def run(*args):
        result = subprocess.run([sys.executable, 'manage_checks.py', *args], cwd=ROOT,
                                capture_output=True, text=True, env=os.environ.copy(), timeout=20)
        assert result.returncode == 0, result.stderr + result.stdout
        return result.stdout
    run('prepare', 'force_door', '--action-id', 'process-test')
    assert json.loads(run('show'))['pending']['action_id'] == 'process-test'
    result = json.loads(run('resolve', 'process-test', '--manual', '10'))
    assert result['result']['check']['total'] == 15
    repeated = json.loads(run('resolve', 'process-test', '--manual', '10'))
    assert repeated == result
    assert ActionRepository(repo.db_path).progress('demo')['elapsed_seconds'] == 60
