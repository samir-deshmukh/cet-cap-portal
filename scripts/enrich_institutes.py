"""
scripts/enrich_institutes.py — Populate institutes.city / institutes.website
from a manually-compiled source CSV (college_code, college_name, city,
website). This is enrichment of EXISTING rows (institutes is already
seeded by seed_reference_tables.py from the cutoffs data) — it never
inserts a new institute, only fills in city/website for ones that
already exist.

Why city/website aren't derived automatically like everything else in
this pipeline: there's no city or website column anywhere in the CET
CAP source PDFs — institution_name sometimes has a city as a trailing
comma-separated fragment, but not reliably (roughly half the names have
no comma at all). This has to come from an external, manually-compiled
source instead.

Safeguards applied, each verified against the actual source file before
being written (see docs/DECISIONS.md "Institute city/website
enrichment"):
    1. Some source rows list TWO institution codes in one cell
       ("02668,02688") for what's the same physical college registered
       under separate CET codes (e.g. one per program family) — these
       are split into one row per code, not dropped or given only the
       first code.
    2. After splitting, a handful of codes have MORE than one source
       row. Where they agree on city (just a differently-phrased college
       name, or one row missing a website), they're safely collapsed
       into one. Where they genuinely DISAGREE on city, the code is
       quarantined rather than guessing which row is right.
    3. website == "Unknown" (case-insensitive) is stored as NULL, never
       as the literal string "Unknown" — so the UI can tell "we don't
       have a website" from "the website is literally the word
       Unknown" and never renders a fake link.
    4. A name-similarity check (difflib ratio on normalized text)
       between the source row's college_name and the DB's
       institution_name for that code — low similarity would mean the
       code likely maps to the wrong college in the source file. None
       of the current source data trips this (minimum similarity found
       was 0.35), but it's here so a future update to the source file
       can't silently attach a real city/website to the wrong college.

Run:
    python scripts/enrich_institutes.py data/reference/institute_contacts.csv --db db/cet_cap.db
"""

from __future__ import annotations

import argparse
import difflib
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

NAME_SIMILARITY_THRESHOLD = 0.30


def _normalize_name(s: str) -> str:
    s = re.sub(r'[^A-Z0-9 ]', ' ', s.upper())
    return re.sub(r'\s+', ' ', s).strip()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add city/website columns if this is an older DB created before
    they were part of the schema — CREATE TABLE IF NOT EXISTS in
    schema.sql is a no-op on an existing table, so a fresh ALTER is
    needed for upgrade-in-place."""
    cur = conn.cursor()
    existing = {row[1] for row in cur.execute('PRAGMA table_info(institutes)')}
    for col in ('city', 'website'):
        if col not in existing:
            cur.execute(f'ALTER TABLE institutes ADD COLUMN {col} TEXT')
    conn.commit()


def load_and_reconcile(csv_path: Path, conn: sqlite3.Connection) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (clean, quarantined) — clean has one row per institution_code
    ready to write; quarantined lists codes skipped and why."""
    raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    raw.columns = [c.strip().lower() for c in raw.columns]

    rows = []
    for _, r in raw.iterrows():
        for code in r['college_code'].split(','):
            website = r['website'].strip()
            rows.append({
                'college_code': code.strip(),
                'college_name': r['college_name'].strip(),
                'city': r['city'].strip(),
                'website': None if website.lower() == 'unknown' or not website else website,
            })
    split_df = pd.DataFrame(rows)

    quarantined = []
    clean_rows = []
    for code, group in split_df.groupby('college_code'):
        if group['city'].nunique() > 1:
            quarantined.append({
                'college_code': code, 'reason': 'CITY_CONFLICT',
                'detail': dict(zip(group['city'], group['college_name'])),
            })
            continue
        # Same city (or only one row) — prefer a row that actually has a
        # website over one that doesn't, so a real site isn't dropped in
        # favor of a duplicate blank entry.
        with_site = group[group['website'].notna()]
        chosen = with_site.iloc[0] if len(with_site) else group.iloc[0]
        clean_rows.append({
            'institution_code': code,
            'city': chosen['city'],
            'website': chosen['website'],
            'source_name': chosen['college_name'],
        })
    clean = pd.DataFrame(clean_rows)

    # Cross-check against the DB: codes not in institutes at all, and a
    # name-similarity sanity check for codes that ARE.
    inst = pd.read_sql('SELECT institution_code, institution_name FROM institutes', conn)
    merged = clean.merge(inst, on='institution_code', how='left', indicator=True)

    not_in_db = merged[merged['_merge'] == 'left_only']
    for _, r in not_in_db.iterrows():
        quarantined.append({'college_code': r['institution_code'],
                             'reason': 'CODE_NOT_IN_DB', 'detail': r['source_name']})

    in_db = merged[merged['_merge'] == 'both'].copy()
    in_db['similarity'] = in_db.apply(
        lambda r: difflib.SequenceMatcher(
            None, _normalize_name(r['institution_name']), _normalize_name(r['source_name'])
        ).ratio(),
        axis=1,
    )
    low_sim = in_db[in_db['similarity'] < NAME_SIMILARITY_THRESHOLD]
    for _, r in low_sim.iterrows():
        quarantined.append({
            'college_code': r['institution_code'], 'reason': 'NAME_MISMATCH',
            'detail': f'DB={r["institution_name"]!r} vs source={r["source_name"]!r} '
                      f'(similarity={r["similarity"]:.2f})',
        })

    final = in_db[in_db['similarity'] >= NAME_SIMILARITY_THRESHOLD][
        ['institution_code', 'city', 'website']]
    return final, pd.DataFrame(quarantined)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('csv_path', nargs='?', default='data/reference/institute_contacts.csv')
    ap.add_argument('--db', default='db/cet_cap.db')
    args = ap.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        sys.exit(f'{csv_path} not found')

    conn = sqlite3.connect(args.db)
    conn.execute('PRAGMA foreign_keys = ON')
    _ensure_columns(conn)

    clean, quarantined = load_and_reconcile(csv_path, conn)

    cur = conn.cursor()
    for _, r in clean.iterrows():
        cur.execute(
            'UPDATE institutes SET city = ?, website = ? WHERE institution_code = ?',
            (r['city'], r['website'], r['institution_code']),
        )
    conn.commit()

    total_institutes = conn.execute('SELECT COUNT(*) FROM institutes').fetchone()[0]
    with_city = conn.execute(
        'SELECT COUNT(*) FROM institutes WHERE city IS NOT NULL').fetchone()[0]
    with_website = conn.execute(
        'SELECT COUNT(*) FROM institutes WHERE website IS NOT NULL').fetchone()[0]

    print(f'Updated {len(clean)} institutes.')
    print(f'Coverage: {with_city}/{total_institutes} have a city, '
          f'{with_website}/{total_institutes} have a website.')
    if len(quarantined):
        print(f'\n{len(quarantined)} rows quarantined (not applied):')
        for _, q in quarantined.iterrows():
            print(f"  [{q['reason']}] {q['college_code']}: {q['detail']}")
    conn.close()


if __name__ == '__main__':
    main()
