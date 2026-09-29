import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from pydantic import ValidationError
from schemas import CharacterState, ScenarioDefinition
from scenario_loader import load_yaml
from story.models import StoryBook, BeatSelection
from story.repository import StoryRepository, StoryError, DEFAULT_STORY
from mechanics.repository import ActionError
from agents.repository import GraphRepository, WorkflowError
from storage import RequestConflict, RevisionConflict
from .conftest import ROOT, act, open_and_enter


def test_initial_public_context_excludes_unrevealed_facts_and_other_scenes(repo):
    context,catalog,_,_=repo.bundle4('demo5')
    text=json.dumps([context,catalog],ensure_ascii=False)
    for secret in ('zapieczętowana skrzynia','Starym Moście','Kos, przewoźnik','journal_revelation','gm_notes','allowed_beats','share_journal'):
        assert secret not in text
    assert context['scene']['npcs']==[]
    assert context['story']['known_facts']==[]
    assert not next(a for a in catalog if a['definition_id']=='enter_tower')['available']
    assert not any(a['definition_id']=='read_journal' for a in catalog)


def test_cannot_walk_through_closed_door(repo):
    before=repo.load('demo5')
    with pytest.raises(ActionError):
        act(repo,'enter_tower','blocked')
    assert repo.load('demo5')==before
    assert repo.pending_action('demo5') is None


def test_move_prepare_is_not_execution_and_cancel_has_no_cost(repo):
    repo.set_door('demo5',door_id='tower_door',is_open=True,expected_revision=0,request_id='open')
    a=repo.prepare_action('demo5','enter_tower',action_id='move',expected_revision=1)
    assert a['status']=='pending' and a['plan'] is None
    assert repo.load('demo5').state.scene_id=='tower_entrance'
    assert 'reveals' not in a['definition']
    repo.cancel_action('demo5','move')
    assert repo.load('demo5').state.scene_id=='tower_entrance'
    assert repo.progress('demo5')['elapsed_seconds']==0
    assert repo.cancel_action('demo5','move')['status']=='cancelled'


def test_move_preserves_hp_and_character(repo):
    before=repo.load('demo5').state.character
    open_and_enter(repo)
    assert repo.load('demo5').state.scene_id=='vestibule'
    assert repo.load('demo5').state.character==before
    assert repo.progress('demo5')['elapsed_seconds']==10
    assert repo.story('demo5')[1].visited_scenes==['tower_entrance','vestibule']


def test_restart_reads_same_world_and_pending_action(repo):
    open_and_enter(repo)
    repo.prepare_action('demo5','go_archive',action_id='up',expected_revision=repo.load('demo5').revision)
    fresh=StoryRepository(repo.db_path)
    fresh.initialize()
    assert fresh.pending_action('demo5')['action_id']=='up'
    fresh.commit_story('demo5','up',{'beat_id':None})
    assert StoryRepository(repo.db_path).load('demo5').state.scene_id=='archive'


def test_remote_npc_cannot_be_contacted(repo):
    with pytest.raises(ActionError):
        act(repo,'talk_marta','remote','keeper')
    assert not repo.story('demo5')[1].npc_memory


def test_first_and_repeat_greeting(repo):
    open_and_enter(repo)
    first=act(repo,'talk_marta','first','hello_first')
    assert 'Jestem Marta' in first['public_text']
    second=act(repo,'talk_marta','second','hello_again')
    assert 'Pamiętam' in second['public_text']
    memory=repo.story('demo5')[1].npc_memory['marta']
    assert memory.conversations==2
    assert memory.discussed_beats==['hello_first','hello_again']


def test_outdated_greeting_not_eligible(repo):
    open_and_enter(repo)
    act(repo,'talk_marta','first','hello_first')
    with pytest.raises(StoryError):
        act(repo,'talk_marta','repeat','hello_first')
    assert repo.story('demo5')[1].npc_memory['marta'].conversations==1


def test_keeper_cannot_reveal_unknown_journal(repo):
    open_and_enter(repo)
    with pytest.raises(StoryError):
        act(repo,'talk_marta','attack','share_journal')
    assert repo.story('demo5')[1].known_facts==[]
    assert repo.story('demo5')[1].npc_memory=={}
    assert repo.progress('demo5')['elapsed_seconds']==10
    assert repo.pending_action('demo5')['status']=='pending'


def test_inspection_reveals_once_and_replay_does_not_cost(repo):
    open_and_enter(repo)
    act(repo,'go_archive','up')
    first=act(repo,'read_journal','read')
    assert first['result']['new_fact_ids']==['journal_revelation']
    elapsed=repo.progress('demo5')['elapsed_seconds']
    again=repo.commit_story('demo5','read',{'beat_id':None})
    assert again==first and repo.progress('demo5')['elapsed_seconds']==elapsed
    second=act(repo,'read_journal','reread')
    assert second['result']['new_fact_ids']==[]
    assert repo.story('demo5')[1].known_facts==['journal_revelation']
    with pytest.raises(RequestConflict):
        repo.commit_story('demo5','read',{'beat_id':'another'})


def test_observe_does_not_read_journal(repo):
    open_and_enter(repo)
    act(repo,'go_archive','up')
    a=repo.prepare_action('demo5','observe_archive',action_id='look',expected_revision=repo.load('demo5').revision)
    assert a['status']=='resolved'
    assert repo.story('demo5')[1].known_facts==[]
    context,catalog,_,_=repo.bundle4('demo5')
    assert 'Starym Moście' not in json.dumps([context,catalog],ensure_ascii=False)


def test_clue_branch_needs_actual_discovery(repo):
    open_and_enter(repo)
    act(repo,'talk_marta','hint','keeper')
    act(repo,'go_archive','up')
    act(repo,'read_journal','read')
    act(repo,'return_vestibule','back')
    answer=act(repo,'talk_marta','tell','share_journal')
    assert 'Kos' in answer['public_text']
    assert repo.story('demo5')[1].known_facts==['journal_hint','journal_revelation','ferryman_mark']
    assert repo.story('demo5')[1].npc_memory['marta'].conversations==2


def test_archive_does_not_require_talking(repo):
    open_and_enter(repo)
    act(repo,'go_archive','up')
    act(repo,'read_journal','read')
    assert repo.story('demo5')[1].known_facts==['journal_revelation']
    assert repo.story('demo5')[1].npc_memory=={}


@pytest.mark.parametrize('bad', ['missing', 'hello_again', 'share_journal'])
def test_rejects_ineligible_beat(repo,bad):
    open_and_enter(repo)
    with pytest.raises(StoryError):
        act(repo,'talk_marta','bad',bad)
    assert not repo.story('demo5')[1].known_facts


def test_world_change_invalidates_pending_story(repo):
    open_and_enter(repo)
    repo.prepare_action('demo5','go_archive',action_id='up',expected_revision=repo.load('demo5').revision)
    repo.set_hp('demo5',hp_current=7,expected_revision=repo.load('demo5').revision,request_id='hp')
    a=repo.commit_story('demo5','up',{'beat_id':None})
    assert a['status']=='invalidated'
    assert repo.load('demo5').state.scene_id=='vestibule'
    assert repo.progress('demo5')['elapsed_seconds']==10


def test_plain_chat_does_not_invalidate_story(repo):
    open_and_enter(repo)
    repo.prepare_action('demo5','go_archive',action_id='up',expected_revision=repo.load('demo5').revision)
    repo.append_turn('demo5',request_id='chat',expected_revision=repo.load('demo5').revision,user_text='HP?',assistant_text='12')
    assert repo.commit_story('demo5','up',{'beat_id':None})['status']=='resolved'


def test_rollback_world_knowledge_memory_and_time(repo,monkeypatch):
    open_and_enter(repo)
    repo.prepare_action('demo5','talk_marta',action_id='talk',expected_revision=repo.load('demo5').revision)
    before=repo.load('demo5')
    old_event=repo._event
    def crash(*args,**kwargs):
        raise RuntimeError('symulacja awarii w transakcji')
    monkeypatch.setattr(repo,'_event',crash)
    with pytest.raises(RuntimeError):
        repo.commit_story('demo5','talk',{'beat_id':'keeper'})
    assert repo.load('demo5')==before
    assert repo.progress('demo5')['elapsed_seconds']==10
    assert repo.story('demo5')[1].known_facts==[]
    assert repo.story('demo5')[1].npc_memory=={}
    monkeypatch.setattr(repo,'_event',old_event)
    assert repo.commit_story('demo5','talk',{'beat_id':'keeper'})['status']=='resolved'


def test_concurrent_double_commit_only_once(repo):
    open_and_enter(repo)
    repo.prepare_action('demo5','talk_marta',action_id='talk',expected_revision=repo.load('demo5').revision)
    def worker(_):
        return StoryRepository(repo.db_path).commit_story('demo5','talk',{'beat_id':'keeper'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(worker,range(2)))
    assert results[0]==results[1]
    assert repo.story('demo5')[1].npc_memory['marta'].conversations==1
    assert repo.progress('demo5')['elapsed_seconds']==40


def test_pack_is_pinned_and_new_campaign_does_not_overwrite(repo,tmp_path):
    book, _=repo.story('demo5')
    with pytest.raises(StoryError):
        repo.create_story('demo5','Overwrite',ROOT/'characters/torin.yaml')
    assert repo.story('demo5')[0]==book
    assert repo.ensure_pack('demo5',tmp_path/'does_not_exist.yaml')==book.mechanics


def test_old_campaign_unmodified_and_not_in_story_list(repo):
    char=load_yaml(ROOT/'characters/torin.yaml',CharacterState)
    scenario=load_yaml(ROOT/'scenarios/tower.yaml',ScenarioDefinition)
    repo.create('old','Old save',char,scenario)
    before=repo.load('old')
    with pytest.raises(StoryError):
        repo.ensure_pack('old')
    assert repo.load('old')==before
    assert [c['campaign_id'] for c in repo.story_campaigns()]==['demo5']


def test_graph_version_and_single_active_declaration(repo):
    repo.new_run('demo5','turn','Rozglądam się',4000)
    assert repo.get_run('demo5','turn')['graph_version']=='step5_v1'
    assert repo.new_run('demo5','turn','Rozglądam się',4000)['turn_id']=='turn'
    with pytest.raises(RequestConflict):
        repo.new_run('demo5','turn','Inne',4000)
    with pytest.raises(WorkflowError):
        repo.new_run('demo5','second','Rozglądam się',4000)
    with pytest.raises(WorkflowError):
        GraphRepository(repo.db_path).get_run('demo5','turn')


def test_story_action_id_collision_with_mechanics(repo):
    repo.prepare_action('demo5','observe_entrance',action_id='same',expected_revision=0)
    repo.set_door('demo5',door_id='tower_door',is_open=True,expected_revision=repo.load('demo5').revision,request_id='open')
    with pytest.raises(RequestConflict):
        repo.prepare_action('demo5','enter_tower',action_id='same',expected_revision=repo.load('demo5').revision)


@pytest.mark.parametrize('changes', [
    {'beat_id':'keeper','reveals':['journal_revelation']},
    {'beat_id':'keeper','hp':100}, {'beat_id':15}])
def test_keeper_contract_cannot_patch_world(changes):
    with pytest.raises(ValidationError):
        BeatSelection.model_validate(changes)


def test_template_references_rejected():
    book=load_yaml(DEFAULT_STORY,StoryBook).model_dump()
    book['actions'][0]['target_id']='invented_scene'
    with pytest.raises(ValidationError):
        StoryBook.model_validate(book)


def test_noise_branch_reads_mechanics_progress(repo):
    for tid,die in [('failure',9),('success',10)]:
        repo.prepare_action('demo5','force_door',action_id=tid,expected_revision=repo.load('demo5').revision)
        repo.resolve_action('demo5',tid,source='manual',dice=[die])
    act(repo,'enter_tower','enter')
    result=act(repo,'talk_marta','noise','noise_loud')
    assert 'łomot' in result['public_text']
    assert repo.progress('demo5')['noise_events']==2


@pytest.mark.parametrize('source', ['action', 'beat'])
def test_template_rejects_duplicate_revealed_facts(source):
    book = load_yaml(DEFAULT_STORY, StoryBook).model_dump()
    if source == 'action':
        entry = next(a for a in book['actions'] if a['reveals'])
    else:
        entry = next(b for n in book['npcs'] for b in n['beats'] if b['reveals'])
    entry['reveals'] = [entry['reveals'][0], entry['reveals'][0]]
    with pytest.raises(ValidationError, match='tego samego faktu'):
        StoryBook.model_validate(book)
