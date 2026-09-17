"""
End-to-end regression tests for three bugs found across two review passes
on the seat-matrix ingest path (docs/DECISIONS.md has the full writeup for
each):

1. `institutes` / `base_categories` were never seeded anywhere (ingest.py
   for cutoffs doesn't exist yet), so ingest_seats.py failed almost every
   row with "FOREIGN KEY constraint failed" instead of the 610 documented
   orphans. Fixed by scripts/seed_reference_tables.py.

2. The `seats` UNIQUE key was missing `program_family`. The same
   `choice_code` string is reused across program families (BCA/BBA/MBA/
   MCA) with genuinely different seat counts, so rows from one family
   silently overwrote rows from another via INSERT OR IGNORE — and the
   script's own "inserted" counter didn't notice, because it counted
   "no exception raised" as success instead of checking cursor.rowcount.

3. `raw_gender` was a nullable column used inside that same UNIQUE key.
   SQL treats NULL as distinct from NULL, so the ~62% of rows with no
   gender split (PWD/DEF/HU/OHU/State-Level lanes) bypassed the UNIQUE
   constraint entirely — re-running ingest_seats.py on an already-loaded
   database would silently duplicate all of them. Fixed by making
   raw_gender NOT NULL DEFAULT '' end to end.

A fresh clone must be able to run, in order:
    python scripts/seed_reference_tables.py --db <db>
    python scripts/ingest_seats.py data/raw/seats/ --year 2026 --db <db>
    python scripts/ingest_seats.py data/raw/seats/ --year 2026 --db <db>  # again

and get 69,655 real rows in `seats` after the FIRST run, with the SECOND
run reporting 0 new inserts and 69,655 duplicates against the same rows —
not a growing table.

Run: pytest tests/test_seed_and_ingest_end_to_end.py
(slower than the unit tests — reads all real CSVs in data/raw/, runs the
ingest twice)
"""

from __future__ import annotations

import subprocess
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_ORPHAN_CODES = {
    '02711', '02799', '02810', '04757', '04759',
    '05628', '05666', '05672', '06305',
}


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )


def test_seed_then_ingest_matches_documented_totals(tmp_path):
    db_path = tmp_path / 'cet_cap_test.db'

    seed_result = _run(['scripts/seed_reference_tables.py', '--db', str(db_path)])
    assert 'institutes=969' in seed_result.stdout
    assert 'base_categories=48' in seed_result.stdout
    # institution_code 01321 was renamed between 2024 and 2025 in the
    # source data — pin the current (2026) name, not the frequency-
    # majority historical one, as canonical.
    assert '01321' in seed_result.stdout
    assert 'Swami Vivekananda' in seed_result.stdout

    ingest_result = _run([
        'scripts/ingest_seats.py', 'data/raw/seats/',
        '--year', '2026', '--db', str(db_path),
    ])
    assert ('TOTAL: read=70269 inserted=69655 skipped(quarantined)=4 '
            'duplicate=0 failed=610') in ingest_result.stdout

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # The printed "inserted" count must match rows actually present —
    # not just "no exception was raised" (bugs #2/#3 above).
    cur.execute('SELECT COUNT(*) FROM seats')
    assert cur.fetchone()[0] == 69655

    cur.execute('SELECT COUNT(*) FROM seats WHERE raw_gender IS NULL')
    assert cur.fetchone()[0] == 0

    cur.execute(
        "SELECT DISTINCT institution_code FROM seats_ingest_errors "
        "WHERE reason = 'FOREIGN KEY constraint failed'"
    )
    failed_codes = {row[0] for row in cur.fetchall()}
    assert failed_codes == EXPECTED_ORPHAN_CODES
    conn.close()

    # Re-running against the same DB must be a true no-op on row count —
    # every row should now hit the UNIQUE key as a duplicate, not insert
    # a second copy (this is exactly what bug #3 broke).
    second_run = _run([
        'scripts/ingest_seats.py', 'data/raw/seats/',
        '--year', '2026', '--db', str(db_path),
    ])
    assert ('TOTAL: read=70269 inserted=0 skipped(quarantined)=4 '
            'duplicate=69655 failed=610') in second_run.stdout

    conn = sqlite3.connect(db_path)
    assert conn.execute('SELECT COUNT(*) FROM seats').fetchone()[0] == 69655
    conn.close()
