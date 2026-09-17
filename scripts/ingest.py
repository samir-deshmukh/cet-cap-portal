"""
ingest.py — Load validated cutoffs CSVs into the `cutoffs` fact table
(see db/schema.sql). This is the "Phase 2" script referenced throughout
README/DECISIONS.md as "not yet built" — it now exists.

Reuses validate_data.py's forward-fill and anomaly/category/stage/section
logic directly (FILL_COLUMNS, forward_fill_block_columns,
is_extraction_anomaly, parse_category, STAGE_MAP, SECTION_MAP,
parse_filename) instead of re-deriving any of it. An earlier attempt at
this script (docs/DECISIONS.md "ingest.py: reusing validate_data.py's
forward-fill instead of re-deriving it") was written from scratch without
that forward-fill step and silently dropped ~11.5% of all rows — every
row after the first in each PDF-extracted block, since those come in with
institution_code/program_name/etc. blank by design (the source PDF prints
that context once per block). Re-deriving parsing logic that already
exists and was already hardened against real data is exactly how that
happened; this script imports it instead.

Run:
    python scripts/seed_reference_tables.py --db db/cet_cap.db   # first
    python scripts/ingest.py data/raw/ --db db/cet_cap.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_data import (  # noqa: E402
    REQUIRED_COLUMNS,
    SECTION_MAP,
    STAGE_MAP,
    forward_fill_block_columns,
    is_extraction_anomaly,
    parse_category,
    parse_filename,
)


def load_program_levels(ref_dir: Path) -> dict[str, str]:
    """raw program_name -> level (UG/PG/Integrated), from
    data/reference/program_aliases.csv. Deliberately NOT using that
    file's own `program_family` column for `programs.program_family` —
    see the module-level note below on why program_family here means
    "which CET application stream this file belongs to" (BCA/BBA/MBA/
    MCA, matching how seats.program_family is scoped), not the more
    granular sub-classification (e.g. "BCA_VISUAL_ARTS") program_aliases.csv
    uses for its own purposes. `program_full` (the human-readable name)
    isn't stored either — `programs` has no column for it currently."""
    df = pd.read_csv(ref_dir / 'program_aliases.csv', dtype=str, keep_default_na=False)
    return dict(zip(df['raw_name'].str.strip(), df['level'].str.strip()))


def get_or_create_program(
    conn: sqlite3.Connection,
    cache: dict[tuple[str, str], int],
    family: str,
    name_raw: str,
    level_map: dict[str, str],
) -> int:
    key = (family, name_raw)
    if key in cache:
        return cache[key]
    cur = conn.cursor()
    level = level_map.get(name_raw)  # None if not in the whitelist (defensive; expected 0 cases)
    cur.execute(
        'INSERT OR IGNORE INTO programs (program_family, program_name_raw, level) '
        'VALUES (?, ?, ?)',
        (family, name_raw, level),
    )
    if cur.rowcount:
        program_id = cur.lastrowid
    else:
        cur.execute(
            'SELECT program_id FROM programs WHERE program_family = ? AND program_name_raw = ?',
            (family, name_raw),
        )
        program_id = cur.fetchone()[0]
    cache[key] = program_id
    return program_id


def ingest_file(
    conn: sqlite3.Connection,
    path: Path,
    program_cache: dict[tuple[str, str], int],
    level_map: dict[str, str],
) -> tuple[int, int, int, int, int]:
    meta = parse_filename(path)
    if not meta:
        raise ValueError(f'{path.name}: does not match the cutoffs filename pattern')
    family, year, round_no = meta['family'], meta['year'], meta['round']

    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding='utf-8-sig')
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f'{path.name}: missing columns {missing}')

    cur = conn.cursor()
    rows_read = len(df)

    # Stage every row exactly as read, before any transformation (see
    # "Staging table before facts") — same pattern as ingest_seats.py.
    for _, row in df.iterrows():
        cur.execute(
            'INSERT INTO staging_cutoffs (source_file, raw_row) VALUES (?, ?)',
            (path.name, row.to_json()),
        )

    # Forward-fill block-context columns — see module docstring for why
    # this has to be the same function validate_data.py uses.
    forward_fill_block_columns(df)

    rows_inserted = rows_skipped = rows_duplicate = rows_failed = 0

    for rowno, row in df.iterrows():
        def log_error(reason: str) -> None:
            cur.execute(
                '''INSERT INTO ingest_errors
                   (source_file, source_page, source_row, raw_category,
                    raw_program, reason)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (path.name, int(row['page']) if row['page'].strip().isdigit() else None,
                 int(rowno), row['category'], row['program_name'], reason),
            )

        if is_extraction_anomaly(row):
            rows_skipped += 1
            log_error('EXTRACTION_ANOMALY: no institution_code/institution_name '
                      'after forward-fill, or known-bad category text')
            continue

        cat_raw = row['category'].strip()
        base_category, is_ladies, _unused_section = parse_category(cat_raw)
        # _unused_section: parse_category also derives HU/OHU/SL from the
        # category-code suffix, but cutoffs.section_code is populated from
        # the CSV's own `section` column via SECTION_MAP below instead —
        # that column carries finer distinctions (HU_TO_OHU, OHU_TO_HU,
        # MI-MIN, MI-NONMIN) the suffix-derived value can't represent, and
        # validate_data.py already checks the two independently rather
        # than cross-validating them, so ingest.py follows the same split.
        if base_category is None:
            rows_skipped += 1
            log_error(f'UNKNOWN_CATEGORY: {cat_raw!r}')
            continue

        stage_code = STAGE_MAP.get(row['stage'].strip())
        if stage_code is None:
            rows_skipped += 1
            log_error(f'UNKNOWN_STAGE: {row["stage"]!r}')
            continue

        section_code = SECTION_MAP.get(row['section'].strip())
        if section_code is None:
            rows_skipped += 1
            log_error(f'UNKNOWN_SECTION: {row["section"]!r}')
            continue

        prog_name = row['program_name'].strip()
        rank_raw = row['rank_number'].strip()
        rank_number = int(rank_raw) if rank_raw.isdigit() else None
        rank_suffix = row['rank_suffix'].strip() or None

        try:
            percentile = float(row['percentage'])
        except ValueError:
            rows_skipped += 1
            log_error(f'BAD_PERCENTAGE: {row["percentage"]!r}')
            continue

        institution_code = row['institution_code'].strip()
        program_id = get_or_create_program(conn, program_cache, family, prog_name, level_map)

        try:
            cur.execute(
                '''INSERT OR IGNORE INTO cutoffs
                   (year, round, institution_code, program_id, base_category,
                    is_ladies, section_code, stage_code, home_university,
                    rank_number, rank_suffix, percentile, raw_category,
                    raw_program_name, source_pdf, source_page)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (year, round_no, institution_code, program_id, base_category,
                 is_ladies, section_code, stage_code, None,
                 rank_number, rank_suffix, percentile, cat_raw,
                 prog_name, row['source_pdf'],
                 int(row['page']) if row['page'].strip().isdigit() else None),
            )
        except sqlite3.IntegrityError as e:
            rows_failed += 1
            log_error(f'{e}')
            continue

        # "OR IGNORE" does not raise on a UNIQUE-key conflict — check
        # rowcount rather than assuming "no exception" == "inserted"
        # (see docs/DECISIONS.md "seats UNIQUE constraint missing
        # program_family" for why this check exists at all).
        if cur.rowcount == 0:
            rows_duplicate += 1
            log_error('DUPLICATE_UNIQUE_KEY: row already present for this '
                       '(year, round, institution_code, program_id, base_category, '
                       'is_ladies, section_code, stage_code, rank_number, percentile)')
        else:
            rows_inserted += 1

    conn.commit()
    cur.execute(
        '''INSERT INTO ingest_log
           (source_file, rows_read, rows_inserted, rows_skipped,
            rows_duplicate, rows_failed)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (path.name, rows_read, rows_inserted, rows_skipped, rows_duplicate, rows_failed),
    )
    conn.commit()
    return rows_read, rows_inserted, rows_skipped, rows_duplicate, rows_failed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('raw_dir', nargs='?', default='data/raw')
    ap.add_argument('--db', default='db/cet_cap.db')
    ap.add_argument('--ref-dir', default='data/reference')
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir)
    csvs = sorted(raw_dir.glob('*.csv'))
    if not csvs:
        sys.exit(f'No CSVs found in {raw_dir}')

    conn = sqlite3.connect(args.db)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.executescript(Path('db/schema.sql').read_text())

    level_map = load_program_levels(Path(args.ref_dir))
    program_cache: dict[tuple[str, str], int] = {}

    grand_read = grand_ins = grand_skip = grand_dup = grand_fail = 0
    for path in csvs:
        read, ins, skip, dup, fail = ingest_file(conn, path, program_cache, level_map)
        print(f'{path.name}: read={read} inserted={ins} '
              f'skipped(quarantined)={skip} duplicate={dup} failed={fail}')
        grand_read += read
        grand_ins += ins
        grand_skip += skip
        grand_dup += dup
        grand_fail += fail

    print(f'\nTOTAL: read={grand_read} inserted={grand_ins} '
          f'skipped(quarantined)={grand_skip} duplicate={grand_dup} failed={grand_fail}')
    if grand_read != grand_ins + grand_skip + grand_dup + grand_fail:
        print('ROW COUNT MISMATCH — some rows vanished silently!')
        sys.exit(1)
    if grand_dup:
        print(f'\n⚠ {grand_dup} rows hit an existing UNIQUE-key row — see '
              f"ingest_errors (reason LIKE 'DUPLICATE_UNIQUE_KEY%').")
    conn.close()


if __name__ == '__main__':
    main()
