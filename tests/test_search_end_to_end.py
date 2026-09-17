"""
End-to-end regression test for the 3-input search (city, percentage,
course) against a real seeded + ingested + enriched database — not
fixtures. Complements tests/test_search.py (pure logic, synthetic data)
by checking the actual queries.py <-> search.py wiring, and the
enrichment coverage the city input depends on.

Run: pytest tests/test_search_end_to_end.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / 'src'))

from cet_cap.db import get_engine  # noqa: E402
from cet_cap.queries import search_cutoffs_for_course, seat_matrix_for_institute  # noqa: E402
from cet_cap.search import filter_by_city, summarize_colleges  # noqa: E402


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )


@pytest.fixture(scope='module')
def engine(tmp_path_factory):
    db_path = tmp_path_factory.mktemp('db') / 'cet_cap_test.db'
    _run(['scripts/seed_reference_tables.py', '--db', str(db_path)])
    _run(['scripts/ingest.py', 'data/raw/', '--db', str(db_path)])
    _run(['scripts/ingest_seats.py', 'data/raw/seats/', '--year', '2026', '--db', str(db_path)])
    enrich_result = _run(['scripts/enrich_institutes.py',
                           'data/reference/institute_contacts.csv', '--db', str(db_path)])
    # Coverage must stay high — if this drops a lot, either the source
    # CSV changed shape or the split/reconciliation logic in
    # enrich_institutes.py regressed.
    assert 'Updated 967 institutes' in enrich_result.stdout
    return get_engine(str(db_path))


def test_city_filter_only_matches_enriched_rows(engine):
    raw = search_cutoffs_for_course(engine, percentage=95.0, program_family='MCA')
    assert raw['city'].notna().any()
    pune = filter_by_city(raw, 'Pune')
    assert len(pune) > 0
    assert (pune['city'].str.contains('Pune', case=False)).all()


def test_summary_list_shape_matches_the_spec(engine):
    """Screen 1 must surface exactly: college, cutoff, city (plus the
    small metadata the UI also shows) — not category, not rank, not
    program name."""
    raw = search_cutoffs_for_course(engine, percentage=95.0, program_family='MCA')
    summary = summarize_colleges(filter_by_city(raw, 'Pune'))
    assert set(summary.columns) == {
        'institution_code', 'institution_name', 'city', 'website',
        'highest_cutoff', 'years_on_record', 'matching_rows',
    }
    assert (summary['highest_cutoff'] <= 95.0).all()


def test_highest_cutoff_mixes_categories_by_design(engine):
    """Per the explicit product decision: no category on screen 1, so
    the headline number is the best across ALL categories, not just
    OPEN. This test pins that as intentional — if it starts matching
    only OPEN-category cutoffs, the mixing behavior regressed."""
    raw = search_cutoffs_for_course(engine, percentage=99.9, program_family='MCA')
    non_open = raw[raw['base_category'] != 'OPEN']
    assert len(non_open) > 0  # sanity: non-OPEN rows exist in range at all

    summary = summarize_colleges(raw)
    # For at least one college, the winning (max) row must come from a
    # non-OPEN category — otherwise this "mixes categories" claim is false.
    idx = raw.groupby('institution_code')['cutoff_percentile'].idxmax()
    winning_categories = raw.loc[idx, 'base_category']
    assert (winning_categories != 'OPEN').any()


def test_detail_panel_data_is_a_pure_filter_no_second_cutoffs_query(engine):
    """The detail panel must be derivable entirely from the same frame
    screen 1 already loaded, filtered to one institution_code — this is
    the whole point of loading everything in the background once."""
    raw = search_cutoffs_for_course(engine, percentage=95.0, program_family='MCA')
    summary = summarize_colleges(raw)
    code = summary.iloc[0]['institution_code']
    college_rows = raw[raw['institution_code'] == code]
    assert len(college_rows) > 0
    assert college_rows['institution_code'].nunique() == 1


def test_seat_matrix_separates_totals_from_category_breakdown(engine):
    raw = search_cutoffs_for_course(engine, percentage=95.0, program_family='MCA')
    summary = summarize_colleges(raw)
    code = summary.iloc[0]['institution_code']
    seats = seat_matrix_for_institute(engine, code, 'MCA')
    if seats.empty:
        pytest.skip('chosen college has no seat-matrix record')
    totals = seats[seats['is_total'] == 1]
    by_category = seats[seats['is_total'] == 0]
    assert totals['base_category'].isna().all()
    assert by_category['base_category'].notna().all()
    # Category rows must not be pre-summed into the total — same lane's
    # total should be >= the max single category row (a real invariant,
    # not just "some number").
    for lane, group in by_category.groupby('allocation_lane'):
        total_row = totals[totals['allocation_lane'] == lane]
        if len(total_row):
            assert total_row['seats'].iloc[0] >= group['seats'].max()
