import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import pytest
from agents.models import Intent
from agents.repository import WorkflowError
from improvisation.repository import ImprovisationRepository
from library.models import LibraryAnswer
from library.store import LibraryStore
from rules.models import RuleNeed, GMDecision
from rules.settings import IntegrationSettings
from rules.library import verify_answer, LocalConsultant
from rules.repository import RulesRepository
from rules.runtime import validate_rules_resume
from rules.capabilities import missing
from .conftest import ROOT, begin, drive


def need(query='Jak działa przewaga?'):
    return RuleNeed(purpose='question', query=query, required_capabilities=[])


@pytest.mark.parametrize('payload', [
    {'purpose':'question','query':'Przewaga','required_capabilities':['help']},
    {'purpose':'action','query':'Help','required_capabilities':[]},
    {'purpose':'action','query':'Help','required_capabilities':['help','help']},
    {'purpose':'question','query':'abc','required_capabilities':[],'hp':100},
    {'purpose':'action','query':'abc','required_capabilities':['ability_check'],'dc':1},
])
def test_bad_requests_cannot_smuggle_effects(payload):
    with pytest.raises(ValueError):
        RuleNeed.model_validate(payload)


def test_question_cannot_contain_action_intent():
    with pytest.raises(ValueError):
        GMDecision(intent=Intent(kind='action',definition_id='force_door',target_id='tower_door',message='Wyważam'),rules=need())


def test_unknown_capability_is_blocked():
    assert missing(['time_travel']) == ['time_travel']
    assert missing(['ability_check'], 'force_door') == []
    assert missing(['help'], 'force_door') == ['help']
    assert missing(['ability_check'], 'talk') == ['ability_check']


@pytest.mark.parametrize('option', [{'search_mode':'hybrid'}, {'generation':'on'}, {'search_mode':'cloud'}])
def test_configuration_fails_closed(option):
    with pytest.raises(ValueError):
        IntegrationSettings(**option)


def test_embedding_consent_explicit():
    assert IntegrationSettings(search_mode='hybrid', allow_embeddings_api=True).search_mode == 'hybrid'
    assert not IntegrationSettings(generation='off').generate('openai')
    assert not IntegrationSettings().generate('mock')


def test_local_consultant_offline(library):
    store, cfg = library
    c = LocalConsultant(cfg, IntegrationSettings(), 'mock')
    assert c.consult('Jak działa przewaga?', cfg.ruleset_id).status == 'retrieved_only'
    with pytest.raises(ValueError, match='nie zgadza'):
        c.consult('Jak działa przewaga?', 'dnd_2014')


def test_missing_library_is_not_created(tmp_path):
    from library.settings import LibrarySettings
    cfg = LibrarySettings(db_path=tmp_path/'missing.sqlite3')
    c = LocalConsultant(cfg, IntegrationSettings(), 'mock')
    with pytest.raises(ValueError, match='Brak bazy'):
        c.consult('przewaga', cfg.ruleset_id)
    assert not cfg.db_path.exists()


def test_library_refuses_campaign_database(repo):
    with pytest.raises(ValueError, match='nie baza biblioteki'):
        LibraryStore(repo.db_path)


def test_wrong_ruleset_answer_is_rejected(consultant):
    a = consultant.consult('Jak działa przewaga?', 'dnd_2024_srd_5_2_1').model_dump(mode='json')
    a['ruleset_id'] = 'dnd_2014'
    with pytest.raises(ValueError):
        verify_answer(a, 'Jak działa przewaga?', 'dnd_2024_srd_5_2_1')


def test_fabricated_quote_is_rejected(consultant):
    a = consultant.consult('Jak działa przewaga?', 'dnd_2024_srd_5_2_1').model_dump(mode='json')
    a['claims'][0]['evidence'][0]['quote'] = 'Ta nieistniejąca reguła dodaje 100 do każdego testu.'
    with pytest.raises(ValueError):
        verify_answer(a, a['question'], a['ruleset_id'])


def test_fabricated_citation_metadata_is_rejected(consultant):
    a = consultant.consult('Jak działa przewaga?', 'dnd_2024_srd_5_2_1').model_dump(mode='json')
    a['citations'][0]['chunk']['pdf_page_start'] = 9999
    with pytest.raises(ValueError, match='Treść cytowania'):
        verify_answer(a, a['question'], a['ruleset_id'])


def test_unreviewed_fragment_is_rejected(consultant):
    a = consultant.consult('Jak działa przewaga?', 'dnd_2024_srd_5_2_1').model_dump(mode='json')
    a['retrieved'][0]['chunk']['reviewed'] = False
    with pytest.raises(ValueError):
        verify_answer(a, a['question'], a['ruleset_id'])


def test_unknown_citation_is_rejected(consultant):
    a = consultant.consult('Jak działa przewaga?', 'dnd_2024_srd_5_2_1').model_dump(mode='json')
    a['claims'][0]['evidence'][0]['chunk_id'] = 'invented'
    with pytest.raises(ValueError):
        verify_answer(a, a['question'], a['ruleset_id'])


def test_extra_effect_field_is_rejected(consultant):
    a = consultant.consult('Jak działa przewaga?', 'dnd_2024_srd_5_2_1').model_dump(mode='json')
    a['open_door'] = True
    with pytest.raises(ValueError):
        verify_answer(a, a['question'], a['ruleset_id'])


def test_literal_quote_validation_is_not_entailment(consultant):
    a = consultant.consult('Jak działa przewaga?', 'dnd_2024_srd_5_2_1').model_dump(mode='json')
    a['claims'][0]['text'] = 'CELOWO BŁĘDNY WNIOSEK — test dokumentuje ograniczenie walidatora.'
    assert verify_answer(a, a['question'], a['ruleset_id'])


def test_enable_idempotent_and_world_preserved(repo):
    before = repo.load('demo')
    assert not repo.enable_rules('demo', 'dnd_2024_srd_5_2_1')
    assert repo.load('demo') == before
    with pytest.raises(WorkflowError):
        repo.enable_rules('demo', 'dnd_2014')


def test_pending_old_version_must_finish_in_old_app(repo):
    repo.create_story('old', 'Stara', ROOT/'characters/torin.yaml')
    repo.enable('old')
    old = ImprovisationRepository(repo.db_path)
    old.new_run('old', 'old_turn', 'pukam', 4000)
    with pytest.raises(WorkflowError, match='poprzedniej'):
        repo.enable_rules('old', 'dnd_2024_srd_5_2_1')
    with pytest.raises(WorkflowError, match='innej wersji'):
        repo.get_run('old', 'old_turn')


def test_report_save_does_not_advance_campaign_revision(repo, consultant):
    repo.new_run('demo', 'q', 'przewaga', 4000)
    before = repo.load('demo')
    a = consultant.consult(need().query, before.state.ruleset_id)
    repo.save_consultation('demo', 'q', need(), a, origin='gm', world_revision=before.revision)
    assert repo.load('demo') == before


def test_report_replay_keeps_first_answer(repo, consultant):
    repo.new_run('demo', 'q', 'przewaga', 4000)
    a = consultant.consult(need().query, repo.ruleset('demo'))
    first = repo.save_consultation('demo','q',need(),a,origin='gm',world_revision=repo.load('demo').revision)
    a.claims[0].text = 'Druga odpowiedź, której nie wolno podmienić.'
    second = repo.save_consultation('demo','q',need(),a,origin='gm',world_revision=repo.load('demo').revision)
    assert second == first


def test_concurrent_saves_keep_one_snapshot(repo, consultant):
    repo.new_run('demo', 'q', 'przewaga', 4000)
    a = consultant.consult(need().query, repo.ruleset('demo'))
    rev = repo.load('demo').revision
    def save(_):
        return RulesRepository(repo.db_path).save_consultation('demo','q',need(),a,origin='gm',world_revision=rev)
    with ThreadPoolExecutor(2) as pool:
        records = list(pool.map(save, range(2)))
    assert records[0] == records[1] and len(repo.consultations('demo')) == 1


def test_snapshot_does_not_depend_on_current_library(repo, nodes, library):
    state, _ = drive(nodes, begin(repo, nodes, '/zasady Jak działa przewaga?'))
    before = repo.consultation('demo', 'turn')
    library[0].review('srd521_starter', False)
    assert RulesRepository(repo.db_path).consultation('demo', 'turn') == before


def test_corrupted_report_is_not_trusted(repo, nodes):
    drive(nodes, begin(repo, nodes, '/zasady Jak działa przewaga?'))
    with repo.connection(write=True) as db:
        db.execute("UPDATE step7b_consultations SET answer_hash='broken'")
    with pytest.raises(WorkflowError, match='Uszkodzony'):
        repo.consultation('demo', 'turn')


def test_failed_save_rolls_back(repo, consultant):
    repo.new_run('demo', 'q', 'przewaga', 4000)
    a = consultant.consult(need().query, repo.ruleset('demo'))
    before = repo.load('demo')
    with repo.connection(write=True) as db:
        db.execute("CREATE TRIGGER test_fail BEFORE INSERT ON step7b_consultations BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_consultation('demo','q',need(),a,origin='gm',world_revision=before.revision)
    assert repo.consultation('demo','q') is None and repo.load('demo') == before


@pytest.mark.parametrize('value', [{'choice':'retry'}, {'choice':'stop'}])
def test_rules_interrupt_valid(value):
    assert validate_rules_resume({'kind':'rules_retry'}, value) == value


@pytest.mark.parametrize('value', [{'choice':'app'}, {'choice':'retry','dice':[20]}, {'choice':'confirm'}])
def test_rules_interrupt_cannot_roll(value):
    with pytest.raises(ValueError):
        validate_rules_resume({'kind':'rules_retry'}, value)
