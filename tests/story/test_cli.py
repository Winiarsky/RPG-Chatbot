import os
import subprocess
import sys
from .conftest import ROOT


def run(*args):
    return subprocess.run([sys.executable,str(ROOT/'manage_story.py'),*args],cwd=ROOT,
                          capture_output=True,text=True,env=dict(os.environ),timeout=20)


def test_cli_init_idempotent_and_public_output():
    first=run('init')
    assert first.returncode==0,first.stdout+first.stderr
    second=run('init')
    assert second.returncode==0 and 'Nie zresetowano' in second.stdout
    show=run('show')
    assert show.returncode==0
    for private in ['zapieczętowana skrzynia','Starym Moście','gm_notes']:
        assert private not in show.stdout
    assert 'tower_entrance' in show.stdout


def test_cli_template_and_doctor():
    assert run('check-template').returncode==0
    doctor=run('doctor')
    assert doctor.returncode==0
    assert 'Nie wywołano API' in doctor.stdout
