"""Retrieval metrics; no claim that recall equals answer correctness."""
import json
from pathlib import Path
from .importers import read_limited


def evaluate_retrieval(retriever,cases_path: Path,mode='lexical',top_k=5):
    cases=json.loads(read_limited(cases_path,200000))
    results=[]
    for case in cases:
        expected=set(case.get('expected_sections',[]))
        hits=retriever.search(case['question'],mode=mode,top_k=top_k)
        found=[h.chunk.section_key for h in hits]
        covered=expected.issubset(found) if expected else None
        ranks=[i for i,key in enumerate(found,1) if key in expected]
        results.append(dict(id=case['id'],question=case['question'],expected=sorted(expected),
            found=found,all_expected_at_k=covered,
            reciprocal_rank=(1/min(ranks) if ranks else 0) if expected else None,
            answer_review=case.get('answer_review',[])))
    scored=[r for r in results if r['all_expected_at_k'] is not None]
    n=len(scored)
    return dict(mode=mode,k=top_k,positive_cases=n,negative_cases=len(results)-n,
        all_expected_at_k=sum(r['all_expected_at_k'] for r in scored)/n if n else None,
        mrr_at_k=sum(r['reciprocal_rank'] for r in scored)/n if n else None,
        note='Negatywne pytania nie są oceniane brakiem hitów. Wymagają oceny insufficient w odpowiedzi LLM.',
        cases=results)
