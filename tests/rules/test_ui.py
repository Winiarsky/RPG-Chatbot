"""Programowe testy Streamlit; modele i embeddingi nie są wywoływane."""
import pytest
pytest.importorskip('streamlit')
pytest.importorskip('langgraph.graph')
pytest.importorskip('langgraph.checkpoint.sqlite')
from streamlit.testing.v1 import AppTest
from chat_service import ChatError
from improvisation.roles import Narrator
from rules.repository import RulesRepository
from .conftest import ROOT


def app():
    return AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()


def submit_roll(view, value=10):
    view.number_input[0].set_value(value)
    next(b for b in view.button if b.label == 'Zatwierdź fizyczny rzut').click().run()
    assert not view.exception
    return view


def test_ui_question_and_sources(library):
    view = app()
    assert not view.exception
    view.chat_input[0].set_value('/zasady Jak działa przewaga?').run()
    assert not view.exception
    assert any('tylko znalezione źródła' in str(m.value) for m in view.markdown)
    assert any('Zasady i źródła' in e.label for e in view.expander)


def test_ui_missing_library_does_not_create_campaign(tmp_path):
    view = app()
    assert not view.exception
    assert any('manage_library.py init' in e.value for e in view.error)
    assert not view.chat_input
    assert not (tmp_path / 'game.sqlite3').exists()
    assert not (tmp_path / 'library.sqlite3').exists()


@pytest.mark.parametrize('unreviewed', [False, True])
def test_ui_requires_approved_materials(tmp_path, library, unreviewed):
    store, _ = library
    for document in store.documents():
        if unreviewed:
            store.review(document['document_id'], False)
        else:
            with store.connection() as db:
                db.execute('DELETE FROM lib7_documents')
    view = app()
    assert not view.exception
    assert any('Brak zatwierdzonych materiałów' in e.value for e in view.error)
    assert not view.chat_input
    assert not (tmp_path / 'game.sqlite3').exists()


def test_ui_rejects_shared_database_before_initialization(tmp_path, monkeypatch):
    path = tmp_path / 'shared.sqlite3'
    monkeypatch.setenv('GAME_DB_PATH', str(path))
    monkeypatch.setenv('RAG7_DB_PATH', str(path))
    view = app()
    assert not view.exception
    assert any('różne ścieżki' in e.value for e in view.error)
    assert not view.chat_input
    assert not path.exists()


def test_ui_restores_pending_roll_and_saves_once(library, tmp_path):
    view = app()
    view.chat_input(key='declaration5').set_value('Wyważam drzwi').run()
    assert not view.exception
    assert view.chat_input(key='declaration5').disabled
    fresh = app()
    assert not fresh.exception
    assert len(fresh.number_input) == 1
    submit_roll(fresh)
    assert any('10 +5 = 15' in m.value for m in fresh.markdown)
    assert not fresh.chat_input(key='declaration5').disabled
    repo = RulesRepository(tmp_path / 'game.sqlite3')
    assert repo.progress('demo7b')['elapsed_seconds'] == 60
    restored = app()
    assert not restored.exception
    assert not restored.number_input
    assert repo.progress('demo7b')['elapsed_seconds'] == 60
    assert repo.load('demo7b').state.doors['tower_door'].is_open


def test_ui_narration_retry_after_restart_preserves_effect(library, tmp_path, monkeypatch):
    original = Narrator.narrate
    calls = []

    def fail_once(self, *args):
        calls.append(True)
        if len(calls) == 1:
            raise ChatError('Test: narrator niedostępny.')
        return original(self, *args)

    monkeypatch.setattr(Narrator, 'narrate', fail_once)
    view = app()
    view.chat_input(key='declaration5').set_value('Wyważam drzwi').run()
    submit_roll(view)
    assert view.button(key='narrate5')
    repo = RulesRepository(tmp_path / 'game.sqlite3')
    assert repo.load('demo7b').state.doors['tower_door'].is_open
    assert repo.progress('demo7b')['elapsed_seconds'] == 60
    fresh = app()
    assert not fresh.exception
    fresh.button(key='narrate5').click().run()
    assert not fresh.exception
    assert not fresh.chat_input(key='declaration5').disabled
    assert repo.progress('demo7b')['elapsed_seconds'] == 60
    assert repo.active_run('demo7b') is None
    assert len(calls) == 2


def test_ui_knock_opens_door_without_roll(library):
    view = app()
    view.chat_input(key='declaration5').set_value('pukam').run()
    assert not view.exception
    assert not view.number_input
    assert any('Drzwi wieży: otwarte' in m.value for m in view.markdown)


def test_ui_restores_story_confirmation_and_npc_memory(library):
    view = app()
    view.chat_input(key='declaration5').set_value('pukam').run()
    view.chat_input(key='declaration5').set_value('Wchodzę do wieży').run()
    assert view.button(key='confirm5')
    fresh = app()
    assert not fresh.exception
    fresh.button(key='confirm5').click().run()
    assert any(h.value == 'Przedsionek' for h in fresh.subheader)
    fresh.chat_input(key='declaration5').set_value('Witam Martę').run()
    fresh.button(key='confirm5').click().run()
    assert not fresh.exception
    assert any('Marta: 2' in m.value for m in fresh.markdown)
    restored = app()
    assert not restored.exception
    assert any('Marta: 2' in m.value for m in restored.markdown)


def test_ui_new_campaign_preserves_existing_one(library):
    view = app()
    view.button(key='new5').click().run()
    assert not view.exception
    assert len(view.selectbox(key='rpg7b_campaign').options) == 2
