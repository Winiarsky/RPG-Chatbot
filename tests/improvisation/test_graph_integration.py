import pytest
pytest.importorskip('langgraph.graph')
pytest.importorskip('langgraph.checkpoint.sqlite')
from .smoke import main
from improv6.runtime import RecoveryController
from .conftest import knock


def test_real_graph_and_restarts():
    assert main()==0


def test_world_change_before_remote_talk_confirmation_invalidates_action(repo, nodes):
    knock(repo)
    controller = RecoveryController(repo, nodes.gm, nodes.narrator, nodes.keeper, nodes.improviser)
    waiting = controller.start('demo', 'Pytam Martę o latarnika', turn_id='talk')
    before = repo.progress('demo'), repo.story('demo')[1]
    repo.set_hp('demo', hp_current=0, expected_revision=repo.load('demo').revision, request_id='hp')
    done = controller.resume('demo', 'talk', waiting['interrupts'][0]['id'], {'choice': 'confirm'})
    assert done['run']['status'] == 'done'
    assert done['action']['status'] == 'invalidated'
    assert (repo.progress('demo'), repo.story('demo')[1]) == before
    assert repo.pending_action('demo') is None
    assert repo.load('demo').state.character.hp_current == 0
