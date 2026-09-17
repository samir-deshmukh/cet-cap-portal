"""
Unit tests for scripts/validate_seats.py and scripts/ingest_seats.py
normalisation logic. Every parser rule gets a test (docs/RULES.md
"Testing Rules"); every real-data bug found on 2026-09-12 gets a
regression test (docs/TESTING.md).

Run: pytest tests/test_seats_normalization.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from validate_seats import (  # noqa: E402
    KNOWN_BAD_CATEGORY_VALUES,
    TOTAL_MARKER,
    normalize_category,
    parse_filename,
)
from ingest_seats import canonical_institution_code  # noqa: E402

ALIAS_MAP = {'VJDT': 'VJ/DT', 'VJ/DT': 'VJ/DT'}


def test_normalize_category_vjdt_no_slash():
    # BBA/MBA/MCA seat files spell it 'VJDT' (regression: this must
    # resolve to the same canonical code as the cutoffs whitelist).
    assert normalize_category('VJDT', ALIAS_MAP) == 'VJ/DT'


def test_normalize_category_vjdt_with_slash():
    # BCA seat file already matches the cutoffs whitelist spelling.
    assert normalize_category('VJ/DT', ALIAS_MAP) == 'VJ/DT'


def test_normalize_category_passthrough_for_plain_codes():
    for code in ('OPEN', 'SC', 'ST', 'OBC', 'SEBC', 'NTB', 'NTC', 'NTD'):
        assert normalize_category(code, ALIAS_MAP) == code


def test_normalize_category_passthrough_for_total_marker():
    # 'Total' is a subtotal row, not a category — normalize_category
    # must not be asked to resolve it against the category whitelist
    # (callers check `!= TOTAL_MARKER` first), but it must not crash
    # or silently rewrite it either.
    assert normalize_category(TOTAL_MARKER, ALIAS_MAP) == TOTAL_MARKER


def test_known_bad_category_quarantined_not_whitelisted():
    # Regression: MBA_SM.csv institution_code=02508 has 'Services) SC'
    # bled in from institution/status text — must stay quarantined,
    # never silently mapped to 'SC'.
    assert 'Services) SC' in KNOWN_BAD_CATEGORY_VALUES
    assert normalize_category('Services) SC', ALIAS_MAP) == 'Services) SC'


@pytest.mark.parametrize('stem,expected_family', [
    ('BBA_SM', 'BBA'), ('BCA_SM', 'BCA'),
    ('MBA_SM', 'MBA'), ('MCA_SM', 'MCA'),
    ('bba_sm', 'BBA'),  # case-insensitive
])
def test_parse_filename_recognises_family(stem, expected_family, tmp_path):
    meta = parse_filename(tmp_path / f'{stem}.csv')
    assert meta is not None
    assert meta['family'] == expected_family


def test_parse_filename_rejects_unrelated_file(tmp_path):
    assert parse_filename(tmp_path / 'random_file.csv') is None


def test_canonical_institution_code_prefers_choice_code_prefix():
    # institution_code column round-trips through Excel/pandas as an
    # int and loses its leading zero in some tooling; choice_code
    # (a string) keeps it. Regression for that mismatch.
    assert canonical_institution_code('2113', '0211310110') == '02113'
    assert canonical_institution_code('02113', '0211310110') == '02113'


def test_canonical_institution_code_flags_real_mismatch():
    # A genuine mismatch (not just a missing leading zero) must raise,
    # not be silently coerced to whichever value "looks more padded".
    with pytest.raises(ValueError):
        canonical_institution_code('99999', '0211310110')
