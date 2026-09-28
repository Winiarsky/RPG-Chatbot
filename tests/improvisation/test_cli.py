import os
from pathlib import Path
import subprocess
import sys
from .conftest import ROOT


def call(*args):
    return subprocess.run([sys.executable,'manage_improv.py',*args],cwd=ROOT,
        env=os.environ.copy(),capture_output=True,text=True,timeout=20)


def test_cli_separate_processes():
    for args in [('check-profile',),('init',),('init',),('list',),('show',)]:
        result=call(*args)
        assert result.returncode==0,result.stdout+result.stderr
    assert 'demo6' in call('list').stdout
    assert '"is_open": false' in call('show').stdout


def test_missing_campaign_not_recreated():
    r=call('enable','--campaign','unknown')
    assert r.returncode==1
