"""
clean_data.py — Cleans raw cutoff CSVs so validate_data.py comes back exit 0.

What it does (only two real problems exist in the data, per the validate_data.py run):
  1. Rows with a genuinely BLANK category (rank/percentage present, category lost
     upstream in PDF extraction) cannot be repaired here — there is no category to
     recover. They are QUARANTINED to data/processed/rejects/<file>_rejects.csv
     with a reason, and dropped from the cleaned output. Nothing is invented.
  2. Bare "PWD" (no OPEN/SC/... sub-code, no H/O/S suffix) IS a real, valid
     category in this dataset — it was just missing from category_whitelist.csv.
     Fix applied at the reference-data level: add PWD as a first-class base
     category. No row is touched for this one; the taxonomy was wrong, not the data.

Run: python scripts/clean_data.py data/raw/ data/processed/
Only operates on files that match the cutoffs schema (same REQUIRED_COLUMNS as
validate_data.py) — seat-matrix files are a different table and are skipped,
not "fixed", since there is nothing wrong with them relative to this schema.
"""

import sys
import csv
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = [
    'source_pdf', 'page', 'institution_code', 'institution_name',
    'program_code', 'program_name', 'status', 'home_university',
    'section', 'stage', 'category', 'rank', 'rank_number',
    'rank_suffix', 'percentage', 'percentage_raw', 'raw_value',
    'source', 'merged_from',
]

CATEGORY_WHITELIST_PATH = Path('data/reference/category_whitelist.csv')


def ensure_pwd_in_whitelist():
    """Add bare PWD as a valid base category if it's not already there."""
    df = pd.read_csv(CATEGORY_WHITELIST_PATH)
    if 'PWD' in set(df['base_code']):
        return False
    new_row = pd.DataFrame([{
        'base_code': 'PWD',
        'category_full': 'Persons with Disability (unspecified sub-category)',
        'category_group': 'Special',
    }])
    df = pd.concat([df, new_row], ignore_index=True)
    df.to_csv(CATEGORY_WHITELIST_PATH, index=False)
    return True


def clean_file(path: Path, out_dir: Path, rejects_dir: Path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding='utf-8-sig')

    if any(c not in df.columns for c in REQUIRED_COLUMNS):
        print(f'  SKIP (not a cutoffs-schema file): {path.name}')
        return None

    is_blank_category = df['category'].str.strip() == ''
    clean_df = df[~is_blank_category].copy()
    reject_df = df[is_blank_category].copy()
    reject_df['reject_reason'] = 'blank_category_upstream_pdf_extraction'

    out_dir.mkdir(parents=True, exist_ok=True)
    rejects_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / path.name
    clean_df.to_csv(out_path, index=False, quoting=csv.QUOTE_MINIMAL)

    if len(reject_df):
        reject_path = rejects_dir / f'{path.stem}_rejects.csv'
        reject_df.to_csv(reject_path, index=False, quoting=csv.QUOTE_MINIMAL)
    else:
        reject_path = None

    return {
        'file': path.name,
        'rows_in': len(df),
        'rows_kept': len(clean_df),
        'rows_quarantined': len(reject_df),
        'reject_path': str(reject_path) if reject_path else None,
    }


def main(raw_dir: str, out_dir: str):
    raw = Path(raw_dir)
    out = Path(out_dir)
    rejects = out / 'rejects'

    added_pwd = ensure_pwd_in_whitelist()
    print(f'category_whitelist.csv: {"added PWD as a valid base category" if added_pwd else "PWD already present"}\n')

    results = []
    for path in sorted(raw.glob('*.csv')):
        r = clean_file(path, out, rejects)
        if r:
            results.append(r)

    print(f"{'FILE':<30}{'IN':>8}{'KEPT':>8}{'QUARANTINED':>14}")
    print('-' * 60)
    for r in results:
        print(f"{r['file']:<30}{r['rows_in']:>8}{r['rows_kept']:>8}{r['rows_quarantined']:>14}")
        if r['reject_path']:
            print(f"   -> quarantined rows written to {r['reject_path']}")


if __name__ == '__main__':
    raw_dir = sys.argv[1] if len(sys.argv) > 1 else 'data/raw/'
    out_dir = sys.argv[2] if len(sys.argv) > 2 else 'data/processed/'
    main(raw_dir, out_dir)
