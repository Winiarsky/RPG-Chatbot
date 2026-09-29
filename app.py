"""Interfejs rozgrywki: python -m streamlit run app.py."""
from uuid import uuid4
import streamlit as st
from config import load_settings
from game_config import load_game_settings
from agents.runtime import safe_error
from rules.repository import RulesRepository
from rules.runtime import make_controller
from rules.ui import evidence_panel
from library.settings import load_library_settings
from library.store import LibraryStore
from rules.settings import load_integration_settings


def perform(operation, *args, **kwargs):
    try:
        operation(*args, **kwargs)
        st.session_state['rpg7b_notice'] = None
    except Exception as exc:
        st.session_state['rpg7b_notice'] = safe_error(exc)
    st.rerun()


def pending_panel(controller, cid, active, view):
    tid = active['turn_id']
    with st.chat_message('user'):
        st.markdown(active['user_text'])
    st.caption('Deklaracja zapisana; nie została jeszcze zakończona.')
    if view and view.get('rules'):
        evidence_panel(view['rules'])
    if view and view.get('intent'):
        with st.expander('Rozpoznany zamiar MG'):
            st.json(view['intent'])
    if view and view.get('recovery'):
        with st.expander('Dopasowanie improwizatora'):
            st.json(view['recovery'])
    action = view.get('action') if view else None
    if action and action['status'] == 'resolved':
        st.success('Konsekwencje zapisano. Ponowienie narracji nie powtórzy działania.')
        st.markdown(action['public_text'])
    pending = view.get('interrupts', []) if view else []
    if pending:
        item = pending[0]
        payload, iid = item['payload'], item['id']
        if payload['kind'] == 'rules_retry':
            st.warning(payload['message'])
            st.error(payload['error'])
            if st.button('Ponów konsultację', key='rules_retry7b'):
                perform(controller.resume, cid, tid, iid, {'choice': 'retry'})
            if st.button('Zakończ bez rozstrzygnięcia', key='rules_stop7b'):
                perform(controller.resume, cid, tid, iid, {'choice': 'stop'})
        elif payload['kind'] == 'story_confirm':
            st.subheader(payload['label'])
            st.write(payload['message'])
            st.caption(f"Czas czynności: {payload['elapsed_seconds']} s. Rzut nie jest wymagany.")
            if st.button('Potwierdź działanie', key='confirm5'):
                perform(controller.resume, cid, tid, iid, {'choice':'confirm'})
            if st.button('Anuluj działanie', key='cancel_story5'):
                perform(controller.resume, cid, tid, iid, {'choice':'cancel'})
        elif payload['kind'] == 'roll':
            plan = payload['plan']
            st.subheader('Oczekuje na rzut: ' + payload['label'])
            st.write(f"{plan['actor_name']} · premia {plan['bonus']:+d} · DC {plan['spec']['dc']} · {plan['mode']}")
            st.caption(f"Koszt przy wykonaniu: {payload['elapsed_seconds']} s, +{payload['noise_events']} hałasu.")
            with st.form('roll5_' + tid + '_' + iid):
                dice = [int(st.number_input(f'Kość {i+1}: surowe k20', min_value=1, max_value=20,
                    value=10, step=1, key=f'die5_{tid}_{i}')) for i in range(plan['dice_count'])]
                manual = st.form_submit_button('Zatwierdź fizyczny rzut')
            if manual:
                perform(controller.resume, cid, tid, iid, {'choice':'manual', 'dice':dice})
            if st.button('Rzuć w aplikacji', key='roll_app5'):
                perform(controller.resume, cid, tid, iid, {'choice':'app'})
            if st.button('Anuluj próbę przed rzutem', key='cancel_roll5'):
                perform(controller.resume, cid, tid, iid, {'choice':'cancel'})
        elif payload['kind'] == 'narration_retry':
            st.warning(payload['message'])
            st.error(payload['error'])
            if st.button('Ponów tylko narrację', key='narrate5'):
                perform(controller.resume, cid, tid, iid, {'choice':'retry'})
            if st.button('Zakończ bez opisu LLM', key='fallback5'):
                perform(controller.resume, cid, tid, iid, {'choice':'fallback'})
        return
    if active.get('error'):
        st.error(active['error'])
    if st.button('Wznów niedokończony etap', key='retry5'):
        perform(controller.retry, cid, tid)
    if st.button('Przerwij deklarację — zachowaj zapisane skutki', key='abort5'):
        perform(controller.abort, cid, tid)


def main():
    st.set_page_config(page_title='RPG DM', layout='wide')
    st.title('RPG DM — rozgrywka z bibliotekarzem zasad')
    st.caption('Opisz działanie postaci. Pytanie o reguły rozpocznij od /zasady.')
    try:
        settings, game = load_settings(), load_game_settings()
        library_cfg, rules_cfg = load_library_settings(), load_integration_settings()
        if game.db_path.resolve() == library_cfg.db_path.resolve():
            raise ValueError('Baza biblioteki i kampanii muszą mieć różne ścieżki. Sprawdź GAME_DB_PATH i RAG7_DB_PATH.')
        library = LibraryStore(library_cfg.db_path)
        if not library.chunks(library_cfg.ruleset_id):
            raise ValueError('Brak zatwierdzonych materiałów tej wersji zasad. '
                             'Uruchom: python manage_library.py init lub sprawdź import i zatwierdzenie materiałów.')
        repo = RulesRepository(game.db_path)
        repo.initialize()
        if not repo.enabled_campaigns():
            existing_ids = {c['campaign_id'] for c in repo.story_campaigns()}
            nid = 'demo7b' if 'demo7b' not in existing_ids else 'rules_' + uuid4().hex[:12]
            repo.create_story(nid, 'Wieża latarnika', game.character_path)
            repo.enable(nid)
            repo.enable_rules(nid, library_cfg.ruleset_id)
        controller = make_controller(repo, settings, library_cfg, rules_cfg)
    except Exception as exc:
        st.error(safe_error(exc))
        st.stop()
    campaigns = repo.enabled_campaigns()
    names = {c['campaign_id']:c['name'] for c in campaigns}
    if 'rpg7b_next_campaign' in st.session_state:
        st.session_state['rpg7b_campaign'] = st.session_state.pop('rpg7b_next_campaign')
    with st.sidebar:
        st.header('Kampanie')
        cid = st.selectbox('Wybór kampanii', list(names), format_func=lambda c:f'{names[c]} [{c}]', key='rpg7b_campaign')
        if st.button('Nowa kampania', key='new5'):
            nid = 'story_' + uuid4().hex[:12]
            try:
                repo.create_story(nid, 'Wieża — nowa próba', game.character_path)
                repo.enable(nid)
                repo.enable_rules(nid, library_cfg.ruleset_id)
                st.session_state['rpg7b_next_campaign'] = nid
            except Exception as exc:
                st.session_state['rpg7b_notice'] = safe_error(exc)
            st.rerun()
        if st.button('Odśwież zapis', key='refresh5'):
            st.rerun()
        st.write(f'Tryb: **{settings.provider}**')
        st.caption(f'Biblioteka: {rules_cfg.search_mode}; odpowiedź LLM: {rules_cfg.generate(settings.provider)}')
        if settings.provider != 'mock':
            st.caption('Model: ' + settings.model)
    try:
        context, catalog, _, turns = repo.bundle4(cid, 100)
        records = {r['turn_id']: r for r in repo.consultations(cid, 100)}
        active = repo.active_run(cid)
        orphan = repo.pending_action(cid) if active is None else None
    except Exception as exc:
        st.error(safe_error(exc))
        st.stop()
    char = context['character']
    with st.sidebar:
        st.subheader(char['name'])
        st.metric('HP', f"{char['hp_current']}/{char['hp_max']}")
        st.write(f"KP: {char['armor_class']} · poziom: {char['level']}")
        st.caption(f"Czas: {context['mechanics']['elapsed_seconds']} s · hałas: {context['mechanics']['noise_events']}")
        st.subheader('Znane informacje')
        for fact in context['story']['known_facts']:
            st.write(fact['text'])
        if not context['story']['known_facts']:
            st.caption('Brak odkrytych wskazówek.')
        st.subheader('Zapamiętane rozmowy')
        for npc in context['story']['known_npcs']:
            st.write(f"{npc['name']}: {npc['conversations']}")
    if settings.provider == 'mock':
        st.info('MOCK: bez modeli generujących; bibliotekarz pokazuje tylko źródła. Naturalny język ma ograniczony zestaw fraz testowych.')
    st.subheader(context['scene']['name'])
    st.write(context['scene']['description'])
    for door in context['scene']['doors']:
        st.write(f"**{door['name']}: {'otwarte' if door['is_open'] else 'zamknięte'}.** {door['description']}")
    if context['scene']['npcs']:
        st.caption('Obecni: ' + ', '.join(n['name'] for n in context['scene']['npcs']))
    if context['scene'].get('contacts'):
        st.caption('Rozmowa przez próg: ' + ', '.join(n['name'] for n in context['scene']['contacts']))
    if st.session_state.get('rpg7b_notice'):
        st.error(st.session_state['rpg7b_notice'])
    for turn in turns:
        with st.chat_message('user'):
            st.markdown(turn['user_text'])
        with st.chat_message('assistant'):
            st.markdown(turn['assistant_text'])
            tid = turn['request_id'].removeprefix('s4:').removesuffix(':reply')
            evidence_panel(records.get(tid))
    if orphan:
        st.warning('Pozostała operacja bez aktywnej deklaracji. Możesz ją jawnie anulować.')
        if st.button('Anuluj osieroconą operację', key='orphan5'):
            perform(repo.cancel_action, cid, orphan['action_id'])
    if active:
        view = None
        try:
            view = controller.view(cid, active['turn_id'])
        except Exception as exc:
            st.error(safe_error(exc))
        pending_panel(controller, cid, active, view)
    with st.expander('Publiczny stan i dostępne działania — bez sekretów'):
        st.json(context)
        st.json(catalog)
    text = st.chat_input('Np. „Pukam” albo „/zasady Jak działa przewaga?”',
                        max_chars=settings.max_input_chars, disabled=active is not None or orphan is not None,
                        key='declaration5')
    if text and text.strip():
        perform(controller.start, cid, text.strip(), turn_id=uuid4().hex)


if __name__ == '__main__':
    main()
