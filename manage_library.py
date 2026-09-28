"""Standalone library CLI. Never imports repositories of campaigns."""
import argparse
import json
import sys
from pathlib import Path
from library7a.settings import load_library_settings,ROOT
from library7a.store import LibraryStore
from library7a.importers import load_manifest
from library7a.search import Retriever,build_embeddings
from library7a.librarian import Librarian,render_text


def parser():
    p=argparse.ArgumentParser(description='RPG — lokalna biblioteka zasad')
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('doctor')
    init=sub.add_parser('init',help='Utwórz bazę i importuj zweryfikowany pakiet startowy')
    init.add_argument('--replace',action='store_true',help='Jawnie zastąp poprzednią rewizję pakietu startowego')
    sub.add_parser('documents')
    i=sub.add_parser('import',help='Importuj lokalny manifest, początkowo niezatwierdzony')
    i.add_argument('manifest',type=Path); i.add_argument('--replace',action='store_true')
    r=sub.add_parser('review'); r.add_argument('document_id')
    group=r.add_mutually_exclusive_group(required=True)
    group.add_argument('--approve',action='store_true'); group.add_argument('--revoke',action='store_true')
    r.add_argument('--confirm',action='store_true',help='Potwierdź ręczne sprawdzenie ekstrakcji i wersji')
    s=sub.add_parser('show'); s.add_argument('chunk_id')
    for name in ('search','ask'):
        s=sub.add_parser(name); s.add_argument('question')
        s.add_argument('--mode',choices=['lexical','hybrid'],default='lexical')
        s.add_argument('--allow-api',action='store_true',help='Zgoda na embedding pytania (hybrid)')
        s.add_argument('--json',action='store_true')
        if name=='search':
            s.add_argument('--include-unreviewed',action='store_true')
            s.add_argument('--top-k',type=int,default=6)
        else:
            s.add_argument('--llm',action='store_true',help='Wyślij pytanie i znalezione fragmenty do modelu')
    e=sub.add_parser('embeddings'); e.add_argument('--allow-api',action='store_true')
    e=sub.add_parser('eval'); e.add_argument('--mode',choices=['lexical','hybrid'],default='lexical')
    e.add_argument('--allow-api',action='store_true'); e.add_argument('--top-k',type=int,default=5)
    e.add_argument('--llm',action='store_true',help='Wygeneruj odpowiedzi ewaluacyjne; płatne API, bez automatycznej oceny prawdy')
    e.add_argument('--out',type=Path)
    f=sub.add_parser('fetch-srd',help='Pobierz oficjalny PDF 5.2.1, bez API i bez automatycznego indeksowania')
    f.add_argument('--out',type=Path,default=ROOT/'materials7a/full')
    return p


def fetch_srd(directory):
    from urllib.request import urlopen
    from library7a.importers import MAX_FILE_BYTES,digest
    import yaml
    url='https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf'
    path=directory.resolve(); path.mkdir(parents=True,exist_ok=True)
    pdf,man=path/'SRD_CC_v5.2.1.pdf',path/'manifest.yaml'
    if pdf.exists() or man.exists():
        raise ValueError('Pliki docelowe istnieją. Wybierz pusty katalog zamiast je nadpisywać.')
    with urlopen(url,timeout=45) as response:
        raw=response.read(MAX_FILE_BYTES+1)
    if len(raw)>MAX_FILE_BYTES or not raw.startswith(b'%PDF-'):
        raise ValueError('Pobrany plik nie jest oczekiwanym PDF lub przekracza limit rozmiaru.')
    # Verify document identity from its own first page, not URL alone.
    from pypdf import PdfReader
    from io import BytesIO
    first=PdfReader(BytesIO(raw)).pages[0].extract_text() or ''
    if '5.2.1' not in first or 'Creative Commons' not in first:
        raise ValueError('Nie potwierdzono wersji 5.2.1 w pobranym PDF.')
    starter,_,_=load_manifest(ROOT/'materials7a/starter/manifest.yaml')
    source=starter.source.model_copy(update={'document_id':'srd521_full',
        'title':'System Reference Document 5.2.1 — full PDF',
        'coverage':'Full official PDF, automatic page extraction; requires manual review of text order and tables.'})
    with pdf.open('xb') as f:
        f.write(raw)
    with man.open('x',encoding='utf-8') as f:
        yaml.safe_dump(dict(source=source.model_dump(),file=pdf.name,format='pdf',sha256=digest(raw)),
                       f,sort_keys=False,allow_unicode=True)
    print(f'Pobrano: {pdf}\nManifest: {man}\nNie zaimportowano ani nie wysłano do API.')


def main(argv=None):
    args=parser().parse_args(argv)
    settings=load_library_settings()
    if args.cmd=='doctor':
        import importlib.metadata as md
        print(f'Python: {sys.version.split()[0]}\nBaza biblioteki: {settings.db_path}\nRuleset: {settings.ruleset_id}')
        missing=[]
        for name in ['pydantic','PyYAML','pypdf','streamlit','langchain-openai','langchain-core']:
            try:
                print(f'{name}: {md.version(name)}')
            except md.PackageNotFoundError:
                print(f'{name}: BRAK')
                missing.append(name)
        print(f'Embedding: {settings.embedding_model}/{settings.dimensions}; wynik: {settings.output_mode}')
        print('Bez wywołania API; klucze nie są wypisywane przez doctor.')
        return 1 if missing else 0
    if args.cmd=='fetch-srd':
        fetch_srd(args.out); return 0
    store=LibraryStore(settings.db_path,create=args.cmd in {'init','import'})
    if args.cmd in {'init','import'}:
        manifest=ROOT/'materials7a/starter/manifest.yaml' if args.cmd=='init' else args.manifest
        _,chunks,warnings=load_manifest(manifest)
        result=store.import_chunks(chunks,reviewed=args.cmd=='init',replace=getattr(args,'replace',False))
        print(f'{result}: {chunks[0].source.document_id}; fragmenty: {len(chunks)}')
        for w in warnings:
            print('UWAGA:',w)
        if args.cmd=='import':
            print('Import własny wymaga review --approve --confirm. Brak wywołań API.')
        return 0
    if args.cmd=='documents':
        print(json.dumps(store.documents(),ensure_ascii=False,indent=2)); return 0
    if args.cmd=='review':
        if not args.confirm:
            raise ValueError('Dodaj --confirm dopiero po sprawdzeniu wersji, stron i ekstrakcji.')
        store.review(args.document_id,args.approve)
        print('Zmieniono status przeglądu. Nie zmieniono kampanii.'); return 0
    if args.cmd=='show':
        print(store.show(args.chunk_id).model_dump_json(indent=2)); return 0
    embedder=None
    if args.cmd=='embeddings' or getattr(args,'mode','lexical')=='hybrid':
        if not args.allow_api:
            raise ValueError('To wywołanie wysyła tekst do API embeddingów. Dodaj --allow-api lub użyj lexical.')
        from library7a.providers import OpenAIEmbedder
        embedder=OpenAIEmbedder(settings)
    if args.cmd=='embeddings':
        print(json.dumps(build_embeddings(store,settings,embedder,
              progress=lambda n,total:print(f'Wektory: {n}/{total}',file=sys.stderr)),indent=2))
        return 0
    retriever=Retriever(store,settings,embedder)
    if args.cmd=='search':
        hits=retriever.search(args.question,args.mode,args.top_k,args.include_unreviewed)
        print(json.dumps([h.model_dump() for h in hits],ensure_ascii=False,indent=2))
        return 0
    writer=None
    if getattr(args,'llm',False):
        from library7a.providers import LangChainWriter
        writer=LangChainWriter(settings)
    if args.cmd=='ask':
        ans=Librarian(retriever,writer).consult(args.question,args.mode,generate=args.llm)
        print(ans.model_dump_json(indent=2) if args.json else render_text(ans)); return 0
    if args.cmd=='eval':
        from library7a.evaluation import evaluate_retrieval
        cases_path=ROOT/'evals7a/cases.json'
        result=evaluate_retrieval(retriever,cases_path,args.mode,args.top_k)
        if args.llm:
            result['generated_answers']=[]
            for case in json.loads(cases_path.read_text(encoding='utf-8')):
                try:
                    ans=Librarian(retriever,writer).consult(case['question'],args.mode,True)
                    item={'id':case['id'],'answer':ans.model_dump(),'human_review_required':True}
                except Exception as exc:
                    item={'id':case['id'],'error_type':type(exc).__name__,'human_review_required':True}
                result['generated_answers'].append(item)
        text=json.dumps(result,ensure_ascii=False,indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True,exist_ok=True)
            # Exclusive creation avoids silently losing earlier reports.
            with args.out.open('x',encoding='utf-8') as f:
                f.write(text+'\n')
            print('Raport:',args.out)
        else:
            print(text)
        return 0
    raise ValueError('Nieznane polecenie.')

if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (Exception,) as exc:
        # Known diagnostic errors from ChatService are already sanitized.
        if type(exc).__name__=='ChatError':
            print(str(exc),file=sys.stderr)
        elif isinstance(exc, (ValueError,FileNotFoundError,FileExistsError)):
            print(f'BŁĄD: {exc}',file=sys.stderr)
        else:
            print(f'BŁĄD: {type(exc).__name__}. Sprawdź instalację, pliki lub połączenie.',file=sys.stderr)
        raise SystemExit(1)
