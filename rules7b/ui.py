"""Panel odczytu zapisanych dowodów; samo otwarcie nie wywołuje modelu."""
from library7a.models import LibraryAnswer


def evidence_panel(record):
    if not record:
        return
    import streamlit as st
    answer = LibraryAnswer.model_validate(record['answer'])
    with st.expander('Zasady i źródła — ' + record['turn_id'][:12]):
        st.write('Pytanie do biblioteki:', answer.question)
        st.caption(f"Status: {answer.status} · inicjator: {record['origin']} · zapis: {record['created_at']}")
        st.caption('Wersja zasad: ' + answer.ruleset_id + ' · rewizja materiałów: ' + answer.corpus_revision[:16])
        st.write('Wymagania silnika:', record['request']['required_capabilities'] or 'Nie dotyczy — pytanie.')
        if answer.unresolved_questions:
            st.warning(' '.join(answer.unresolved_questions))
        if not answer.citations:
            st.caption('Brak potwierdzonych cytowań. Nie dopisano odpowiedzi z pamięci modelu.')
        for cite in answer.citations:
            c = cite.chunk
            st.markdown(f'**[{cite.number}] {c.source.title} — {c.section}**')
            st.caption(f'Wersja dokumentu: {c.source.version} · ID: {c.chunk_id}')
            if c.pdf_page_start:
                st.caption(f'Strony pliku PDF (liczone od 1): {c.pdf_page_start}–{c.pdf_page_end}')
            if c.printed_page:
                st.caption('Oznaczenie strony w druku: ' + c.printed_page)
            quotes = list(dict.fromkeys(e.quote for claim in answer.claims for e in claim.evidence
                if e.chunk_id == c.chunk_id))
            for quote in quotes:
                st.text(quote)
            if not quotes:
                st.text(c.text)
            st.caption('Licencja: ' + c.source.license + ' · ' + c.source.attribution)
        st.caption(answer.citation_checks)
        st.caption('To historyczny zapis konsultacji. Ponowny import materiałów nie przepisuje tej odpowiedzi.')
