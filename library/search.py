"""Transparent lexical BM25 + optional dense cosine, merged with reciprocal rank fusion."""
import math
import re
import unicodedata
from collections import Counter
from .models import Hit
from .store import normalize_vector
from .importers import canonical_hash

STOP = set('a an and are as at be by for from has have if in into is it of on or the to when which with you your co czy do dla jak jaka jakie jaki jest ma mam na nie o po to w z ze i sie ten ta te oraz mi mnie moge mogę dziala działa dnd rules rule zasady zasad'.split())
ALIASES = [
    (r'\bprzewag\w*', 'advantage'), (r'\b(?:utrudn|zawad)\w*', 'disadvantage'),
    (r'\bbieglo\w*', 'proficiency bonus'), (r'\bekspertyz\w*', 'expertise'),
    (r'\b(?:pomoc|pomag|pomoz|pomaga|pomagam)\w*', 'help assist'),
    (r'\b(?:inspirac|inspiracj)\w*', 'heroic inspiration'),
    (r'\b(?:reakcj|reakc|reakcja)\w*', 'reaction reactions'),
    (r'\bakcj\w* dodatk\w*', 'bonus action'),
    (r'\b(?:test|testu|testy|testow)\b', 'check'),
    (r'\bczynno\w*', 'action activity'), (r'\bakcj\w*', 'action actions'),
    (r'\b(?:rzuc|rzut)\w*', 'roll'),
    (r'\b(?:cech|cechy|ceche)\b', 'ability'),
    (r'\b(?:modyfikat|modyfik)\w*', 'modifier'),
    (r'\b(?:atlet|atletyk)\w*', 'athletics'),
    (r'\b(?:naturaln)\w*', 'natural'),
    (r'\b(?:dwudziestk|dwudziestce)\w*', '20 natural'),
    (r'\b(?:jedynk|jedynce)\w*', '1 natural'),
    (r'\b(?:atak|ataku|ataki|trafienie)\b', 'attack roll'),
    (r'\b(?:sumuj|kumuluj|sumowa|podwojn|podwaja|dubluj)\w*', 'stack double'),
    (r'\b(?:trudnos|trudnosc|dc)\w*', 'difficulty class'),
    (r'\b(?:przerzuc|przerzut|przerzucic)\w*', 'reroll'),
    (r'\b(?:kostk|kosci|koscia)\w*', 'die dice roll'),
    (r'\b(?:ukry|chowa|schowa|skrad)\w*', 'hide stealth'),
    (r'\b(?:wyjat|wyjatek)\w*', 'exception'),
    (r'\b(?:zaokrag)\w*', 'round fraction'),
    (r'\b(?:otw|wywaz)\w*', 'forcing stuck door'),
    (r'\bdrzwi\b', 'door'), (r'\b(?:improwiz)\w*', 'improvise actions'),
]

def normalize(text):
    text = text.lower().replace('ł','l')
    return ''.join(c for c in unicodedata.normalize('NFKD',text) if not unicodedata.combining(c))

def tokens(text):
    return [w for w in re.findall(r'[a-z0-9]+', normalize(text)) if w not in STOP]

def query_tokens(query):
    q = normalize(query)
    expansion = ' '.join(value for pattern,value in ALIASES if re.search(pattern,q))
    return list(dict.fromkeys(tokens(q+' '+expansion)))[:80]

def lexical_ranks(chunks, question):
    docs = [Counter(tokens(c.section+' '+c.section+' '+c.text)) for c in chunks]
    terms = query_tokens(question)
    if not terms or not docs:
        return []
    lengths = [sum(d.values()) for d in docs]
    mean = sum(lengths)/len(lengths) or 1
    df = {term:sum(term in d for d in docs) for term in terms}
    results = []
    for chunk, doc, length in zip(chunks,docs,lengths):
        score=0.0
        for term in terms:
            tf=doc.get(term,0)
            if tf:
                idf=math.log(1+(len(docs)-df[term]+0.5)/(df[term]+0.5))
                score += idf*tf*2.5/(tf+1.5*(0.25+0.75*length/mean))
        if score > 0:
            results.append((chunk.chunk_id,score))
    return sorted(results,key=lambda x:(-x[1],x[0]))

class Retriever:
    def __init__(self, store, settings, embedder=None):
        self.store,self.settings,self.embedder=store,settings,embedder

    def search(self, question: str, mode='lexical', top_k=None, include_unreviewed=False):
        if not isinstance(question,str) or not question.strip() or len(question)>4000:
            raise ValueError('Pytanie musi mieć 1–4000 znaków.')
        if mode not in {'lexical','hybrid'}:
            raise ValueError('Tryb wyszukiwania: lexical lub hybrid.')
        k = self.settings.top_k if top_k is None else top_k
        if not 1<=k<=20:
            raise ValueError('top_k: zakres 1–20.')
        chunks=self.store.chunks(self.settings.ruleset_id,include_unreviewed)
        if not chunks:
            return []
        lexical=lexical_ranks(chunks,question)
        if mode=='lexical':
            by_id={c.chunk_id:c for c in chunks}
            return [Hit(chunk=by_id[cid],score=score,lexical_rank=i)
                    for i,(cid,score) in enumerate(lexical[:k],1)]
        if include_unreviewed:
            raise ValueError('Podgląd niezatwierdzonego importu obsługuje tylko wyszukiwanie lexical.')
        if self.embedder is None:
            raise ValueError('Hybrid wymaga skonfigurowanych embeddingów i zgody na API.')
        fp=canonical_hash(self.settings.embedding_spec)
        vectors=self.store.vectors(fp)
        if any(c.chunk_id not in vectors for c in chunks):
            raise ValueError('Brak pełnego indeksu dla tego modelu/wymiaru. Uruchom embeddings --allow-api.')
        q=normalize_vector(self.embedder.embed_query(question),self.settings.dimensions)
        scores=[]
        for c in chunks:
            v=normalize_vector(vectors[c.chunk_id],self.settings.dimensions)
            scores.append((c.chunk_id,sum(a*b for a,b in zip(v,q))))
        dense=sorted(scores,key=lambda x:(-x[1],x[0]))
        # No score is interpreted as probability or a guarantee of support.
        lexrank={cid:i for i,(cid,_) in enumerate(lexical[:40],1)}
        denserank={cid:i for i,(cid,_) in enumerate(dense[:40],1)}
        candidates=[]
        for c in chunks:
            lr,dr=lexrank.get(c.chunk_id),denserank.get(c.chunk_id)
            if lr or dr:
                score=(1/(60+lr) if lr else 0)+(1/(60+dr) if dr else 0)
                candidates.append(Hit(chunk=c,score=score,lexical_rank=lr,semantic_rank=dr))
        return sorted(candidates,key=lambda h:(-h.score,h.chunk.chunk_id))[:k]


def build_embeddings(store, settings, embedder, progress=None):
    chunks=store.chunks(settings.ruleset_id)
    fp=canonical_hash(settings.embedding_spec)
    existing=store.vectors(fp)
    missing=[c for c in chunks if c.chunk_id not in existing]
    completed=0
    for start in range(0,len(missing),32):
        batch=missing[start:start+32]
        vectors=embedder.embed_documents([c.section+'\n\n'+c.text for c in batch])
        store.save_vectors(batch,vectors,fp,settings.dimensions)
        completed+=len(batch)
        if progress:
            progress(completed,len(missing))
    return {'chunks':len(chunks),'embedded_now':completed,'cached':len(chunks)-len(missing),
            'fingerprint':fp,'spec':settings.embedding_spec}
