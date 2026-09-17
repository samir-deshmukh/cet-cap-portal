"""
src/cet_cap/queries.py — Core eligibility-lookup query for the
candidate-facing portal. Kept separate from the Streamlit UI (app/Home.py)
so it's unit-testable without spinning up Streamlit, and separate from
db.py so it doesn't care whether it's talking to SQLite or PostgreSQL —
see docs/ARCHITECTURE.md "Application Layers".

Eligibility rule:
    A cutoff row records the percentile of the LAST (lowest-performing)
    candidate admitted to a given (year, round, institute, program,
    category, section, stage) seat. A candidate with percentile >= that
    cutoff would have performed at least as well as the last admitted
    candidate, so they'd be eligible for that same seat. Verified the
    percentile/rank_number relationship empirically against the real
    ingested data (higher percentile consistently pairs with lower
    rank_number) before relying on percentile as the comparison field —
    percentile is what a candidate actually knows about themselves, so
    the query is expressed in terms of it rather than rank.

Known data artifact: exactly 1 row across the whole dataset has
rank_number=0 AND percentile=0.0 (year=2026 round=3, institution_code
03218, MCA, category OPEN) — implausible as a real cutoff (even the
lowest-performing admitted candidate has percentile > 0), and would
otherwise show as "anyone qualifies" for that seat. Excluded here as a
likely extraction artifact rather than a real cutoff; if this pattern
ever affects more than a handful of rows, it needs the same
"quarantine and log it" treatment as ingest.py's other anomalies rather
than a silent filter in every query.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine, text

ZERO_CUTOFF_ARTIFACT_FILTER = 'NOT (c.rank_number = 0 AND c.percentile = 0.0)'


def available_years(engine: Engine) -> list[int]:
    with engine.connect() as conn:
        rows = conn.execute(text('SELECT DISTINCT year FROM cutoffs ORDER BY year DESC'))
        return [r[0] for r in rows]


def available_program_families(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text('SELECT DISTINCT program_family FROM programs ORDER BY program_family'))
        return [r[0] for r in rows]


def available_categories(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text('SELECT base_code FROM base_categories ORDER BY base_code'))
        return [r[0] for r in rows]


def search_cutoffs_for_course(
    engine: Engine,
    percentage: float,
    program_family: str,
) -> pd.DataFrame:
    """The background "load everything at once" fetch behind the 3-input
    search (city, percentage, course — see src/cet_cap/search.py and
    docs/DECISIONS.md "3-input search"). Deliberately NOT filtered by
    category or city — every category/year/round/section/stage row for
    this program family that the candidate's percentage clears, for
    every institute, regardless of location. src/cet_cap/search.py then
    does city filtering and per-college summarization on this in pandas,
    and the same frame (filtered down to one institution_code) becomes
    the detail-panel data on click — no second query per click.
    """
    query = text(f'''
        SELECT
            i.institution_code,
            i.institution_name,
            i.city,
            i.website,
            i.home_university,
            p.program_name_raw AS program_name,
            c.year,
            c.round,
            c.base_category,
            c.is_ladies,
            c.section_code,
            c.stage_code,
            c.percentile AS cutoff_percentile,
            c.rank_number AS cutoff_rank
        FROM cutoffs c
        JOIN institutes i ON i.institution_code = c.institution_code
        JOIN programs p ON p.program_id = c.program_id
        WHERE c.percentile <= :percentage AND p.program_family = :program_family
              AND {ZERO_CUTOFF_ARTIFACT_FILTER}
    ''')
    with engine.connect() as conn:
        return pd.read_sql_query(
            query, conn, params={'percentage': percentage, 'program_family': program_family})


def seat_matrix_for_institute(
    engine: Engine,
    institution_code: str,
    program_family: str,
) -> pd.DataFrame:
    """Seat matrix for the detail panel: one row per (allocation_lane,
    base_category, is_ladies) at the MOST RECENT capture_year on record
    for this institute/program combination — older years are dropped
    rather than summed, since summing seat counts across years would
    overstate current capacity.

    is_total rows are NOT excluded — they're returned with
    is_total=True so the UI can show the lane's actual sanctioned total
    as its own clearly-labeled row instead of either hiding it or
    silently adding it into the per-category rows (which would
    double-count capacity).
    """
    query = text('''
        SELECT allocation_lane, base_category, is_total, is_ladies, seats, capture_year
        FROM seats
        WHERE institution_code = :institution_code AND program_family = :program_family
              AND capture_year = (
                  SELECT MAX(capture_year) FROM seats
                  WHERE institution_code = :institution_code
                        AND program_family = :program_family
              )
        ORDER BY allocation_lane, is_total, base_category
    ''')
    with engine.connect() as conn:
        return pd.read_sql_query(
            query, conn,
            params={'institution_code': institution_code, 'program_family': program_family})
def find_eligible_colleges(
    engine: Engine,
    percentile: float,
    base_category: str,
    program_family: str,
    is_ladies: bool = False,
    year: int | None = None,
    round_no: int | None = None,
) -> pd.DataFrame:
    """Colleges/programs a candidate with the given percentile and
    category would qualify for, most competitive (highest cutoff) first.

    Filters to the exact (base_category, is_ladies) combination the
    candidate belongs to — this does NOT fall back to broader categories
    (e.g. a candidate in a reserved category is not shown OPEN-category
    cutoffs), since that's a distinct, more permissive eligibility rule
    a candidate would need to explicitly ask for, not a default.
    """
    where = [
        'c.percentile <= :percentile',
        'c.base_category = :base_category',
        'p.program_family = :program_family',
        'c.is_ladies = :is_ladies',
        ZERO_CUTOFF_ARTIFACT_FILTER,
    ]
    params: dict = {
        'percentile': percentile,
        'base_category': base_category,
        'program_family': program_family,
        'is_ladies': int(is_ladies),
    }

    if year is not None:
        where.append('c.year = :year')
        params['year'] = year
    if round_no is not None:
        where.append('c.round = :round_no')
        params['round_no'] = round_no

    query = text(f'''
        SELECT
            i.institution_name,
            i.institution_code,
            p.program_name_raw AS program_name,
            c.year,
            c.round,
            c.stage_code,
            c.section_code,
            c.percentile AS cutoff_percentile,
            c.rank_number AS cutoff_rank
        FROM cutoffs c
        JOIN institutes i ON i.institution_code = c.institution_code
        JOIN programs p ON p.program_id = c.program_id
        WHERE {' AND '.join(where)}
        ORDER BY c.percentile DESC, i.institution_name
    ''')
    with engine.connect() as conn:
        return pd.read_sql_query(query, conn, params=params)
