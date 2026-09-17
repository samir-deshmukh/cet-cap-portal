"""
End-to-end regression test for scripts/ingest.py (the cutoffs ingest).

Guards specifically against the bug found in a from-scratch rewrite of
this script (docs/DECISIONS.md "ingest.py: reusing validate_data.py's
forward-fill instead of re-deriving it"): re-deriving "is this row
complete" instead of reusing validate_data.py's forward-fill dropped
~11.5% of all rows, since most rows in a PDF-extracted block legitimately
arrive with institution_code/program_name/etc. blank. A fresh clone must
recover all but the one already-documented quarantined anomaly.

Run: pytest tests/test_ingest_cutoffs_end_to_end.py
(slower than the unit tests — reads all real CSVs in data/raw/, runs the
ingest twice)
"""

from __future__ import annotations

import subprocess
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )


def test_seed_then_ingest_cutoffs_matches_validated_totals(tmp_path):
    db_path = tmp_path / 'cet_cap_test.db'

    _run(['scripts/seed_reference_tables.py', '--db', str(db_path)])

    ingest_result = _run(['scripts/ingest.py', 'data/raw/', '--db', str(db_path)])
    # 67,770 raw rows, 1 documented quarantined anomaly (BCA_24_C2.csv,
    # category field "GOVERNMENT") — see VALIDATION_REPORT.txt. If this
    # ever regresses toward ~59,993 inserted / ~7,777 skipped, the
    # forward-fill step has been bypassed again.
    assert ('TOTAL: read=67770 inserted=67769 skipped(quarantined)=1 '
            'duplicate=0 failed=0') in ingest_result.stdout

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Printed total must match what's actually in the table.
    cur.execute('SELECT COUNT(*) FROM cutoffs')
    assert cur.fetchone()[0] == 67769

    # program_family must stay scoped to the 4 CET application streams
    # (from the filename), not program_aliases.csv's finer sub-classification
    # (e.g. "BCA_VISUAL_ARTS") — see ingest.py's load_program_levels docstring.
    cur.execute('SELECT DISTINCT program_family FROM programs')
    assert {row[0] for row in cur.fetchall()} == {'BBA', 'BCA', 'MBA', 'MCA'}

    cur.execute(
        "SELECT source_file, raw_category FROM ingest_errors "
        "WHERE reason LIKE 'EXTRACTION_ANOMALY%'"
    )
    anomalies = cur.fetchall()
    assert anomalies == [('BCA_24_C2.csv', 'GOVERNMENT')]
    conn.close()

    # Re-running must be a true no-op on row count.
    second_run = _run(['scripts/ingest.py', 'data/raw/', '--db', str(db_path)])
    assert ('TOTAL: read=67770 inserted=0 skipped(quarantined)=1 '
            'duplicate=67769 failed=0') in second_run.stdout

    conn = sqlite3.connect(db_path)
    assert conn.execute('SELECT COUNT(*) FROM cutoffs').fetchone()[0] == 67769
    conn.close()
