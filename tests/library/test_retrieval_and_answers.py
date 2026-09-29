from dataclasses import replace
from types import SimpleNamespace
import pytest
from library.models import AnswerDraft,Claim,EvidenceRef
from library.settings import ROOT
from library.search import Retriever,build_embeddings,query_tokens
from library.librarian import Librarian,EvidenceError,validate_evidence,select_context,render_text
from library.evaluation import evaluate_retrieval
from library.importers import canonical_hash


class FakeEmbeddings:
    """Only tests data flow and dimensions, never semantic quality."""
    def __init__(self,d=64): self.d,self.calls=d,0
    def embed_documents(self,texts):
        self.calls+=1
        return [[1.0]+[0.0]*(self.d-1) for t in texts]
    def embed_query(self,text): return [1.0]+[0.0]*(self.d-1)


def test_aliases_polish_accents():
    assert 'proficiency' in query_tokens('biegłość')
    assert 'advantage' in query_tokens('przewagę')
    assert 'help' in query_tokens('pomagam')


def test_lexical_offline(store,cfg):
    r=Retriever(store,cfg)
    hits=r.search('Jak działa przewaga?')
    assert hits[0].chunk.section_key=='advantage'
    assert all(h.semantic_rank is None for h in hits)


@pytest.mark.parametrize('q',['',' '*5,'x'*4001])
def test_bad_question(store,cfg,q):
    with pytest.raises(ValueError):Retriever(store,cfg).search(q)


def test_no_matches(store,cfg):
    assert Retriever(store,cfg).search('zzzxxyyqqq')==[]


def test_embedding_cache(store,cfg):
    e=FakeEmbeddings();result=build_embeddings(store,cfg,e)
    assert result['embedded_now']==16 and e.calls==1
    again=build_embeddings(store,cfg,e)
    assert again['embedded_now']==0 and e.calls==1
    assert len(Retriever(store,cfg,e).search('przewaga','hybrid'))==6


def test_different_embedding_fingerprint_rejected(store,cfg):
    e=FakeEmbeddings();build_embeddings(store,cfg,e)
    altered=replace(cfg,embedding_model='other-model')
    with pytest.raises(ValueError,match='Brak pełnego indeksu'):
        Retriever(store,altered,e).search('przewaga','hybrid')


def test_missing_embedder(store,cfg):
    with pytest.raises(ValueError,match='Hybrid wymaga'):
        Retriever(store,cfg).search('przewaga','hybrid')


def test_retrieved_only_is_not_supported(store,cfg):
    answer=Librarian(Retriever(store,cfg)).consult('Jak działa przewaga?')
    assert answer.status=='retrieved_only' and answer.generation=='none'
    assert not answer.claims and answer.citations
    assert 'nie gotowa odpowiedź' in render_text(answer)


def draft_for(hits):
    h=next(h for h in hits if h.chunk.section_key=='advantage')
    return AnswerDraft(status='supported',claims=[Claim(text='Przewaga używa wyższej z dwóch kości.',basis='rule',
        evidence=[EvidenceRef(chunk_id=h.chunk.chunk_id,quote='Use the higher of the two rolls if you have Advantage')])],
        unresolved_questions=[],search_query=None)


class FakeWriter:
    def __init__(self):self.calls=0
    def draft(self,q,hits,ruleset):
        self.calls+=1
        return draft_for(hits)


def test_grounded_answer(store,cfg):
    w=FakeWriter()
    a=Librarian(Retriever(store,cfg),w).consult('Jak działa przewaga?',generate=True)
    assert a.status=='supported' and w.calls==1
    assert a.citations[0].chunk.pdf_page_start==7
    assert '[1]' in render_text(a)


def test_no_source_no_model_call(store,cfg):
    w=FakeWriter()
    a=Librarian(Retriever(store,cfg),w).consult('zzzxxyyqqq',generate=True)
    assert a.status=='insufficient' and w.calls==0


def test_fabricated_source_rejected(store,cfg):
    hits=Retriever(store,cfg).search('przewaga')
    d=draft_for(hits);d.claims[0].evidence[0].chunk_id='nonexistent'
    with pytest.raises(EvidenceError,match='niepobrane'):
        validate_evidence(d,hits,cfg.ruleset_id)


def test_fabricated_quote_rejected(store,cfg):
    hits=Retriever(store,cfg).search('przewaga')
    d=draft_for(hits);d.claims[0].evidence[0].quote='Przewaga daje czternaście dodatkowych kości.'
    with pytest.raises(EvidenceError,match='nie występuje'):
        validate_evidence(d,hits,cfg.ruleset_id)


def test_whitespace_normalization_is_allowed(store,cfg):
    hits=Retriever(store,cfg).search('przewaga')
    d=draft_for(hits);d.claims[0].evidence[0].quote=d.claims[0].evidence[0].quote.replace(' ','\n')
    assert validate_evidence(d,hits,cfg.ruleset_id)


def test_one_source_is_not_a_conflict():
    with pytest.raises(ValueError):
        AnswerDraft(status='conflicting',claims=[],unresolved_questions=['Sprzeczność.'])


def test_supported_requires_claims():
    with pytest.raises(ValueError):
        AnswerDraft(status='supported',claims=[],unresolved_questions=[])


def test_no_extra_actions_in_draft():
    with pytest.raises(ValueError):
        AnswerDraft(status='insufficient',claims=[],unresolved_questions=['Brak danych.'],open_door=True)


def test_unreviewed_cannot_be_evidence(store,cfg):
    hits=Retriever(store,cfg).search('przewaga')
    d=draft_for(hits)
    for h in hits:h.chunk.reviewed=False
    with pytest.raises(EvidenceError):validate_evidence(d,hits,cfg.ruleset_id)


def test_evidence_identity_is_not_entailment_proof(store,cfg):
    hits=Retriever(store,cfg).search('przewaga')
    d=draft_for(hits)
    # This intentionally demonstrates the boundary of deterministic validation.
    d.claims[0].text='Celowo błędna interpretacja mimo istniejącego cytatu.'
    assert validate_evidence(d,hits,cfg.ruleset_id) is d


def test_context_does_not_cut_quotes_in_half(store,cfg):
    hits=Retriever(store,cfg).search('przewaga')
    selected=select_context(hits,5000)
    assert all(h.chunk.text==store.show(h.chunk.chunk_id).text for h in selected)
    assert sum(len(h.chunk.text)+len(h.chunk.section)+500 for h in selected)<=5000


def test_rewrite_is_bounded(store,cfg):
    class W:
        calls=0
        def draft(self,q,hits,ruleset):
            self.calls+=1
            return AnswerDraft(status='insufficient',claims=[],unresolved_questions=['Potrzeba innej reguły.'],
                               search_query='Help ability check proficiency')
    w=W(); a=Librarian(Retriever(store,cfg),w).consult('przewaga',generate=True)
    assert len(a.queries)==2 and 1<=w.calls<=2 and a.status=='insufficient'


def test_corpus_edit_during_draft_rejected(store,cfg):
    class W(FakeWriter):
        def draft(self,q,hits,ruleset):
            d=super().draft(q,hits,ruleset)
            store.review('srd521_starter',False)
            return d
    with pytest.raises(ValueError,match='zmieniła'):
        Librarian(Retriever(store,cfg),W()).consult('przewaga',generate=True)


@pytest.mark.parametrize('generate',[False,True])
def test_corpus_review_revoked_during_search_rejected(store,cfg,generate):
    class RevokingRetriever(Retriever):
        def search(self,*args,**kwargs):
            hits=super().search(*args,**kwargs)
            store.review('srd521_starter',False)
            return hits
    writer=FakeWriter()
    with pytest.raises(ValueError,match='zmieniła'):
        Librarian(RevokingRetriever(store,cfg),writer).consult('przewaga',generate=generate)
    assert writer.calls==0


def test_eval_honest_denominators(store,cfg):
    result=evaluate_retrieval(Retriever(store,cfg),ROOT/'evals/cases.json')
    assert result['positive_cases']==27 and result['negative_cases']==3
    assert result['all_expected_at_k']>=0.85
    assert all(c['all_expected_at_k'] is None for c in result['cases'][-3:])
