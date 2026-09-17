"""Regression tests for college-specific drawer metadata."""
from __future__ import annotations
import json, re, sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
COURSES=("BBA","BCA","MBA","MCA")

def search():
    text=(ROOT/'site/data/search_index.js').read_text()
    return json.loads(text.split('window.__SEARCH_INDEX__ = ',1)[1].split(';',1)[0])

def test_drawer_does_not_show_duplicate_specialization_selector():
    html=(ROOT/'site/index.html').read_text()
    assert 'id="dTrendBranch"' not in html
    assert '<label class="field-label">Specialization</label>' not in html
    assert 'id="dTrendCategory"' in html and 'id="dTrendQuota"' in html

def test_drawer_category_and_quota_options_match_db_for_every_college():
    idx={(r['institution_code'],r['course']):r for r in search()}
    conn=sqlite3.connect(ROOT/'db/cet_cap.db')
    try:
        for course in COURSES:
            insts=[r[0] for r in conn.execute("SELECT DISTINCT c.institution_code FROM cutoffs c JOIN programs p ON p.program_id=c.program_id WHERE p.program_family=? AND NOT (c.rank_number=0 AND c.percentile=0.0)",(course,))]
            for college in insts:
                if (college, course) not in idx:
                    continue  # search index intentionally skips orphan institute codes
                d=idx[(college,course)]['drawer']
                for ladies_key,ladies in [('0',0),('1',1)]:
                    expected={}
                    for cat,sec in conn.execute("SELECT DISTINCT c.base_category,c.section_code FROM cutoffs c JOIN programs p ON p.program_id=c.program_id WHERE p.program_family=? AND c.institution_code=? AND c.is_ladies=? AND NOT (c.rank_number=0 AND c.percentile=0.0)",(course,college,ladies)):
                        expected.setdefault(cat,set()).add(sec)
                    actual={x['v']:set(x['q']) for x in d['c'][ladies_key]}
                    assert actual==expected,(course,college,ladies)
    finally: conn.close()
