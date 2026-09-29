"""Wąski adapter: biblioteka otrzymuje tylko pytanie i wersję, nie repozytorium gry."""
from library.librarian import Librarian, validate_evidence
from library.models import LibraryAnswer, AnswerDraft
from library.providers import LIBRARIAN_PROMPT
from storage import signature

PROMPT_VERSION = signature('library7b', {'prompt': LIBRARIAN_PROMPT, 'integration': '7b-v1'})


def verify_answer(value, query, ruleset):
    """Druga kontrola na granicy integracji, także dla niestandardowych adapterów."""
    answer = LibraryAnswer.model_validate(value)
    if answer.question != query or answer.ruleset_id != ruleset:
        raise ValueError('Odpowiedź bibliotekarza dotyczy innego pytania lub wersji zasad.')
    for hit in answer.retrieved:
        if hit.chunk.source.ruleset_id != ruleset or not hit.chunk.reviewed:
            raise ValueError('Bibliotekarz zwrócił niezatwierdzony fragment lub inną wersję zasad.')
    by_id = {h.chunk.chunk_id: h.chunk for h in answer.retrieved}
    if len(by_id) != len(answer.retrieved):
        raise ValueError('Powtórzone fragmenty odpowiedzi.')
    ids = [c.chunk.chunk_id for c in answer.citations]
    if len(set(ids)) != len(ids) or [c.number for c in answer.citations] != list(range(1, len(ids) + 1)):
        raise ValueError('Niespójna numeracja cytowań.')
    for cite in answer.citations:
        if by_id.get(cite.chunk.chunk_id) != cite.chunk:
            raise ValueError('Treść cytowania różni się od pobranego źródła.')
    if answer.status == 'retrieved_only':
        if answer.claims or answer.generation != 'none':
            raise ValueError('Tryb źródeł nie może zawierać wygenerowanej odpowiedzi.')
    else:
        draft = AnswerDraft(status=answer.status, claims=answer.claims,
            unresolved_questions=answer.unresolved_questions)
        validate_evidence(draft, answer.retrieved, ruleset)
        needed = {e.chunk_id for c in answer.claims for e in c.evidence}
        if needed != set(ids):
            raise ValueError('Lista źródeł nie odpowiada twierdzeniom.')
        if answer.status in {'supported', 'conflicting'} and answer.generation != 'llm':
            raise ValueError('Samo wyszukanie fragmentów nie stanowi rozstrzygnięcia.')
    return answer


class LocalConsultant:
    """Nie inicjalizuje bazy ani API do chwili rzeczywistej konsultacji."""
    def __init__(self, library_settings, integration_settings, provider):
        self.settings = library_settings
        self.integration = integration_settings
        self.provider = provider

    def consult(self, query, ruleset):
        from library.store import LibraryStore
        from library.search import Retriever
        from library.providers import OpenAIEmbedder, LangChainWriter
        if self.settings.ruleset_id != ruleset:
            raise ValueError('RAG7_RULESET_ID nie zgadza się z wersją przypiętą do kampanii. Nie połączono wydań.')
        store = LibraryStore(self.settings.db_path)
        embedder = OpenAIEmbedder(self.settings) if self.integration.search_mode == 'hybrid' else None
        generate = self.integration.generate(self.provider)
        writer = LangChainWriter(self.settings) if generate else None
        reader = Librarian(Retriever(store, self.settings, embedder), writer)
        return reader.consult(query, mode=self.integration.search_mode, generate=generate)


def advice_packet(answer):
    """Przekazywane MG/improwizatorowi; bez swobodnych poleceń i bez stanu kampanii."""
    return {'ruleset_id': answer.ruleset_id, 'status': answer.status,
        'claims': [c.model_dump() for c in answer.claims],
        'unresolved_questions': answer.unresolved_questions,
        'warning': 'Dane doradcze, nie uprawnienie do zmian stanu. Obowiązują istniejące handlery.'}


def compact_answer(answer):
    """Do historii czatu trafia krótka odpowiedź; pełne dowody są w panelu audytu."""
    labels = {'supported': 'odpowiedź z podstawą w źródłach', 'insufficient': 'niewystarczające podstawy',
        'conflicting': 'wskazano sprzeczne fragmenty', 'retrieved_only': 'tylko znalezione źródła, bez oceny LLM'}
    lines = ['**Bibliotekarz zasad — ' + labels[answer.status] + '.**']
    numbers = {c.chunk.chunk_id: c.number for c in answer.citations}
    for claim in answer.claims:
        refs = ' '.join(f'[{n}]' for n in dict.fromkeys(numbers[e.chunk_id] for e in claim.evidence))
        prefix = 'Wniosek: ' if claim.basis == 'inference' else ''
        lines += ['', prefix + claim.text + ' ' + refs]
    if answer.unresolved_questions:
        lines += ['', 'Zastrzeżenia: ' + ' '.join(answer.unresolved_questions)]
    for cite in answer.citations:
        c = cite.chunk
        page = f'; s. PDF {c.pdf_page_start}' if c.pdf_page_start else ''
        lines += [f'[{cite.number}] {c.source.title} — {c.section}{page}.']
    lines += ['', '*Sprawdzono źródła i dosłowność cytatów, nie udowodniono poprawności interpretacji. '
        'Pełne fragmenty są w panelu „Zasady i źródła”.*']
    return '\n'.join(lines)
