"""Uruchom: python -m streamlit run app.py"""

import streamlit as st

from chat_service import ChatError, ChatService
from config import load_settings
from prompts import DEMO_SCENE


def reset_chat() -> None:
    st.session_state["messages"] = []
    st.session_state["pending"] = None
    st.session_state["error"] = None


def main() -> None:
    st.set_page_config(page_title="RPG DM — etap 1", layout="wide")
    st.title("RPG DM — pierwszy czat")
    st.caption("Prototyp D&D • bez silnika zasad i trwałego zapisu sesji")

    try:
        settings = load_settings()
    except ValueError as exc:
        st.error(f"Błąd konfiguracji: {exc}")
        st.stop()
    except OSError:
        st.error("Nie można odczytać .env. Sprawdź uprawnienia pliku.")
        st.stop()

    if st.session_state.get("settings") != settings:
        try:
            service = ChatService(settings)
        except Exception:
            st.error("Nie można utworzyć klienta modelu. Sprawdź instalację i .env.")
            st.stop()
        st.session_state["settings"] = settings
        st.session_state["service"] = service
        reset_chat()

    with st.sidebar:
        st.header("Sesja testowa")
        st.write(f"Tryb: **{settings.provider}**")
        if settings.provider == "openai":
            st.write(f"Model: `{settings.model}`")
        st.caption(f"Kontekst: do {settings.max_history_turns} deklaracji.")
        if st.button("Nowa rozmowa", key="reset"):
            reset_chat()
            st.rerun()
        st.divider()
        st.subheader("Postać")
        st.write("Jeszcze niewczytana — dodamy ją w etapie 2.")
        st.subheader("Rzuty")
        st.caption("Brak mechaniki rzutów na tym etapie.")
        st.button("Rzuć k20", disabled=True, key="future_roll")

    if settings.provider == "mock":
        st.info("Tryb MOCK: odpowiedzi są zapisane w kodzie, bez API i bez LLM.")
    else:
        st.info("Tryb API: wysyłane są instrukcja i ostatnie wiadomości czatu.")
    st.caption("Historia znika po utracie sesji, restarcie lub zmianie konfiguracji.")
    st.write(f"**Scena demonstracyjna:** {DEMO_SCENE}")

    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input(
        "Co mówi lub zamierza zrobić twoja postać?",
        max_chars=settings.max_input_chars,
        disabled=st.session_state["pending"] is not None,
        key="declaration",
    )
    if prompt and prompt.strip():
        st.session_state["pending"] = prompt.strip()
        st.rerun()

    pending = st.session_state["pending"]
    if pending is None:
        return

    with st.chat_message("user"):
        st.markdown(pending)

    if st.session_state["error"]:
        st.error(st.session_state["error"])
        retry, cancel = st.columns(2)
        if retry.button("Ponów odpowiedź", key="retry"):
            st.session_state["error"] = None
            st.rerun()
        if cancel.button("Anuluj deklarację", key="cancel"):
            st.session_state["pending"] = None
            st.session_state["error"] = None
            st.rerun()
        return

    candidate = [
        *st.session_state["messages"],
        {"role": "user", "content": pending},
    ]
    with st.chat_message("assistant"):
        with st.spinner("Przygotowuję odpowiedź…"):
            try:
                reply = st.session_state["service"].reply(candidate)
            except (ChatError, ValueError) as exc:
                st.session_state["error"] = str(exc)
            else:
                # Zapisujemy pełną parę dopiero po otrzymaniu odpowiedzi.
                st.session_state["messages"] = [
                    *candidate, {"role": "assistant", "content": reply}
                ]
                st.session_state["pending"] = None
    st.rerun()


if __name__ == "__main__":
    main()
