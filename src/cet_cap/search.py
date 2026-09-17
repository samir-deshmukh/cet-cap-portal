"""
src/cet_cap/search.py — Pure aggregation logic for the 3-input search
(city, percentage, course). Deliberately has no DB access of its own —
src/cet_cap/queries.py fetches the raw rows, this module turns them into
the "one row per college" list the first screen shows. Kept separate so
it's testable with plain DataFrames, no database required.

Design (see docs/DECISIONS.md "3-input search: city/percentage/course,
list-then-detail UX" for the full reasoning):
    - No category input on screen 1. The list's headline number mixes
      every category together (MAX percentile cleared, any category) so
      a college isn't invisible just because its OPEN cutoff doesn't
      qualify — the actual category-specific truth is one click away
      in the detail panel. The UI must label this as "any category",
      never implying a guarantee.
    - City has no real database column (see enrich_institutes.py) —
      matching is a case-insensitive substring match against the
      enriched `city` field where available.
"""

from __future__ import annotations

import pandas as pd


def filter_by_city(df: pd.DataFrame, city: str) -> pd.DataFrame:
    """`city` matches (case-insensitive, substring) against the `city`
    column. Rows with no enriched city (institutes.city IS NULL) are
    excluded whenever a city filter is given — showing them would be
    guessing, not filtering. Empty/blank `city` input means "no filter",
    not "only unenriched rows"."""
    if not city or not city.strip():
        return df
    mask = df['city'].fillna('').str.contains(city.strip(), case=False, na=False)
    return df[mask]


def summarize_colleges(df: pd.DataFrame) -> pd.DataFrame:
    """One row per college: the toughest (highest) cutoff the candidate
    still clears, across every category/year/round/section/stage present
    in `df` (df is expected to already be filtered to
    percentile <= candidate's percentage and the chosen program_family —
    see queries.search_cutoffs_for_course). Also reports how many
    distinct years that college has any record in, at or below the
    threshold, and the total number of matching rows (for the detail
    panel to reference).

    Returns columns: institution_code, institution_name, city, website,
    highest_cutoff, years_on_record, matching_rows — sorted most
    competitive first, i.e. descending by highest_cutoff.
    """
    columns = ['institution_code', 'institution_name', 'city', 'website',
               'highest_cutoff', 'years_on_record', 'matching_rows']
    if df.empty:
        return pd.DataFrame(columns=columns)

    grouped = df.groupby('institution_code')
    idx = grouped['cutoff_percentile'].idxmax()
    best_rows = df.loc[idx].set_index('institution_code')

    summary = pd.DataFrame({
        'institution_name': best_rows['institution_name'],
        'city': best_rows['city'],
        'website': best_rows['website'],
        'highest_cutoff': best_rows['cutoff_percentile'],
        'years_on_record': grouped['year'].nunique(),
        'matching_rows': grouped.size(),
    }).reset_index()

    return summary.sort_values('highest_cutoff', ascending=False).reset_index(drop=True)
