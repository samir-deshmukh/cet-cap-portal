# Testing Strategy

## Testing Philosophy
Every parser rule gets a test. Every bug fix gets a regression
test. Tests never touch production data.

## Test Stack
- pytest 8.3
- pandas for fixture loading

## Unit Testing
- parse_category: one case per pattern (GOPENH, LSCS, PWDROBCH,
  MI, EWS, ORPHAN variants, malformed, and now GNTAH/LNTAH/PWD/
  DEFROBCS — the real-data cases found on 2026-09-12).
- STAGE_MAP, SECTION_MAP lookups.
- Forward-fill: a fixture with a multi-row block where
  institution_code/institution_name/program_code/program_name/
  status/section are blank on rows 2+ must fill correctly from
  row 1. A fixture with home_university or stage blank for an
  ENTIRE file must NOT be forward-filled (regression test for the
  2026-09-12 fix).
- KNOWN_BAD_CATEGORY_VALUES: a fixture row with `category =
  "GOVERNMENT"` must be quarantined, not whitelisted or dropped.
- Seat matrix (tests/test_seats_normalization.py, added
  2026-09-12): normalize_category resolves 'VJDT' and 'VJ/DT' to
  the same base code and passes 'Total' through unchanged;
  parse_filename recognises BCA/BBA/MBA/MCA _SM files
  case-insensitively and rejects unrelated filenames;
  canonical_institution_code prefers choice_code's zero-padded
  prefix over the institution_code column (which loses its leading
  zero through some tooling) and raises on a genuine mismatch;
  'Services) SC' stays in KNOWN_BAD_CATEGORY_VALUES rather than
  being silently mapped to 'SC'.

## Integration Testing
- Run ingest on tests/fixtures/mini.csv (5 rows).
- Assert row counts in cutoffs and ingest_errors.
- Assert quarantined anomaly rows land in ingest_errors, never in
  cutoffs, and never silently vanish (row count in must equal row
  count out across cutoffs + ingest_errors combined).
- Seat matrix (verified manually on 2026-09-12 against real data,
  pending a proper pytest fixture): ran ingest_seats.py end-to-end
  against all 70,269 real rows into a fresh SQLite DB seeded with
  base_categories + institutes — 69,655 inserted, 4 quarantined, 610
  held out on a missing-institute foreign key (9 institutes with no
  cutoffs-side record), 0 unaccounted for
  (read == inserted + skipped + failed for every file).

## End-to-End Testing
Manual: run Streamlit, walk through Flow A and Flow B.

## API Testing
N/A — no external API.

## Database Testing
- Use a fresh SQLite file per test run.
- Never use production DATABASE_URL in tests.

## Mocking Strategy
No network mocks needed.

## Test Data
- tests/fixtures/mini.csv — 5 hand-written rows.
- Never commit real CET data as fixtures.

## Test Naming Conventions
test_<module>_<case>.py
test_parse_category_gopenh(), etc.

## Coverage Requirements
- parse_category: 100%.
- Ingest pipeline: at least one happy path and one failure path.
- Forward-fill column-selection logic: 100% (this is where the
  2026-09-12 bug was — a column that's legitimately all-blank vs
  a column that's a per-block continuation gap).

## Required Checks
Before every commit:
- `python scripts/validate_data.py data/raw/` exits 0.
- `python scripts/validate_seats.py data/raw/seats/` exits 0.
- pytest

## CI Testing
GitHub Actions: pytest on push.

## Definition of Done
- New code has tests.
- All tests pass locally and in CI.
- No regressions in existing tests.
