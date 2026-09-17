"""
ingest_seats.py — Load validated seat-matrix CSVs into the `seats`
fact table (see db/schema.sql). Mirrors the staging-then-normalise
pattern used for cutoffs (docs/DECISIONS.md "Staging table before
facts"), with its own staging/log/error tables since the seat-matrix
source has a different shape and no year/round in the filename.

Run:
    python scripts/ingest_seats.py data/raw/seats/ --year 2026 --db db/cet_cap.db

`--year` is required because the seat-matrix source itself carries no
year — see docs/DECISIONS.md "Seat matrix: capture_year is an
ingest-time assumption, not source data" before picking a value.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from validate_seats import (
    FILENAME_PATTERN,
    KNOWN_BAD_CATEGORY_VALUES,
    TOTAL_MARKER,
    load_whitelists,
    normalize_category,
    parse_filename,
)

REQUIRED_COLUMNS = [
    'source_pdf', 'page', 'institution_code', 'choice_code',
    'allocation_type', 'category', 'gender', 'seats',
]


def canonical_institution_code(institution_code_raw: str, choice_code: str) -> str:
    """The seat-matrix source's institution_code column loses its
    leading zero when read as a number (e.g. 2113 for "02113"). The
    same 5-digit code is preserved as the first 5 characters of
    choice_code (a string, e.g. "0211310110"), which we treat as the
    canonical form so it lines up with institutes.institution_code
    (TEXT, zero-padded) and cutoffs.program_code. We still cross-check
    against the numeric column as a sanity check."""
    from_choice = choice_code[:5]
    if institution_code_raw.strip():
        padded = institution_code_raw.strip().zfill(5)
        if padded != from_choice:
            raise ValueError(
                f'institution_code mismatch: column={padded} '
                f'vs choice_code prefix={from_choice}'
            )
    return from_choice


def ingest_file(
    conn: sqlite3.Connection,
    path: Path,
    year: int,
    valid_cats: set[str],
    lane_map: dict[str, str],
    alias_map: dict[str, str],
) -> tuple[int, int, int, int, int]:
    meta = parse_filename(path)
    if not meta:
        raise ValueError(f'{path.name}: does not match {FILENAME_PATTERN.pattern}')
    family = meta['family']

    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                      encoding='utf-8-sig')
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f'{path.name}: missing columns {missing}')

    cur = conn.cursor()
    rows_read = len(df)
    rows_inserted = rows_skipped = rows_duplicate = rows_failed = 0

    for _, row in df.iterrows():
        # Stage the raw row first (see "Staging table before facts").
        cur.execute(
            'INSERT INTO staging_seats (source_file, raw_row) VALUES (?, ?)',
            (path.name, row.to_json()),
        )

        cat_raw = row['category'].strip()
        lane_raw = row['allocation_type'].strip()

        if cat_raw in KNOWN_BAD_CATEGORY_VALUES:
            cur.execute(
                '''INSERT INTO seats_ingest_errors
                   (source_file, source_page, institution_code, choice_code,
                    raw_allocation_type, raw_category, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (path.name, int(row['page']), row['institution_code'],
                 row['choice_code'], lane_raw, cat_raw,
                 'QUARANTINED_EXTRACTION_ANOMALY: known column-shift artifact'),
            )
            rows_skipped += 1
            continue

        try:
            inst_code = canonical_institution_code(row['institution_code'],
                                                    row['choice_code'])
            lane_code = lane_map.get(lane_raw)
            if lane_code is None:
                raise ValueError(f'unknown allocation_type {lane_raw!r}')

            is_total = cat_raw == TOTAL_MARKER
            base_category = None
            if not is_total:
                base_category = normalize_category(cat_raw, alias_map)
                if base_category not in valid_cats:
                    raise ValueError(f'unknown category {cat_raw!r}')

            # Keep '' rather than None for unspecified gender — never
            # store NULL here (see schema.sql comment on raw_gender: a
            # nullable column would break the UNIQUE constraint's
            # dedup for the 62% of rows with no gender split).
            gender_raw = row['gender'].strip()
            is_ladies = {'G': False, 'L': True}.get(gender_raw) if gender_raw else None

            seats = int(row['seats'])

            cur.execute(
                '''INSERT OR IGNORE INTO seats
                   (capture_year, program_family, institution_code, choice_code,
                    allocation_lane, base_category, is_total, is_ladies, seats,
                    raw_category, raw_allocation_type, raw_gender,
                    source_pdf, source_page)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (year, family, inst_code, row['choice_code'], lane_code,
                 base_category, is_total, is_ladies, seats,
                 cat_raw, lane_raw, gender_raw,
                 row['source_pdf'], int(row['page'])),
            )
            # "OR IGNORE" does not raise on a UNIQUE-key conflict, so a
            # successful call here does NOT mean a row was actually
            # inserted — check rowcount explicitly rather than assuming
            # "no exception" == "inserted" (see seats_ingest_log.rows_duplicate).
            if cur.rowcount == 0:
                rows_duplicate += 1
                cur.execute(
                    '''INSERT INTO seats_ingest_errors
                       (source_file, source_page, institution_code, choice_code,
                        raw_allocation_type, raw_category, reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (path.name, int(row['page']), row['institution_code'],
                     row['choice_code'], lane_raw, cat_raw,
                     'DUPLICATE_UNIQUE_KEY: row already present for this '
                     '(capture_year, program_family, institution_code, '
                     'choice_code, allocation_lane, raw_category, raw_gender)'),
                )
            else:
                rows_inserted += 1
        except Exception as e:  # noqa: BLE001 — logged, never swallowed silently
            cur.execute(
                '''INSERT INTO seats_ingest_errors
                   (source_file, source_page, institution_code, choice_code,
                    raw_allocation_type, raw_category, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (path.name, int(row['page']), row['institution_code'],
                 row['choice_code'], lane_raw, cat_raw, str(e)),
            )
            rows_failed += 1

    cur.execute(
        '''INSERT INTO seats_ingest_log
           (source_file, rows_read, rows_inserted, rows_skipped,
            rows_duplicate, rows_failed)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (path.name, rows_read, rows_inserted, rows_skipped,
         rows_duplicate, rows_failed),
    )
    conn.commit()
    return rows_read, rows_inserted, rows_skipped, rows_duplicate, rows_failed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('raw_dir', nargs='?', default='data/raw/seats')
    ap.add_argument('--year', type=int, required=True,
                     help='Year this seat-matrix snapshot applies to '
                          '(not present in the source — see DECISIONS.md)')
    ap.add_argument('--db', default='db/cet_cap.db')
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.executescript(Path('db/schema.sql').read_text())

    ref = Path('data/reference')
    valid_cats, lane_map, alias_map = load_whitelists(ref)
    # Seed reference tables (idempotent).
    lane_df = pd.read_csv(ref / 'allocation_lane_whitelist.csv')
    for _, r in lane_df.drop_duplicates('lane_code').iterrows():
        conn.execute('INSERT OR IGNORE INTO allocation_lanes VALUES (?, ?)',
                      (r['lane_code'], r['lane_full']))
    conn.commit()

    folder = Path(args.raw_dir)
    csvs = sorted(folder.glob('*.csv'))
    if not csvs:
        print(f'No CSVs in {folder}')
        sys.exit(1)

    grand_read = grand_ins = grand_skip = grand_dup = grand_fail = 0
    for path in csvs:
        read, ins, skip, dup, fail = ingest_file(
            conn, path, args.year, valid_cats, lane_map, alias_map,
        )
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
              f"seats_ingest_errors (reason LIKE 'DUPLICATE_UNIQUE_KEY%'). "
              f'If this number is unexpectedly large, check whether the '
              f'`seats` UNIQUE key still matches how choice_code is scoped '
              f'in the source data (see docs/DECISIONS.md).')
    conn.close()


if __name__ == '__main__':
    main()
