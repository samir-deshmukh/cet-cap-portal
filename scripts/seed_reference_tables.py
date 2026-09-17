"""
seed_reference_tables.py — Temporary bridge to populate `institutes`,
`base_categories`, `sections`, and `stages` (see db/schema.sql) so
ingest_seats.py and ingest.py can be run on a fresh database *before*
those two tables that only ingest.py should really own (`institutes`,
`base_categories`) have a proper long-term source.

Why this is needed:
    `seats.institution_code` and `seats.base_category` (and, once
    ingest.py runs, `cutoffs.institution_code`/`base_category`) are
    foreign keys into `institutes` and `base_categories`. Historically
    nothing populated those two tables before ingest.py existed — see
    docs/DECISIONS.md for the FOREIGN KEY failures that caused. `sections`
    and `stages` are simpler: both are static, already-whitelisted
    reference data with no ingest-time ambiguity, so they're seeded here
    too rather than needing their own bridge.

What this does:
    - `base_categories`: loaded directly from data/reference/category_whitelist.csv
      (columns already match the table 1:1).
    - `sections`: loaded directly from data/reference/section_whitelist.csv
      (columns already match the table 1:1) — the same whitelist
      validate_data.py checks the raw `section` column against.
    - `stages`: the distinct `canonical` values from data/reference/stage_whitelist.csv
      (includes 'Unknown', for genuinely-blank-stage files/rows).
    - `institutes`: derived from every (institution_code, institution_name,
      year) triple across all cutoffs CSVs in data/raw/*.csv (year comes
      from the filename via validate_data.parse_filename, so this doesn't
      re-implement that parsing).

Canonical-name rule for the 29 codes (out of 970) that have more than one
distinct raw name string:
    Pick the majority name in the code's MOST RECENT year, not the
    majority across all years combined. These 29 splits are not all the
    same kind of noise — some are one-off PDF-extraction typos (e.g. one
    file says "NA"), but at least 14 of them are genuine institution
    renames that are cleanly correlated with year (e.g. institution_code
    01321: "INSTITUTE OF MANAGEMENT STUDIES MAHAVIDYALAYA WARUD" in every
    2024 row, "Swami Vivekananda College Of Management, Science and Arts,
    Warud." in every 2025/2026 row — a real rename, not a typo). Picking
    by raw frequency across all years gets several of these wrong (it
    would pick whichever name happened to have more historical rows,
    which is the OLD name whenever the rename is recent) — verified by
    comparing both approaches: 14 of the 29 codes get a different, more
    current name under the recency rule than under a plain frequency
    vote. Ties within the most recent year (same name count) fall back to
    alphabetical order for determinism; none currently occur, but it's
    printed if one ever does, since that would mean the recency signal
    didn't actually resolve the ambiguity.
    This is still a heuristic, not verified against an authoritative
    institute registry — it is the best signal available in this data,
    not a guarantee of correctness for any single code.

institute_type / affiliation_status are left NULL: the source `status`
column is free text that mixes affiliation, autonomy, minority status,
and home-university in one string per row and isn't reliably splittable
here — that parsing belongs in the real ingest.py, not this bridge.

Safe to re-run: uses upsert (ON CONFLICT DO UPDATE), not INSERT OR
IGNORE, so re-running after a source CSV correction or a logic fix here
updates the stored institution_name/category fields in place rather than
leaving stale data from a previous run. This does NOT delete-then-insert,
so it's safe even after `seats` or `cutoffs` rows already reference these
institutes (a plain REPLACE would violate the foreign key in that case).

This is NOT a replacement for ingest.py's ownership of `institutes` /
`base_categories` specifically — ingest.py should eventually own those
two properly (including institute_type / affiliation_status, and a real
authoritative name where the source data disagrees with itself), at
which point the institutes/base_categories parts of this script should
be retired. `sections`/`stages` seeding here is not time-limited the
same way — it's just where static reference data belongs.

Run:
    python scripts/seed_reference_tables.py --db db/cet_cap.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_data import parse_filename  # noqa: E402


def seed_base_categories(conn: sqlite3.Connection, ref_dir: Path) -> int:
    df = pd.read_csv(ref_dir / 'category_whitelist.csv', dtype=str, keep_default_na=False)
    cur = conn.cursor()
    n = 0
    for _, row in df.iterrows():
        cur.execute(
            '''INSERT INTO base_categories (base_code, category_full, category_group)
               VALUES (?, ?, ?)
               ON CONFLICT(base_code) DO UPDATE SET
                   category_full=excluded.category_full,
                   category_group=excluded.category_group''',
            (row['base_code'], row['category_full'], row['category_group']),
        )
        n += 1
    conn.commit()
    return n


def seed_sections(conn: sqlite3.Connection, ref_dir: Path) -> int:
    """From data/reference/section_whitelist.csv — columns match the
    `sections` table 1:1, same as base_categories. This is the same
    whitelist validate_data.py checks the raw `section` column against
    (via its SECTION_MAP), so ingest.py can trust every real row's
    mapped section_code already exists here."""
    df = pd.read_csv(ref_dir / 'section_whitelist.csv', dtype=str, keep_default_na=False)
    cur = conn.cursor()
    n = 0
    for _, row in df.iterrows():
        cur.execute(
            '''INSERT INTO sections (section_code, section_full)
               VALUES (?, ?)
               ON CONFLICT(section_code) DO UPDATE SET
                   section_full=excluded.section_full''',
            (row['section_code'], row['section_full']),
        )
        n += 1
    conn.commit()
    return n


def seed_stages(conn: sqlite3.Connection, ref_dir: Path) -> int:
    """From the distinct `canonical` values in stage_whitelist.csv
    (includes 'Unknown', for files/rows where stage is genuinely blank —
    see validate_data.STAGE_MAP's '' -> 'Unknown' entry). `stages` only
    has one column (the code itself), unlike sections/base_categories."""
    df = pd.read_csv(ref_dir / 'stage_whitelist.csv', dtype=str, keep_default_na=False)
    cur = conn.cursor()
    n = 0
    for canonical in sorted(set(df['canonical'])):
        cur.execute('INSERT OR IGNORE INTO stages (stage_code) VALUES (?)', (canonical,))
        n += cur.rowcount
    conn.commit()
    return n


def _pick_canonical_name(code: str, by_year: dict[int, Counter]) -> str:
    """Most frequent name in the most recent year this code appears in.
    Falls back to alphabetical order on a same-year tie (see docstring)."""
    latest_year = max(by_year)
    counts = by_year[latest_year].most_common()
    top_count = counts[0][1]
    tied = sorted(name for name, count in counts if count == top_count)
    if len(tied) > 1:
        print(f'      (unresolved tie in {latest_year} for {code}, '
              f'{tied} — picking {tied[0]!r} alphabetically)')
    return tied[0]


def seed_institutes(conn: sqlite3.Connection, raw_dir: Path) -> tuple[int, int]:
    csvs = sorted(p for p in raw_dir.glob('*.csv'))
    if not csvs:
        raise SystemExit(f'No cutoffs CSVs found in {raw_dir}')

    # code -> year -> Counter(name -> occurrences)
    names_by_code: dict[str, dict[int, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for path in csvs:
        meta = parse_filename(path)
        if not meta:
            print(f'  (skipping {path.name}: does not match the cutoffs filename pattern)')
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=False,
                          usecols=['institution_code', 'institution_name'])
        for code, name in zip(df['institution_code'], df['institution_name']):
            code = code.strip()
            name = name.strip()
            if not code or not name:
                continue
            names_by_code[code][meta['year']][name] += 1

    cur = conn.cursor()
    upserted = 0
    variants_flagged = 0
    for code, by_year in sorted(names_by_code.items()):
        all_names = Counter()
        for year_counts in by_year.values():
            all_names.update(year_counts)
        canonical_name = _pick_canonical_name(code, by_year)

        if len(all_names) > 1:
            variants_flagged += 1
            print(f'  ⚠ institution_code={code}: {len(all_names)} name variants '
                  f'across years, using most recent ({max(by_year)}): {canonical_name!r}')
            for variant, count in all_names.most_common():
                if variant != canonical_name:
                    print(f'      (also seen {count}x, other years: {variant!r})')

        cur.execute(
            '''INSERT INTO institutes (institution_code, institution_name)
               VALUES (?, ?)
               ON CONFLICT(institution_code) DO UPDATE SET
                   institution_name=excluded.institution_name''',
            (code, canonical_name),
        )
        upserted += 1
    conn.commit()
    return upserted, variants_flagged


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--db', default='db/cet_cap.db')
    ap.add_argument('--raw-dir', default='data/raw')
    ap.add_argument('--ref-dir', default='data/reference')
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.executescript(Path('db/schema.sql').read_text())

    print('Seeding base_categories...')
    n_cats = seed_base_categories(conn, Path(args.ref_dir))
    print(f'  {n_cats} category rows upserted\n')

    print('Seeding sections and stages...')
    n_sections = seed_sections(conn, Path(args.ref_dir))
    n_stages = seed_stages(conn, Path(args.ref_dir))
    print(f'  {n_sections} section rows upserted, {n_stages} stage rows inserted\n')

    print('Seeding institutes from cutoffs CSVs...')
    n_inst, n_variants = seed_institutes(conn, Path(args.raw_dir))
    print(f'\n  {n_inst} institute rows upserted, '
          f'{n_variants} codes had multiple name variants across years (see above)')

    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM institutes')
    total_institutes = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM base_categories')
    total_cats = cur.fetchone()[0]
    conn.close()

    print(f'\nDone. institutes={total_institutes} base_categories={total_cats} in {args.db}')
    print('Note: institute_type / affiliation_status left NULL — see module '
          'docstring. The 9 institutes that exist only in the seat matrix '
          '(not in any cutoffs CSV) are intentionally NOT seeded here; they '
          'should still be quarantined by ingest_seats.py as documented in '
          'DECISIONS.md.')


if __name__ == '__main__':
    main()
