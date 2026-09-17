"""Regression tests for the fast course/year/college runtime data architecture."""
from __future__ import annotations
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
COURSES = ("BBA", "BCA", "MBA", "MCA")

def _load(path: Path, key: str):
    text=path.read_text()
    marker=f'window.__COLLEGE_YEAR_DATA__[{json.dumps(key)}]='
    assert marker in text
    return json.loads(text.split(marker,1)[1].rstrip(';.\n'))

def test_no_course_wide_cutoff_payloads_are_shipped():
    data=ROOT/'site/data'
    assert not list(data.glob('cutoffs_*.json'))
    assert not list(data.glob('cutoffs_*.js'))
    assert not (data/'college_cutoffs').exists()

def test_college_year_files_exist_and_are_small():
    for course in COURSES:
        course_dir=ROOT/'site/data/courses'/course
        assert course_dir.exists()
        years=[int(p.name) for p in course_dir.iterdir() if p.is_dir()]
        assert years
        for year in years:
            files=list((course_dir/str(year)).glob('*.js'))
            assert files
            assert all(p.stat().st_size < 100_000 for p in files)

def test_all_college_year_files_cover_exact_db_rows():
    import sqlite3
    conn=sqlite3.connect(ROOT/'db/cet_cap.db')
    for course in COURSES:
        db_rows=[]
        query="""SELECT c.program_id,c.institution_code,c.base_category,c.is_ladies,c.section_code,c.year,c.round,c.stage_code,c.rank_number,c.percentile,c.raw_category FROM cutoffs c JOIN programs p ON p.program_id=c.program_id WHERE p.program_family=? AND NOT (c.rank_number=0 AND c.percentile=0.0) ORDER BY c.program_id,c.institution_code,c.year,c.round,c.section_code,c.stage_code,c.base_category,c.is_ladies,c.source_page,c.rank_number"""
        raw=conn.execute(query,(course,)).fetchall()
        expected=[{'branch':pid,'college':inst,'category':cat,'ladies':bool(ladies),'section':section,'year':year,'round':rnd,'stage':stage,'rank':rank,'percentile':round(pct,7),'raw_category':raw_cat} for pid,inst,cat,ladies,section,year,rnd,stage,rank,pct,raw_cat in raw]
        exported=[]
        for year in sorted({int(r['year']) for r in expected}):
            colleges=sorted({str(r['college']) for r in expected if int(r['year'])==year})
            for college in colleges:
                d=_load(ROOT/'site/data/courses'/course/str(year)/(college+'.js'),f'{course}:{year}:{college}')
                exported.extend(d['pdf_rows'])
        key=lambda r: json.dumps(r,sort_keys=True)
        assert sorted(exported,key=key)==sorted(expected,key=key)
    conn.close()
