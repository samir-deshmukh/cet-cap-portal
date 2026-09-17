"""Regression tests for the drawer's PDF-style all-data export."""
from __future__ import annotations
import json, sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
COURSES=("BBA","BCA","MBA","MCA")

def _load(path: Path, key: str):
    text=path.read_text()
    marker=f'window.__COLLEGE_YEAR_DATA__[{json.dumps(key)}]='
    assert marker in text
    return json.loads(text.split(marker,1)[1].rstrip(';.\n'))

def test_public_college_year_rows_equal_nonblank_db_rows():
    conn=sqlite3.connect(ROOT/'db/cet_cap.db')
    for course in COURSES:
        db_count=conn.execute("""SELECT COUNT(*) FROM cutoffs c JOIN programs p ON p.program_id=c.program_id WHERE p.program_family=? AND NOT (c.rank_number=0 AND c.percentile=0.0)""",(course,)).fetchone()[0]
        total=0
        for year in sorted({r[0] for r in conn.execute("SELECT DISTINCT c.year FROM cutoffs c JOIN programs p ON p.program_id=c.program_id WHERE p.program_family=? AND NOT (c.rank_number=0 AND c.percentile=0.0)",(course,)).fetchall()}):
            colleges=[r[0] for r in conn.execute("SELECT DISTINCT c.institution_code FROM cutoffs c JOIN programs p ON p.program_id=c.program_id WHERE p.program_family=? AND c.year=? AND NOT (c.rank_number=0 AND c.percentile=0.0)",(course,year)).fetchall()]
            for college in colleges:
                d=_load(ROOT/'site/data/courses'/course/str(year)/(college+'.js'),f'{course}:{year}:{college}')
                total+=len(d['pdf_rows'])
        assert total==db_count
    conn.close()

def test_06307_mba_pdf_rows_match_raw_database_values():
    rows=[]
    for year in (2025,2026):
        d=_load(ROOT/'site/data/courses/MBA'/str(year)/'06307.js',f'MBA:{year}:06307')
        rows.extend([r for r in d['pdf_rows'] if not r['ladies']])
    conn=sqlite3.connect(ROOT/'db/cet_cap.db')
    db_rows=conn.execute("""SELECT c.program_id,c.institution_code,c.base_category,c.is_ladies,c.section_code,c.year,c.round,c.stage_code,c.rank_number,c.percentile,c.raw_category FROM cutoffs c JOIN programs p ON p.program_id=c.program_id WHERE p.program_family='MBA' AND c.institution_code='06307' AND c.is_ladies=0 AND NOT (c.rank_number=0 AND c.percentile=0.0) ORDER BY c.program_id,c.institution_code,c.year,c.round,c.section_code,c.stage_code,c.base_category,c.is_ladies,c.source_page,c.rank_number""").fetchall()
    conn.close()
    exported=[(r['branch'],r['college'],r['category'],r['ladies'],r['section'],r['year'],r['round'],r['stage'],r['rank'],r['percentile'],r['raw_category']) for r in rows]
    expected=[(pid,inst,cat,bool(ladies),section,year,rnd,stage,rank,round(pct,7),raw) for pid,inst,cat,ladies,section,year,rnd,stage,rank,pct,raw in db_rows]
    assert exported==expected
