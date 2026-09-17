"""
Unit tests for src/cet_cap/search.py — pure pandas logic, no database.

Run: pytest tests/test_search.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cet_cap.search import filter_by_city, summarize_colleges  # noqa: E402


def _row(institution_code, institution_name, city, cutoff_percentile,
         year=2026, website='http://example.com'):
    return {
        'institution_code': institution_code, 'institution_name': institution_name,
        'city': city, 'website': website, 'cutoff_percentile': cutoff_percentile,
        'year': year,
    }


def test_filter_by_city_is_case_insensitive_substring():
    df = pd.DataFrame([
        _row('A1', 'College A', 'Pune', 90.0),
        _row('A2', 'College B', 'PUNE EAST', 85.0),
        _row('A3', 'College C', 'Mumbai', 88.0),
    ])
    result = filter_by_city(df, 'pune')
    assert set(result['institution_code']) == {'A1', 'A2'}


def test_filter_by_city_empty_string_means_no_filter():
    df = pd.DataFrame([_row('A1', 'College A', 'Pune', 90.0), _row('A2', 'College B', None, 80.0)])
    assert len(filter_by_city(df, '')) == 2
    assert len(filter_by_city(df, '   ')) == 2


def test_filter_by_city_excludes_rows_with_no_enriched_city():
    """A row with city=NULL should never match a real city search —
    that would be a guess, not a filter."""
    df = pd.DataFrame([_row('A1', 'College A', None, 90.0)])
    assert len(filter_by_city(df, 'Pune')) == 0


def test_summarize_picks_highest_cutoff_per_college():
    df = pd.DataFrame([
        _row('A1', 'College A', 'Pune', 70.0, year=2024),
        _row('A1', 'College A', 'Pune', 85.0, year=2025),
        _row('A1', 'College A', 'Pune', 60.0, year=2026),
    ])
    summary = summarize_colleges(df)
    assert len(summary) == 1
    assert summary.iloc[0]['highest_cutoff'] == 85.0
    assert summary.iloc[0]['years_on_record'] == 3
    assert summary.iloc[0]['matching_rows'] == 3


def test_summarize_sorts_most_competitive_first():
    df = pd.DataFrame([
        _row('A1', 'College A', 'Pune', 70.0),
        _row('B1', 'College B', 'Mumbai', 95.0),
        _row('C1', 'College C', 'Nashik', 80.0),
    ])
    summary = summarize_colleges(df)
    assert list(summary['institution_code']) == ['B1', 'C1', 'A1']


def test_summarize_empty_input_returns_empty_with_expected_columns():
    summary = summarize_colleges(pd.DataFrame(columns=[
        'institution_code', 'institution_name', 'city', 'website',
        'cutoff_percentile', 'year',
    ]))
    assert summary.empty
    assert list(summary.columns) == [
        'institution_code', 'institution_name', 'city', 'website',
        'highest_cutoff', 'years_on_record', 'matching_rows',
    ]


def test_summarize_one_row_per_college_even_with_many_matching_rows():
    df = pd.DataFrame([
        _row('A1', 'College A', 'Pune', 70.0 + i, year=2024 + (i % 3))
        for i in range(20)
    ])
    summary = summarize_colleges(df)
    assert len(summary) == 1
    assert summary.iloc[0]['matching_rows'] == 20
