"""Bounded RAG: retrieve -> draft -> at most one rewritten search -> validate evidence."""
import re
from typing import Protocol
from .models import AnswerDraft, LibraryAnswer, Citation
from .store import corpus_revision

class DraftingModel(Protocol):
    def draft(self, question: str, hits: list, ruleset: str) -> AnswerDraft: ...

class EvidenceError(ValueError):
    pass

def compact(text):
    return re.sub(r'\s+', ' ', text).strip()

def validate_evidence(draft, hits, ruleset):
    by_id={h.chunk.chunk_id:h.chunk for h in hits}
    for claim in draft.claims:
        for ref in claim.evidence:
            c=by_id.get(ref.chunk_id)
            if not c or c.source.ruleset_id!=ruleset or not c.reviewed:
                raise EvidenceError('Model wskazał niepobrane lub niedopuszczone źródło. Odpowiedź odrzucona.')
            if compact(ref.quote) not in compact(c.text):
                raise EvidenceError('Cytat modelu nie występuje w pobranym fragmencie. Odpowiedź odrzucona.')
    return draft

def select_context(hits, budget):
    selected=[]
    used=0
    seen=set()
    for h in hits:
        if h.chunk.chunk_id in seen:
            continue
        # Metadata contributes too. Do not truncate inside an evidence passage.
        cost=len(h.chunk.text)+len(h.chunk.section)+500
        if used+cost > budget:
            continue
        selected.append(h)
        seen.add(h.chunk.chunk_id)
        used+=cost
    return selected

class Librarian:
    def __init__(self, retriever, writer: DraftingModel | None = None):
        self.retriever, self.writer= retriever,writer

    def _check_revision(self, expected):
        current=corpus_revision(self.retriever.store.chunks(self.retriever.settings.ruleset_id))
        if expected!=current:
            raise ValueError('Biblioteka zmieniła się podczas odpowiedzi. Powtórz zapytanie dla aktualnych źródeł.')

    def consult(self, question, mode='lexical', generate=False):
        cfg=self.retriever.settings
        before=corpus_revision(self.retriever.store.chunks(cfg.ruleset_id))
        hits=select_context(self.retriever.search(question,mode),cfg.context_chars)
        self._check_revision(before)
        queries=[question]
        if not generate:
            return LibraryAnswer(question=question,ruleset_id=cfg.ruleset_id,
                status='retrieved_only' if hits else 'insufficient',claims=[],
                citations=[Citation(number=i,chunk=h.chunk) for i,h in enumerate(hits,1)],
                retrieved=hits,unresolved_questions=['Nie oceniono, czy fragmenty w pełni odpowiadają na pytanie.'] if hits
                    else ['Brak pasujących fragmentów w zatwierdzonych materiałach. To nie oznacza zakazu w zasadach.'],
                queries=queries,corpus_revision=before,generation='none')
        if not hits:
            return LibraryAnswer(question=question,ruleset_id=cfg.ruleset_id,status='insufficient',
                claims=[],citations=[],retrieved=[],
                unresolved_questions=['Brak materiałów do odpowiedzi. Doprecyzuj zapytanie lub dodaj źródło.'],
                queries=queries,corpus_revision=before,generation='none')
        if self.writer is None:
            raise ValueError('Brak modelu bibliotekarza. Użyj trybu źródeł albo skonfiguruj LLM.')
        draft=AnswerDraft.model_validate(self.writer.draft(question,hits,cfg.ruleset_id))
        validate_evidence(draft,hits,cfg.ruleset_id)
        if draft.status=='insufficient' and draft.search_query and compact(draft.search_query)!=compact(question):
            queries.append(draft.search_query)
            more=self.retriever.search(draft.search_query,mode)
            merged=select_context([*more,*hits],cfg.context_chars)
            if {h.chunk.chunk_id for h in merged}!={h.chunk.chunk_id for h in hits}:
                hits=merged
                draft=AnswerDraft.model_validate(self.writer.draft(question,hits,cfg.ruleset_id))
                validate_evidence(draft,hits,cfg.ruleset_id)
        self._check_revision(before)
        ids=list(dict.fromkeys(e.chunk_id for c in draft.claims for e in c.evidence))
        by_id={h.chunk.chunk_id:h.chunk for h in hits}
        return LibraryAnswer(question=question,ruleset_id=cfg.ruleset_id,status=draft.status,
            claims=draft.claims,citations=[Citation(number=i,chunk=by_id[cid]) for i,cid in enumerate(ids,1)],
            retrieved=hits,unresolved_questions=draft.unresolved_questions,queries=queries,
            corpus_revision=before,generation='llm')


def render_text(answer):
    lines=[f'Status: {answer.status}', f'Pakiet zasad: {answer.ruleset_id}']
    numbers={c.chunk.chunk_id:c.number for c in answer.citations}
    for c in answer.claims:
        refs=' '.join(f'[{numbers[e.chunk_id]}]' for e in c.evidence)
        label='Wniosek z reguł: ' if c.basis=='inference' else ''
        lines+=['',label+c.text+' '+refs]
    if answer.unresolved_questions:
        lines+=['','Braki / zastrzeżenia:']+answer.unresolved_questions
    if answer.status=='retrieved_only':
        lines+=['','Tryb źródeł: bez LLM. To wyniki wyszukiwania, nie gotowa odpowiedź.']
    for cite in answer.citations:
        c=cite.chunk
        page=(f'PDF {c.pdf_page_start}–{c.pdf_page_end}' if c.pdf_page_start!=c.pdf_page_end
              else f'PDF {c.pdf_page_start}') if c.pdf_page_start else 'brak numeru strony (materiał tekstowy)'
        lines+=['',f'[{cite.number}] {c.source.title} | {c.section} | {page}',
                f'ID: {c.chunk_id}; rewizja: {c.revision[:12]}; licencja: {c.source.license}']
        if answer.generation=='none':
            lines.append(c.text)
        else:
            quotes=list(dict.fromkeys(e.quote for cl in answer.claims for e in cl.evidence if e.chunk_id==c.chunk_id))
            lines.extend('> '+q for q in quotes)
    lines+=['',answer.citation_checks,'Bibliotekarz nie wykonuje działań ani nie zmienia stanu kampanii.']
    return '\n'.join(lines)
