"""
validate_seats.py — Data quality gate for the seat-matrix source
(sanctioned intake capacity), separate from validate_data.py (which
covers the cutoffs/allotment source).

Run: python scripts/validate_seats.py data/raw/seats/
Exit 0 = clean. Exit 1 = issues found.
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = [
    'source_pdf', 'page', 'institution_code', 'choice_code',
    'allocation_type', 'category', 'gender', 'seats',
]

FILENAME_PATTERN = re.compile(
    r'^(?P<family>BCA|BBA|MBA|MCA)_SM', re.IGNORECASE,
)

# Rows individually verified against source context to be PDF
# column-shift/text-bleed artifacts, not real category codes. Same
# audit discipline as KNOWN_BAD_CATEGORY_VALUES in validate_data.py.
# 'Services) SC' — MBA_SM.csv, institution_code=2508: a fragment of
# an institution-name/status string ("... Services)") bled into the
# category cell for all 4 lanes (HU/OHU/PWD/DEF) of that block; the
# surrounding rows for the same institution are normal SC-adjacent
# rows in sequence, confirming this is extraction corruption.
KNOWN_BAD_CATEGORY_VALUES = {'Services) SC'}

TOTAL_MARKER = 'Total'


def parse_filename(path: Path) -> dict | None:
    m = FILENAME_PATTERN.match(path.stem)
    if not m:
        return None
    return {'family': m.group('family').upper()}


def load_whitelists(ref: Path) -> tuple[set[str], dict[str, str], dict[str, str]]:
    valid_cats = set(pd.read_csv(ref / 'category_whitelist.csv')['base_code'])
    lane_df = pd.read_csv(ref / 'allocation_lane_whitelist.csv')
    lane_map = dict(zip(lane_df['raw_value'], lane_df['lane_code']))
    alias_df = pd.read_csv(ref / 'seat_category_aliases.csv')
    alias_map = dict(zip(alias_df['raw_value'], alias_df['base_code']))
    return valid_cats, lane_map, alias_map


def normalize_category(raw: str, alias_map: dict[str, str]) -> str:
    """Seat-matrix category strings need alias resolution before the
    whitelist check: BBA/MBA/MCA seat files spell Vimukta Jati /
    Denotified Tribe as 'VJDT' (no slash) while the cutoffs whitelist
    (and the BCA seat file) use 'VJ/DT'. Falls through unchanged for
    every other value, including 'Total'."""
    return alias_map.get(raw, raw)


def hash_rows(df: pd.DataFrame) -> str:
    cols = [c for c in df.columns if c not in ('source_pdf', 'page')]
    return df[cols].astype(str).sort_values(by=cols).to_csv(index=False)


def main(raw_dir: str) -> None:
    folder = Path(raw_dir)
    csvs = sorted(folder.glob('*.csv'))
    if not csvs:
        print(f'No CSVs in {folder}')
        sys.exit(1)

    print(f'Validating {len(csvs)} seat-matrix files in {folder}\n')

    ref = Path('data/reference')
    valid_cats, lane_map, alias_map = load_whitelists(ref)

    summary = []
    hashes: dict[str, list[str]] = defaultdict(list)
    unknown_cats: Counter = Counter()
    unknown_lanes: Counter = Counter()
    dup_rows = 0
    file_errors: list[tuple[str, str]] = []
    anomalies: list[dict] = []
    total_mismatches: list[dict] = []

    for path in csvs:
        meta = parse_filename(path)
        if not meta:
            file_errors.append((path.name, 'FILENAME_PATTERN_MISMATCH'))
            continue

        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False,
                              encoding='utf-8-sig')
        except Exception as e:
            file_errors.append((path.name, f'READ_ERROR: {e}'))
            continue

        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            file_errors.append((path.name, f'MISSING_COLUMNS: {missing}'))
            continue

        hashes[hash_rows(df)].append(path.name)

        # No forward-fill needed here: unlike the cutoffs source,
        # every row in the seat-matrix source is fully populated —
        # institution_code, choice_code, allocation_type and category
        # are never blank (confirmed against real data: 0 blanks
        # across all 4 files).
        blank_required = [c for c in
                           ('institution_code', 'choice_code',
                            'allocation_type', 'category', 'seats')
                           if (df[c].str.strip() == '').any()]
        if blank_required:
            file_errors.append((
                path.name, f'UNEXPECTED_BLANKS: {blank_required} — '
                'seat-matrix rows are expected to be fully populated, '
                'unlike the block-forward-fill cutoffs source',
            ))

        bad_cat = bad_lane = bad_seats = 0
        anomaly_rows = 0

        for _, row in df.iterrows():
            cat_raw = row['category'].strip()
            lane_raw = row['allocation_type'].strip()

            if cat_raw in KNOWN_BAD_CATEGORY_VALUES:
                anomaly_rows += 1
                anomalies.append({
                    'file': path.name,
                    'institution_code': row['institution_code'],
                    'choice_code': row['choice_code'],
                    'raw_category': row['category'],
                    'raw_allocation_type': row['allocation_type'],
                })
                continue

            if cat_raw != TOTAL_MARKER:
                base = normalize_category(cat_raw, alias_map)
                if base not in valid_cats:
                    bad_cat += 1
                    unknown_cats[cat_raw] += 1

            if lane_raw not in lane_map:
                bad_lane += 1
                unknown_lanes[lane_raw] += 1

            try:
                int(row['seats'])
            except (ValueError, TypeError):
                bad_seats += 1

        # Total-row reconciliation: sum of non-Total category rows per
        # (institution_code, choice_code, allocation_type) must equal
        # that lane's own 'Total' row. This is a cross-check the
        # cutoffs source has no equivalent for — the seat-matrix PDF
        # prints its own subtotal, so we can catch extraction drift
        # (a missing/extra category row) without any external truth.
        df_num = df.copy()
        df_num['seats_int'] = pd.to_numeric(df_num['seats'], errors='coerce')
        keys = ['institution_code', 'choice_code', 'allocation_type']
        parts = df_num[df_num['category'] != TOTAL_MARKER].groupby(keys)['seats_int'].sum()
        totals = df_num[df_num['category'] == TOTAL_MARKER].groupby(keys)['seats_int'].sum()
        joined = pd.concat({'parts': parts, 'total': totals}, axis=1)
        mismatched = joined[joined['parts'] != joined['total']].dropna()
        for key, r in mismatched.iterrows():
            total_mismatches.append({
                'file': path.name,
                'institution_code': key[0], 'choice_code': key[1],
                'allocation_type': key[2],
                'sum_of_categories': r['parts'], 'total_row': r['total'],
            })

        key_cols = ['institution_code', 'choice_code',
                    'allocation_type', 'category', 'gender']
        dups = int(df.duplicated(subset=key_cols, keep=False).sum())
        dup_rows += dups

        summary.append({
            'file': path.name, 'rows': len(df),
            'bad_cat': bad_cat, 'bad_lane': bad_lane,
            'bad_seats': bad_seats, 'dups': dups, 'anomaly': anomaly_rows,
        })

    # -------- Report --------
    print(f"{'FILE':<20}{'ROWS':>8}{'CAT':>6}{'LANE':>6}"
          f"{'SEATS':>7}{'DUP':>5}{'ANOM':>6}")
    print('-' * 58)
    for s in summary:
        print(f"{s['file']:<20}{s['rows']:>8,}{s['bad_cat']:>6}"
              f"{s['bad_lane']:>6}{s['bad_seats']:>7}{s['dups']:>5}"
              f"{s['anomaly']:>6}")

    print(f'\nExact duplicate (institution+choice+lane+category+gender) rows: {dup_rows}')

    print('\n=== DUPLICATE FILE CONTENT ===')
    any_dup = False
    for h, files in hashes.items():
        if len(files) > 1:
            any_dup = True
            print(f'  \u26a0 {files}')
    if not any_dup:
        print('  \u2713 None')

    def report(title: str, counter: Counter) -> None:
        print(f'\n=== UNKNOWN {title} ({len(counter)}) ===')
        if not counter:
            print('  \u2713 All recognised')
            return
        for v, c in sorted(counter.items(), key=lambda x: -x[1])[:25]:
            print(f'  [{v}] : {c}')

    report('CATEGORIES', unknown_cats)
    report('ALLOCATION LANES', unknown_lanes)

    if file_errors:
        print('\n=== FILE ERRORS ===')
        for f, e in file_errors:
            print(f'  \u2717 {f}: {e}')

    print(f'\n=== QUARANTINED EXTRACTION ANOMALIES ({len(anomalies)}) ===')
    print('  (category values individually verified as PDF column-shift')
    print('   artifacts, not real category codes. Excluded from seats')
    print('   and logged to seats_ingest_errors, not counted below.)')
    if not anomalies:
        print('  \u2713 None')
    for a in anomalies[:25]:
        print(f"  \u26a0 {a['file']}: institution_code={a['institution_code']} "
              f"choice_code={a['choice_code']} "
              f"category-field='{a['raw_category']}' "
              f"lane='{a['raw_allocation_type']}'")

    print(f'\n=== TOTAL-ROW RECONCILIATION MISMATCHES ({len(total_mismatches)}) ===')
    print('  (sum of category rows vs that lane\'s own "Total" row,')
    print('   per institution_code + choice_code + allocation_type)')
    if not total_mismatches:
        print('  \u2713 None — every lane\'s Total row matches its category rows')
    for m in total_mismatches[:25]:
        print(f"  \u26a0 {m['file']}: institution_code={m['institution_code']} "
              f"choice_code={m['choice_code']} lane={m['allocation_type']} "
              f"sum={m['sum_of_categories']} total_row={m['total_row']}")

    total = (sum(len(x) for x in (unknown_cats, unknown_lanes))
             + len(file_errors) + len(total_mismatches))
    print(f'\n{"=" * 58}\nTotal issues: {total}  '
          f'(+ {len(anomalies)} quarantined anomalies, handled separately)'
          f'\n{"=" * 58}')
    sys.exit(1 if total else 0)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'data/raw/seats')
