from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import pytest
import yaml
from storage import RequestConflict
from mechanics.repository import ActionError
from agents.repository import WorkflowError
from story.repository import StoryRepository, StoryError
from improvisation.repository import ImprovisationRepository, DEFAULT_PROFILE
from .conftest import ROOT, knock


def use_profile(repo,tmp_path,**changes):
    data=yaml.safe_load(DEFAULT_PROFILE.read_text())
    data['signals'][0].update(changes)
    path=tmp_path/'profile.yaml';path.write_text(yaml.safe_dump(data,allow_unicode=True))
    repo.enable('demo',path)


def test_enable_keeps_old_hp_doors_and_history(bare_repo):
    r=bare_repo
    r.set_hp('demo',hp_current=7,expected_revision=r.load('demo').revision,request_id='hp')
    old=StoryRepository(r.db_path)
    old.new_run('demo','old','pukam',4000)
    old.finish_run('demo','old','Stara odpowiedź',expected_revision=old.load('demo').revision)
    state=r.load('demo').state.model_dump()
    assert r.enable('demo')
    assert not r.enable('demo')
    assert state==r.load('demo').state.model_dump()
    assert r.bundle4('demo')[2][1]['content']=='Stara odpowiedź'


def test_enable_rejects_active_older_run(bare_repo):
    old=StoryRepository(bare_repo.db_path)
    old.new_run('demo','old','pukam',4000)
    with pytest.raises(WorkflowError):
        bare_repo.enable('demo')


def test_version_guard(bare_repo):
    old=StoryRepository(bare_repo.db_path)
    old.new_run('demo','old','pukam',4000)
    with pytest.raises(WorkflowError):
        bare_repo.get_run('demo','old')


def test_repeat_commit_is_idempotent(repo):
    first=knock(repo)
    rev=repo.load('demo').revision
    second=repo.commit_story('demo','knock',{'beat_id':None})
    assert first==second and repo.load('demo').revision==rev
    assert repo.progress('demo')['elapsed_seconds']==6
    assert repo.story('demo')[1].npc_memory['marta'].conversations==1
    with pytest.raises(RequestConflict):
        repo.commit_story('demo','knock',{'beat_id':'keeper'})


def test_two_concurrent_commits(repo):
    repo.prepare_action('demo','signal_tower_door',action_id='x',expected_revision=repo.load('demo').revision)
    def commit(_):
        return ImprovisationRepository(repo.db_path).commit_story('demo','x',{'beat_id':None})
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(commit,range(2)))
    assert results[0]['result']==results[1]['result']
    assert repo.progress('demo')['elapsed_seconds']==6


def test_restart_preserves_contact_and_result(repo):
    knock(repo)
    restored=ImprovisationRepository(repo.db_path)
    context,catalog,_,_=restored.bundle4('demo')
    assert context['scene']['contacts'][0]['name']=='Marta'
    assert any(a['definition_id']=='contact_signal_tower_door' for a in catalog)
    assert restored.get_action('demo','knock')['status']=='resolved'


def test_pending_then_hp_change_invalidates(repo):
    repo.prepare_action('demo','signal_tower_door',action_id='x',expected_revision=repo.load('demo').revision)
    repo.set_hp('demo',hp_current=7,expected_revision=repo.load('demo').revision,request_id='hp')
    result=repo.commit_story('demo','x',{'beat_id':None})
    assert result['status']=='invalidated'
    assert not repo.load('demo').state.doors['tower_door'].is_open
    assert repo.progress('demo')['elapsed_seconds']==0


def test_failure_during_event_rolls_back(repo,monkeypatch):
    repo.prepare_action('demo','signal_tower_door',action_id='x',expected_revision=repo.load('demo').revision)
    original=repo._event
    def fail(*args,**kwargs):
        if len(args)>5 and args[5]=='InteractionResolved':
            raise RuntimeError('test')
        return original(*args,**kwargs)
    monkeypatch.setattr(repo,'_event',fail)
    with pytest.raises(RuntimeError):
        repo.commit_story('demo','x',{'beat_id':None})
    assert repo.get_action('demo','x')['status']=='pending'
    assert not repo.load('demo').state.doors['tower_door'].is_open
    assert repo.progress('demo')['elapsed_seconds']==0


@pytest.mark.parametrize('change', [{'can_hear':False},{'response_mode':'silent','responder_id':None}])
def test_no_response_does_not_invent_npc(bare_repo,tmp_path,change):
    use_profile(bare_repo,tmp_path,**change)
    r=knock(bare_repo)
    assert r['result']['npc_reply'] is None
    assert bare_repo.story('demo')[1].npc_memory=={}
    assert not bare_repo.load('demo').state.doors['tower_door'].is_open
    assert bare_repo.bundle4('demo')[0]['scene']['contacts']==[]


def test_answer_without_opening(bare_repo,tmp_path):
    use_profile(bare_repo,tmp_path,response_mode='answer',can_open_from_inside=False)
    r=knock(bare_repo)
    assert r['result']['npc_reply']
    assert not bare_repo.load('demo').state.doors['tower_door'].is_open
    assert bare_repo.bundle4('demo')[0]['scene']['contacts']


def test_loud_prior_actions_cause_caution(repo):
    with repo.connection(write=True) as db:
        db.execute('UPDATE step3_progress SET noise_events=2 WHERE campaign_id=?',('demo',))
    r=knock(repo)
    assert r['result']['npc_reply']
    assert not repo.load('demo').state.doors['tower_door'].is_open
    assert repo.progress('demo')['noise_events']==2


def test_remote_talk_needs_contact(repo):
    with pytest.raises(ActionError):
        repo.prepare_action('demo','contact_signal_tower_door',action_id='talk',expected_revision=repo.load('demo').revision)


def test_remote_talk_reveals_only_allowed_fact_without_move(repo):
    knock(repo)
    repo.prepare_action('demo','contact_signal_tower_door',action_id='talk',expected_revision=repo.load('demo').revision)
    with pytest.raises(StoryError):
        repo.commit_story('demo','talk',{'beat_id':'share_journal'})
    r=repo.commit_story('demo','talk',{'beat_id':'keeper'})
    assert r['result']['new_fact_ids']==['journal_hint']
    assert repo.load('demo').state.scene_id=='tower_entrance'
    assert 'journal_revelation' not in repo.story('demo')[1].known_facts


def test_enter_remains_separate_action_and_hides_remote_contact(repo):
    knock(repo)
    repo.prepare_action('demo','enter_tower',action_id='enter',expected_revision=repo.load('demo').revision)
    repo.commit_story('demo','enter',{'beat_id':None})
    context,catalog,_,_=repo.bundle4('demo')
    assert context['scene']['id']=='vestibule'
    assert context['scene']['contacts']==[]
    assert context['scene']['npcs'][0]['id']=='marta'
    assert any(a['definition_id']=='talk_marta' for a in catalog)


def test_missing_profile_cannot_silently_change_scenario(bare_repo):
    with pytest.raises(StoryError):
        bare_repo.new_run('demo','x','pukam',4000)


def test_incompatible_profile_rejected(bare_repo,tmp_path):
    with pytest.raises(StoryError):
        use_profile(bare_repo,tmp_path,responder_id='ghost')
    assert bare_repo.enabled_campaigns()==[]
