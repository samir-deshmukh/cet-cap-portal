"""
Regression test for scripts/export_search_index.py.

Guards against a real bug found on review: the original query computed
MIN(percentile) and MAX(rank_number) as two INDEPENDENT aggregates
within each (institution_code, program_family, year) group. Unlike
export_trends_json.py's per-round grouping (where that pairing is
guaranteed — verified separately), this export collapses across
MULTIPLE ROUNDS within a year, and the two extremes came from
different underlying rows in 184 of 2,597 multi-row groups (~7%) —
e.g. a college's exported "cutoff" (rank) and "percentile" described
two different students who were never admitted together. Fixed with a
window function so the exported rank always comes from the exact row
that has the exported percentile.

Run: pytest tests/test_export_search_index.py
"""

from __future__ import annotations

import json
import subprocess
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )


@pytest.fixture(scope='module')
def db_and_index(tmp_path_factory):
    db_path = tmp_path_factory.mktemp('db') / 'cet_cap_test.db'
    out_dir = tmp_path_factory.mktemp('out')
    _run(['scripts/seed_reference_tables.py', '--db', str(db_path)])
    _run(['scripts/ingest.py', 'data/raw/', '--db', str(db_path)])
    _run(['scripts/ingest_seats.py', 'data/raw/seats/', '--year', '2026',
          '--db', str(db_path)])
    _run(['scripts/enrich_institutes.py', 'data/reference/institute_contacts.csv',
          '--db', str(db_path)])
    _run(['scripts/export_search_index.py', '--db', str(db_path), '--out', str(out_dir)])
    return db_path, out_dir / 'search_index.json'


def test_every_record_has_1433_total(db_and_index):
    _, index_path = db_and_index
    records = json.loads(index_path.read_text())
    assert len(records) == 1433


def test_rank_and_percentile_always_co_occur_in_a_real_row(db_and_index):
    """The exact bug this test exists to catch: cutoff (rank) and
    percentile must always come from the SAME real cutoffs row for
    that institution/course/year/OPEN/non-ladies — never a rank from
    one round paired with a percentile from a different round."""
    db_path, index_path = db_and_index
    records = json.loads(index_path.read_text())
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    mismatches = []
    for r in records:
        year = r['history'][-1]['year']
        cur.execute(
            '''SELECT 1 FROM cutoffs c JOIN programs p ON p.program_id = c.program_id
               WHERE c.institution_code = ? AND p.program_family = ? AND c.year = ?
                     AND c.base_category = 'OPEN' AND c.is_ladies = 0
                     AND c.percentile = ? AND c.rank_number = ?''',
            (r['institution_code'], r['course'], year, r['percentile'], r['cutoff']),
        )
        if cur.fetchone() is None:
            mismatches.append(r['id'])
    conn.close()
    assert mismatches == []


def test_institutes_without_any_open_nonladies_row_are_excluded_not_guessed(db_and_index):
    db_path, index_path = db_and_index
    records = json.loads(index_path.read_text())
    conn = sqlite3.connect(db_path)
    total_institutes = conn.execute('SELECT COUNT(*) FROM institutes').fetchone()[0]
    conn.close()

    covered = {r['institution_code'] for r in records}
    assert total_institutes - len(covered) == 46


def test_graph_history_is_per_year_raw_min_max(db_and_index):
    """Graph ranges must be independently calculated for each year from
    raw OPEN/non-ladies cutoff rows, not from a single all-years range."""
    db_path, index_path = db_and_index
    records = json.loads(index_path.read_text())
    record = next(r for r in records if r['id'] == '06307_MBA')

    conn = sqlite3.connect(db_path)
    expected = [
        {'year': year, 'low': low, 'high': high}
        for year, low, high in conn.execute(
            '''SELECT c.year, MIN(c.percentile), MAX(c.percentile)
               FROM cutoffs c JOIN programs p ON p.program_id = c.program_id
               WHERE c.institution_code = '06307' AND p.program_family = 'MBA'
                 AND c.base_category = 'OPEN' AND c.is_ladies = 0
                 AND NOT (c.rank_number = 0 AND c.percentile = 0.0)
               GROUP BY c.year ORDER BY c.year'''
        )
    ]
    conn.close()
    assert record['graph_history'] == expected[-4:]
    # The graph must not silently become the all-years college-list range.
    assert record['graph_history'] != [
        {'year': 0, 'low': min(x['low'] for x in expected),
         'high': max(x['high'] for x in expected)}
    ]
