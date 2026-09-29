import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
import yaml
from library.models import Source,Section
from library.importers import load_manifest,markdown_sections,paragraphs,digest
from library.settings import ROOT
from library.store import LibraryStore,normalize_vector


def custom_manifest(tmp_path,text='# Rule\n\nThis is a clearly marked artificial testing rule.',docid='custom',ruleset='test_only'):
    source=Source(document_id=docid,title='Test rule',ruleset_id=ruleset,version='1',
                  license='Test',attribution='Test author',coverage='Synthetic fixtures only')
    (tmp_path/'rule.md').write_text(text,encoding='utf-8')
    path=tmp_path/'manifest.yaml'
    path.write_text(yaml.safe_dump({'source':source.model_dump(),'file':'rule.md','format':'markdown'}),encoding='utf-8')
    return path


def test_starter_hash_and_exact_pages():
    m,chunks,w=load_manifest(ROOT/'materials/starter/manifest.yaml')
    assert len(chunks)==16 and not w
    assert len({c.chunk_id for c in chunks})==16
    assert all(c.file_sha256==m.sha256 and len(c.text)<=3500 for c in chunks)
    help_chunk=next(c for c in chunks if c.section_key=='help')
    assert (help_chunk.pdf_page_start,help_chunk.pdf_page_end)==(182,183)
    assert 'one of your' in help_chunk.text and 'proficiencies' in help_chunk.text


def test_idempotent_import(store):
    _,chunks,_=load_manifest(ROOT/'materials/starter/manifest.yaml')
    assert store.import_chunks(chunks,reviewed=True)=='unchanged'
    assert len(store.documents())==1
    assert len(store.chunks(chunks[0].source.ruleset_id))==16


def test_non_library_database_rejected(tmp_path):
    p=tmp_path/'game.sqlite3'
    con=sqlite3.connect(p); con.execute('CREATE TABLE campaigns(id TEXT)'); con.commit(); con.close()
    before=p.read_bytes()
    with pytest.raises(ValueError,match='nie baza'):
        LibraryStore(p,create=True)
    assert p.read_bytes()==before


def test_not_initialized(cfg):
    with pytest.raises(ValueError,match='init'):
        LibraryStore(cfg.db_path)


def test_unknown_database_version(cfg):
    s=LibraryStore(cfg.db_path,create=True)
    with s.connection() as c:
        c.execute("UPDATE lib7_meta SET value='999'")
    with pytest.raises(ValueError,match='wersja'):
        LibraryStore(cfg.db_path)


def test_new_documents_hidden_until_review(store,cfg,tmp_path):
    _,cs,_=load_manifest(custom_manifest(tmp_path,ruleset=cfg.ruleset_id))
    store.import_chunks(cs)
    assert len(store.chunks(cfg.ruleset_id))==16
    assert len(store.chunks(cfg.ruleset_id,True))==17
    store.review('custom',True)
    assert len(store.chunks(cfg.ruleset_id))==17
    store.review('custom',False)
    assert len(store.chunks(cfg.ruleset_id))==16


def test_ruleset_filter(store,cfg,tmp_path):
    _,cs,_=load_manifest(custom_manifest(tmp_path,ruleset='dnd_2014'))
    store.import_chunks(cs,reviewed=True)
    assert len(store.chunks(cfg.ruleset_id))==16
    assert len(store.chunks('dnd_2014'))==1


def test_replace_requires_explicit_flag_and_resets_review(store,cfg,tmp_path):
    path=custom_manifest(tmp_path,ruleset=cfg.ruleset_id)
    _,cs,_=load_manifest(path); store.import_chunks(cs,reviewed=True)
    store.save_vectors(cs,[[1.0]+[0.0]*63],'example',64)
    (tmp_path/'rule.md').write_text('# Different\n\nThis is a replacement artificial testing rule.')
    _,new,_=load_manifest(path)
    with pytest.raises(ValueError,match='zmienił'):
        store.import_chunks(new)
    assert store.import_chunks(new,replace=True)=='replaced'
    assert not store.vectors('example')
    assert len(store.chunks(cfg.ruleset_id))==16
    assert cs[0].chunk_id!=new[0].chunk_id


def test_bad_import_rolls_back(store,cfg,tmp_path):
    _,cs,_=load_manifest(custom_manifest(tmp_path,ruleset=cfg.ruleset_id))
    # A primary-key collision after document INSERT must roll the entire transaction back.
    existing=store.chunks(cfg.ruleset_id)[0]
    cs=[cs[0].model_copy(update={'chunk_id':existing.chunk_id})]
    with pytest.raises(sqlite3.IntegrityError):
        store.import_chunks(cs)
    assert len(store.documents())==1


def test_concurrent_same_import(store,cfg,tmp_path):
    _,cs,_=load_manifest(custom_manifest(tmp_path,ruleset=cfg.ruleset_id))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:store.import_chunks(cs),range(2)))
    assert sorted(results)==['imported','unchanged']


def test_deleted_chunk_embedding_does_not_recreate(store,cfg,tmp_path):
    c=store.chunks(cfg.ruleset_id)[0]
    with store.connection() as con:
        con.execute('DELETE FROM lib7_chunks WHERE chunk_id=?',(c.chunk_id,))
    with pytest.raises(ValueError,match='zmienił'):
        store.save_vectors([c],[[1]+[0]*63],'test',64)
    assert not store.vectors('test')


@pytest.mark.parametrize('bad',[[0.0]*64,[float('nan')]*64,[float('inf')]*64,[True]*64,[1]*63,['1']*64])
def test_invalid_vectors(bad):
    with pytest.raises(ValueError):
        normalize_vector(bad,64)


def test_markdown_headings_and_no_fake_pages():
    ss=markdown_sections('# A\n\nFirst rule text is long enough.\n\n## B\n\nSecond rule text is also long enough.')
    assert [s.title for s in ss]==['A','B']
    assert all(s.pdf_page_start is None for s in ss)


def test_chunking_bounds_and_content():
    original=('word '*3000).strip()
    parts=list(paragraphs(original))
    assert len(parts)>1 and all(0<len(s)<=3500 for s in parts)
    assert all('word' in s for s in parts)


@pytest.mark.parametrize('limit',[12,3500])
def test_chunking_exact_limit_word_never_emits_empty_chunk(limit):
    word='x'*limit
    assert list(paragraphs(word+' tail',limit))==[word,'tail']


def test_manifest_path_traversal(tmp_path):
    path=custom_manifest(tmp_path)
    d=yaml.safe_load(path.read_text()); d['file']='../outside.md'; path.write_text(yaml.safe_dump(d))
    with pytest.raises(ValueError,match='katalogiem'):
        load_manifest(path)


def test_checksum_mismatch(tmp_path):
    path=custom_manifest(tmp_path)
    d=yaml.safe_load(path.read_text()); d['sha256']='0'*64; path.write_text(yaml.safe_dump(d))
    with pytest.raises(ValueError,match='SHA-256'):
        load_manifest(path)


def test_yaml_object_is_not_executed(tmp_path):
    p=tmp_path/'bad.yaml'; p.write_text('!!python/object/apply:os.system ["touch PWNED"]')
    with pytest.raises(yaml.YAMLError):
        load_manifest(p)
    assert not (tmp_path/'PWNED').exists()


def test_metadata_extra_fields_rejected():
    with pytest.raises(ValueError):
        Section(key='a',title='A',text='This is long enough to be a rule.',gm_only='secret')


def test_invalid_page_range():
    with pytest.raises(ValueError):
        Section(key='a',title='A',text='This is long enough to be a rule.',pdf_page_start=5,pdf_page_end=2)


@pytest.mark.parametrize('change_after_read',[False,True])
def test_pdf_with_text_and_blank_page(tmp_path,monkeypatch,change_after_read):
    # A tiny real PDF created using pypdf primitives, without OCR or reportlab.
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,NumberObject,DecodedStreamObject
    writer=PdfWriter()
    page=writer.add_blank_page(width=400,height=400)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 20 350 Td (Artificial rule text for testing PDF extraction.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    writer.add_blank_page(width=400,height=400)
    path=custom_manifest(tmp_path)
    with (tmp_path/'rules.pdf').open('wb') as f: writer.write(f)
    m=yaml.safe_load(path.read_text());m.update(file='rules.pdf',format='pdf');path.write_text(yaml.safe_dump(m))
    original=(tmp_path/'rules.pdf').read_bytes()
    if change_after_read:
        import library.importers as importers
        read_limited=importers.read_limited
        def replace_after_read(target,*args,**kwargs):
            raw=read_limited(target,*args,**kwargs)
            if target.name=='rules.pdf':
                target.write_bytes(b'File replaced while importing.')
            return raw
        monkeypatch.setattr(importers,'read_limited',replace_after_read)
    _,chunks,warnings=load_manifest(path)
    assert all(c.file_sha256==digest(original) for c in chunks)
    assert chunks[0].pdf_page_start==1 and chunks[0].printed_page is None
    assert 'Artificial rule' in chunks[0].text
    assert any('2' in x for x in warnings)
