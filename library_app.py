"""Standalone Streamlit workbench. No imports of game state, graph, or campaign DB."""
import streamlit as st
from library.settings import load_library_settings,ROOT
from library.store import LibraryStore
from library.search import Retriever
from library.librarian import Librarian


def display_hit(chunk):
    st.caption(f'{chunk.source.title} | {chunk.section} | wersja {chunk.source.version}')
    if chunk.pdf_page_start:
        st.caption(f'Strona PDF: {chunk.pdf_page_start}–{chunk.pdf_page_end}; '
                   f'numer drukowany: {chunk.printed_page or "nieustalony"}')
    else:
        st.caption('Materiał tekstowy: bez numeru strony PDF.')
    st.code(chunk.text,language=None,wrap_lines=True)
    st.caption(f'ID: {chunk.chunk_id} | rewizja: {chunk.revision[:12]} | {chunk.source.license}')
    if chunk.source.source_url:
        st.link_button('Otwórz źródło',chunk.source.source_url+
                       (f'#page={chunk.pdf_page_start}' if chunk.pdf_page_start else ''))


def main():
    st.set_page_config(page_title='RPG DM — biblioteka zasad',layout='wide')
    st.title('Bibliotekarz zasad')
    st.caption('Wyszukaj zasady i sprawdź źródła odpowiedzi.')
    try:
        settings=load_library_settings()
        store=LibraryStore(settings.db_path)
    except Exception as exc:
        st.error(str(exc) if isinstance(exc,ValueError) else f'Błąd biblioteki: {type(exc).__name__}')
        st.code('python manage_library.py init',language='bash')
        return
    docs=store.documents()
    with st.sidebar:
        st.header('Zakres biblioteki')
        st.write(settings.ruleset_id)
        st.caption(str(settings.db_path))
        st.write(f'Zatwierdzone fragmenty: {len(store.chunks(settings.ruleset_id))}')
        st.warning('Start: 16 wybranych sekcji, nie pełny SRD. Brak odpowiedzi nie oznacza zakazu.')
        for d in docs:
            st.write(f"{d['document_id']} — {'zatwierdzony' if d['reviewed'] else 'do przeglądu'}")
        st.caption('Import plików i budowanie wektorów wykonuj jawnie z terminala.')
    with st.form('library_question'):
        question=st.text_area('Pytanie o zasady',value='Jak działa przewaga?',max_chars=4000,key='question')
        mode=st.selectbox('Wyszukiwanie',['lexical','hybrid'],key='mode',
            help='Lexical: lokalne BM25 + aliasy PL/EN. Hybrid: dodatkowo embedding pytania przez API.')
        generate=st.checkbox('Wygeneruj odpowiedź LLM (wysyła pytanie i fragmenty do API)',value=False,key='generate')
        consent=st.checkbox('Zgoda na API embeddingów dla hybrid',value=False,key='embedding_consent')
        submitted=st.form_submit_button('Sprawdź')
    if submitted:
        st.session_state.pop('answer7a',None)
        try:
            if mode=='hybrid' and not consent:
                raise ValueError('Zaznacz zgodę na API embeddingów albo wybierz lexical.')
            embedder=writer=None
            if mode=='hybrid':
                from library.providers import OpenAIEmbedder
                embedder=OpenAIEmbedder(settings)
            if generate:
                from library.providers import LangChainWriter
                writer=LangChainWriter(settings)
            with st.spinner('Wyszukuję i sprawdzam źródła…'):
                result=Librarian(Retriever(store,settings,embedder),writer).consult(question,mode,generate)
            st.session_state['answer7a']=result
        except Exception as exc:
            if type(exc).__name__=='ChatError' or isinstance(exc,ValueError):
                st.error(str(exc))
            else:
                st.error(f'Błąd: {type(exc).__name__}. Sprawdź terminal i konfigurację.')
    answer=st.session_state.get('answer7a')
    if answer:
        st.subheader(f'Wynik: {answer.status}')
        st.caption(f'Pytanie: {answer.question}')
        if answer.generation=='none':
            st.info('Bez LLM: znalezione fragmenty nie są jeszcze zweryfikowaną odpowiedzią na pytanie.')
        numbers={c.chunk.chunk_id:c.number for c in answer.citations}
        for claim in answer.claims:
            refs=' '.join(f'[{numbers[e.chunk_id]}]' for e in claim.evidence)
            prefix='Wniosek z reguł: ' if claim.basis=='inference' else ''
            st.write(prefix+claim.text+' '+refs)
        for missing in answer.unresolved_questions:
            st.warning(missing)
        for cite in answer.citations:
            with st.expander(f'[{cite.number}] {cite.chunk.section}'):
                display_hit(cite.chunk)
        with st.expander('Wszystkie pobrane fragmenty i ślad wyszukiwania'):
            st.write(answer.queries)
            for hit in answer.retrieved:
                st.caption(f'Score rankingowy: {hit.score:.5f} — nie prawdopodobieństwo poprawności')
                display_hit(hit.chunk)
        st.caption(answer.citation_checks)
        st.download_button('Eksport odpowiedzi i dowodów (JSON)',answer.model_dump_json(indent=2),
                           file_name='library_answer.json',mime='application/json')
    with st.expander('Atrybucja materiałów'):
        path=ROOT/'ATTRIBUTION_SRD.md'
        if path.exists():
            st.markdown(path.read_text(encoding='utf-8'))
        else:
            st.write('Sprawdź atrybucję dokumentów w manifestach.')

if __name__=='__main__':
    main()
