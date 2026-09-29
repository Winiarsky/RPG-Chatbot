from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import pytest
from storage import GameRepository, RequestConflict, RevisionConflict, StorageError
from mechanics.repository import ActionRepository, ActionError
from manage_checks import backup_database
from .conftest import prepare


def resolve(repo, die=10, action_id='try1'):
    return repo.resolve_action('demo', action_id, source='manual', dice=[die])


def test_additive_tables_and_old_client_compatibility(repo):
    old = GameRepository(repo.db_path)
    assert old.load('demo').state.character.hp_current == 12
    with old.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_prepare_does_not_change_world_and_survives_new_instance(repo):
    before = repo.load('demo').state
    pending = prepare(repo)
    assert pending['status'] == 'pending' and pending['plan']['bonus'] == 5
    assert repo.load('demo').state == before
    other = ActionRepository(repo.db_path)
    assert other.pending_action('demo')['action_id'] == 'try1'


def test_idempotent_prepare(repo):
    first = prepare(repo)
    revision = repo.load('demo').revision
    repeated = repo.prepare_action('demo', 'force_door', action_id='try1', expected_revision=0)
    assert repeated == first and repo.load('demo').revision == revision
    with pytest.raises(RequestConflict):
        prepare(repo, definition_id='observe_scene')


def test_one_pending_per_campaign(repo):
    prepare(repo)
    with pytest.raises(ActionError):
        prepare(repo, 'try2')


def test_wrong_revision(repo):
    with pytest.raises(RevisionConflict):
        repo.prepare_action('demo', 'force_door', action_id='bad', expected_revision=-1)


def test_success_commits_everything_once(repo):
    prepare(repo)
    result = resolve(repo)
    assert result['status'] == 'resolved' and result['result']['check']['success']
    assert result['result']['check']['total'] == 15
    assert repo.load('demo').state.doors['tower_door'].is_open
    assert repo.progress('demo') == {'elapsed_seconds': 60, 'noise_events': 1}
    assert len(repo.read_bundle('demo')[1]) == 1
    rev = repo.load('demo').revision
    assert resolve(repo) == result
    assert repo.load('demo').revision == rev
    assert len([e for e in repo.events('demo') if e['kind'] == 'AbilityCheckResolved']) == 1


def test_failure_records_cost_and_can_retry_new_action(repo):
    prepare(repo)
    assert not resolve(repo, 9)['result']['check']['success']
    assert not repo.load('demo').state.doors['tower_door'].is_open
    prepare(repo, 'try2')
    assert resolve(repo, 10, 'try2')['result']['check']['success']
    assert repo.progress('demo')['elapsed_seconds'] == 120


def test_result_cannot_be_replaced(repo):
    prepare(repo)
    resolve(repo, 9)
    with pytest.raises(RequestConflict):
        resolve(repo, 20)
    with pytest.raises(RequestConflict):
        repo.resolve_action('demo', 'try1', source='app')


def test_replay_does_not_reapply_state_change(repo):
    prepare(repo)
    resolve(repo)
    repo.set_door('demo', door_id='tower_door', is_open=False,
                  expected_revision=repo.load('demo').revision, request_id='admin-close')
    resolve(repo)
    assert not repo.load('demo').state.doors['tower_door'].is_open


def test_roll_app_replay_does_not_randomize(repo):
    prepare(repo)
    calls = []
    def roller():
        calls.append(1)
        return 14
    first = repo.resolve_action('demo', 'try1', source='app', roller=roller)
    second = repo.resolve_action('demo', 'try1', source='app', roller=roller)
    assert first == second and len(calls) == 1


def test_invalid_manual_result_keeps_request_pending(repo):
    prepare(repo)
    with pytest.raises(ValueError):
        resolve(repo, 21)
    with pytest.raises(ValueError):
        repo.resolve_action('demo', 'try1', source='manual', dice=[10, 12])
    assert repo.pending_action('demo') is not None
    assert repo.progress('demo')['elapsed_seconds'] == 0


def test_app_cannot_accept_supplied_dice(repo):
    prepare(repo)
    with pytest.raises(ValueError):
        repo.resolve_action('demo', 'try1', source='app', dice=[20])


def test_state_change_invalidates_before_drawing(repo):
    prepare(repo)
    repo.set_hp('demo', hp_current=7, expected_revision=repo.load('demo').revision, request_id='hp')
    calls = []
    result = repo.resolve_action('demo', 'try1', source='app', roller=lambda: calls.append(1) or 20)
    assert result['status'] == 'invalidated' and calls == []
    assert repo.pending_action('demo') is None
    assert repo.progress('demo')['elapsed_seconds'] == 0
    assert not repo.load('demo').state.doors['tower_door'].is_open


def test_chat_does_not_invalidate_world_fingerprint(repo):
    prepare(repo)
    repo.append_turn('demo', request_id='chat', expected_revision=repo.load('demo').revision,
                     user_text='Hej', assistant_text='Witaj')
    assert resolve(repo)['status'] == 'resolved'


def test_cancel_is_idempotent_and_has_no_cost(repo):
    prepare(repo)
    first = repo.cancel_action('demo', 'try1')
    revision = repo.load('demo').revision
    assert repo.cancel_action('demo', 'try1') == first
    assert repo.load('demo').revision == revision
    assert repo.progress('demo')['elapsed_seconds'] == 0
    with pytest.raises(ActionError):
        resolve(repo)
    prepare(repo, 'try2')


def test_no_roll_observe_does_not_reveal_secret(repo):
    result = prepare(repo, definition_id='observe_scene')
    assert result['status'] == 'resolved' and result['plan'] is None
    assert result['result']['check'] is None
    assert 'mosiężny klucz' not in result['public_text']
    assert 'kruka' not in result['public_text']
    assert not repo.pending_action('demo')
    assert repo.progress('demo')['elapsed_seconds'] == 0


def test_open_door_rejects_new_test(repo):
    prepare(repo)
    resolve(repo)
    with pytest.raises(ActionError):
        prepare(repo, 'unnecessary')


def test_zero_hp_rejected(repo):
    repo.set_hp('demo', hp_current=0, expected_revision=repo.load('demo').revision, request_id='down')
    with pytest.raises(ActionError):
        prepare(repo)


def test_pack_is_pinned_and_not_reread(repo, tmp_path):
    original = repo.pack('demo')
    assert repo.ensure_pack('demo', tmp_path / 'missing.yaml') == original


def test_atomic_rollback_after_world_update(repo, monkeypatch):
    prepare(repo)
    revision = repo.load('demo').revision
    def fail(*args, **kwargs):
        raise RuntimeError('Simulated write failure')
    monkeypatch.setattr(repo, '_event', fail)
    with pytest.raises(RuntimeError):
        resolve(repo)
    assert repo.load('demo').revision == revision
    assert not repo.load('demo').state.doors['tower_door'].is_open
    assert repo.pending_action('demo')['status'] == 'pending'
    assert repo.progress('demo')['elapsed_seconds'] == 0
    assert repo.read_bundle('demo')[1] == []


def test_two_concurrent_resolutions_draw_only_once(repo):
    prepare(repo)
    calls = []
    def work(_):
        return repo.resolve_action('demo', 'try1', source='app', roller=lambda: calls.append(1) or 15)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(work, [1, 2]))
    assert results[0] == results[1] and len(calls) == 1
    assert repo.progress('demo')['elapsed_seconds'] == 60


def test_campaign_isolation(repo):
    snap = repo.load('demo')
    repo.create('other', 'Other', snap.state.character, snap.scenario)
    repo.ensure_pack('other')
    prepare(repo)
    with pytest.raises(ActionError):
        repo.resolve_action('other', 'try1', source='manual', dice=[20])
    assert repo.pending_action('demo') is not None


def test_backup_preserves_pending_and_does_not_overwrite(repo):
    prepare(repo)
    copy1 = backup_database(repo.db_path)
    copy2 = backup_database(repo.db_path)
    assert copy1 != copy2 and copy1.exists() and copy2.exists()
    backup = ActionRepository(copy1)
    assert backup.pending_action('demo')['action_id'] == 'try1'
    with backup.connection() as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('identifier', ['', '../../evil', 'a' * 65, 'bad id'])
def test_invalid_action_id(repo, identifier):
    with pytest.raises(ValueError):
        prepare(repo, identifier)


def test_concurrent_preparation_only_one_pending(repo):
    revision = repo.load('demo').revision
    def work(action_id):
        try:
            return repo.prepare_action('demo', 'force_door', action_id=action_id, expected_revision=revision)['status']
        except StorageError:
            return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(work, ['a1', 'a2']))
    assert sorted(results) == ['pending', 'rejected']
    assert len(repo.recent_actions('demo')) == 1


def test_observation_after_success_uses_new_description(repo):
    prepare(repo)
    resolve(repo)
    result = prepare(repo, 'look', 'observe_scene')
    assert 'przedsionek' in result['public_text']
    assert 'mosiężny klucz' not in result['public_text']


def test_advantage_pack_is_executable(repo, tmp_path):
    import yaml
    snap = repo.load('demo')
    repo.create('adv', 'Advantage', snap.state.character, snap.scenario)
    data = repo.pack('demo').model_dump(mode='json')
    data['actions'][0]['check']['advantage_sources'] = ['Testowe rozstrzygnięcie MG']
    path = tmp_path / 'adv.yaml'
    path.write_text(yaml.safe_dump(data), encoding='utf-8')
    repo.ensure_pack('adv', path)
    repo.prepare_action('adv', 'force_door', action_id='test', expected_revision=repo.load('adv').revision)
    result = repo.resolve_action('adv', 'test', source='manual', dice=[2, 15])
    assert result['result']['check']['selected'] == 15
    assert result['result']['check']['total'] == 20


def test_wrong_pack_does_not_change_existing_state(repo, tmp_path):
    import yaml
    snap = repo.load('demo')
    repo.create('other', 'Other', snap.state.character, snap.scenario)
    data = repo.pack('demo').model_dump(mode='json')
    data['scenario_id'] = 'wrong'
    path = tmp_path / 'wrong.yaml'
    path.write_text(yaml.safe_dump(data), encoding='utf-8')
    with pytest.raises(ActionError):
        repo.ensure_pack('other', path)
    assert repo.load('other').revision == 0


def test_backup_before_migration_does_not_touch_original_schema(repo, tmp_path):
    snap = repo.load('demo')
    old = GameRepository(tmp_path / 'old.sqlite3')
    old.create('original', 'Original', snap.state.character, snap.scenario)
    copy_path = backup_database(old.db_path)
    with old.connection() as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert not any(name.startswith('step3_') for name in tables)
    assert GameRepository(copy_path).load('original').state == snap.state


def test_future_extension_version_is_rejected(repo):
    with repo.connection(write=True) as db:
        db.execute('UPDATE step3_meta SET version=999')
    with pytest.raises(ActionError):
        ActionRepository(repo.db_path)
    with repo.connection() as db:
        assert db.execute('SELECT version FROM step3_meta').fetchone()[0] == 999
