"""Regression tests for the fast drawer runtime path."""
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COURSES = ("BBA", "BCA", "MBA", "MCA")

def _search():
    
    text=(ROOT/'site/data/search_index.js').read_text()
    marker='window.__SEARCH_INDEX__ = '
    return json.loads(text.split(marker,1)[1].split(';',1)[0])

def test_search_index_and_inline_copy_match():
    html=(ROOT/'site/index.html').read_text()
    m=re.search(r'window\.__SEARCH_INDEX__\s*=\s*(\[.*?\]);',html,re.S)
    assert m
    inline=json.loads(m.group(1))
    assert inline == _search()

def test_drawer_metadata_is_immediate_and_college_specific():
    rows=_search()
    r=next(x for x in rows if x['id']=='06307_MBA')
    d=r['drawer']
    assert d['y']==[2025,2026]
    for ladies_key in ('0','1'):
        for cat in d['c'][ladies_key]:
            assert cat['v']
            assert isinstance(cat['q'], list)
    # The metadata must not invent a category/section: compare against the
    # canonical SQLite rows for this exact college/course.
    import sqlite3
    conn=sqlite3.connect(ROOT/'db/cet_cap.db')
    raw=conn.execute("SELECT c.is_ladies,c.base_category,c.section_code FROM cutoffs c JOIN programs p ON p.program_id=c.program_id WHERE p.program_family='MBA' AND c.institution_code='06307' AND NOT (c.rank_number=0 AND c.percentile=0.0)").fetchall()
    conn.close()
    rows=[{'ladies':bool(l),'category':c,'section':s} for l,c,s in raw]
    for ladies_key, ladies in [('0',False),('1',True)]:
        expected={}
        for x in rows:
            if x['ladies']!=ladies: continue
            expected.setdefault(x['category'],set()).add(x['section'])
        actual={x['v']:set(x['q']) for x in d['c'][ladies_key]}
        assert actual==expected

def test_college_year_payload_is_small_and_exact_for_06307_mba():
    for year in (2025,2026):
        p=ROOT/f'site/data/courses/MBA/{year}/06307.js'
        assert p.exists()
        text=p.read_text()
        assert p.stat().st_size < 50_000
        marker=f'window.__COLLEGE_YEAR_DATA__[{json.dumps(f"MBA:{year}:06307")}]='
        assert marker in text
        payload=json.loads(text.split(marker,1)[1].rstrip(';.\n'))
        import sqlite3
        conn=sqlite3.connect(ROOT/'db/cet_cap.db')
        db=conn.execute("SELECT c.program_id,c.institution_code,c.base_category,is_ladies,section_code,year,round,stage_code,rank_number,percentile,raw_category FROM cutoffs c JOIN programs p ON p.program_id=c.program_id WHERE p.program_family='MBA' AND c.institution_code='06307' AND c.year=? AND NOT (c.rank_number=0 AND c.percentile=0.0) ORDER BY c.program_id,c.institution_code,c.year,c.round,c.section_code,c.stage_code,c.base_category,c.is_ladies,c.source_page,c.rank_number", (year,)).fetchall()
        conn.close()
        expected=[{'branch':pid,'college':inst,'category':cat,'ladies':bool(ladies),'section':section,'year':yr,'round':rnd,'stage':stage,'rank':rank,'percentile':round(pct,7),'raw_category':raw} for pid,inst,cat,ladies,section,yr,rnd,stage,rank,pct,raw in db]
        assert payload['pdf_rows']==expected
