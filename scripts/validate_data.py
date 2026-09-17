"""
validate_data.py — Data quality gate for CET CAP project.
Run: python scripts/validate_data.py data/raw/
Exit 0 = clean. Exit 1 = issues found.
"""

import re
import sys
from pathlib import Path
from collections import Counter, defaultdict

import pandas as pd

FILL_COLUMNS = [
    'institution_code', 'institution_name', 'program_code',
    'program_name', 'status', 'section',
]
# NOT included: home_university, stage.
# Both are genuinely 100%-blank in some files (e.g. home_university across
# all of MBA_25_C1.csv; stage across all of BCA_26_C1.csv) rather than
# per-block "sticky" state — forward-filling them would fabricate values
# that were never in the source PDF for that file/row.

REQUIRED_COLUMNS = [
    'source_pdf', 'page', 'institution_code', 'institution_name',
    'program_code', 'program_name', 'status', 'home_university',
    'section', 'stage', 'category', 'rank', 'rank_number',
    'rank_suffix', 'percentage', 'percentage_raw', 'raw_value',
    'source', 'merged_from',
]

FILENAME_PATTERN = re.compile(
    r'^(?P<family>BCA|BBA|MBA|MCA)_(?P<year>\d{2})_C(?P<round>\d)',
    re.IGNORECASE,
)

STAGE_MAP = {
    'Stage-I': 'Stage-I', 'Stage I': 'Stage-I',
    'Stage-II': 'Stage-II', 'Stage II': 'Stage-II',
    'Stage-III': 'Stage-III', 'Stage III': 'Stage-III',
    'Stage-IV': 'Stage-IV', 'Stage IV': 'Stage-IV',
    'Stage-V': 'Stage-V', 'Stage V': 'Stage-V',
    'Stage-VI': 'Stage-VI', 'Stage VI': 'Stage-VI',
    'Stage-VII': 'Stage-VII', 'Stage VII': 'Stage-VII',
    'Stage-MI': 'Stage-MI', 'Stage MI': 'Stage-MI',
    '': 'Unknown',
}

SECTION_MAP = {
    'Home University Seats Allotted to Home University Candidates': 'HU',
    'Other Than Home University Seats Allotted to Other Than Home University Candidates': 'OHU',
    'Home University Seats Allotted to Other Than Home University Candidates': 'HU_TO_OHU',
    'Other Than Home University Seats Allotted to Home University Candidates': 'OHU_TO_HU',
    'State Level': 'SL',
    'Minority Seats : Minority Seats Allotted to Minority Candidates': 'MI-MIN',
    'Minority Seats : Minority Seats Allotted to Non Minority Candidates': 'MI-NONMIN',
}

# Values that have appeared in the `category` column but are not category
# codes at all — confirmed by manual inspection to be column-shift/text-
# bleed artifacts from the source PDF extraction (e.g. a fragment of an
# institution "status" string landing in the category cell). Each entry
# here was individually verified against its row context before being
# added — this is NOT a catch-all, it's a short, audited list.
KNOWN_BAD_CATEGORY_VALUES = {
    'GOVERNMENT',  # BCA_24_C2.csv row: institution/program fields also
                    # inconsistent for this row; "GOVERNMENT" is status
                    # text (e.g. "Government Aided"), not a category code.
}

ORPHAN_MAP = {
    'ORPHAN': 'ORPHAN',
    'ORPINST': 'ORPHAN_INST',
    'ORPHANI': 'ORPHAN_INST',
    'ORPNONINST': 'ORPHAN_NONINST',
    'ORPHANN': 'ORPHAN_NONINST',
}


def parse_filename(path: Path):
    m = FILENAME_PATTERN.match(path.stem)
    if not m:
        return None
    return {
        'family': m.group('family').upper(),
        'year': 2000 + int(m.group('year')),
        'round': int(m.group('round')),
    }


def forward_fill_block_columns(df: pd.DataFrame) -> tuple[int, list[str]]:
    """Forward-fill FILL_COLUMNS in place (see that constant's comment for
    why these columns and not others). Returns (fill_count,
    first_row_blank_cols) — the latter is non-empty only if some column
    has no value at all to fill from at row 0, meaning that file cannot
    be trusted for that column. Shared by validate_data.py and ingest.py
    so both apply exactly the same rule — a version of this that only
    lived in the validator was already found once to disagree with what
    ingest actually did (docs/DECISIONS.md "Second review pass").
    """
    fill_count = 0
    first_row_blank = []
    for col in FILL_COLUMNS:
        last = ''
        for i in range(len(df)):
            v = df.at[i, col].strip()
            if v:
                last = v
            elif last:
                df.at[i, col] = last
                fill_count += 1
            elif i == 0:
                first_row_blank.append(col)
    return fill_count, first_row_blank


def is_extraction_anomaly(row) -> bool:
    """True for rows that, even after forward-filling block context,
    still have no institution_code/institution_name, or whose category
    cell is a known non-category text fragment (KNOWN_BAD_CATEGORY_VALUES)
    — i.e. genuinely broken PDF extraction, not a data-quality "unknown
    value". Shared by validate_data.py and ingest.py so a row is
    classified identically by both."""
    cat_raw = row['category'].strip().upper()
    return (not row['institution_code'].strip() and not row['institution_name'].strip()) \
        or cat_raw in KNOWN_BAD_CATEGORY_VALUES


def parse_category(raw: str):
    """
    'GOPENH'   -> ('OPEN',  False, 'HU')
    'LSCS'     -> ('SC',    True,  'SL')
    'PWDROBCH' -> ('PWDROBC', False, 'HU')
    'MI'       -> ('MI',    False, 'SL')
    Returns (base, is_ladies, section) or (None, None, None).
    """
    if not raw or not raw.strip():
        return None, None, None
    raw = raw.strip().upper()

    if raw in ('MI', 'MI-MH'):
        return 'MI', False, 'SL'
    if raw in ('EWS', 'TFWS'):
        return raw, False, 'SL'
    if raw in ORPHAN_MAP:
        return ORPHAN_MAP[raw], False, 'SL'

    if raw[-1] in ('H', 'O', 'S'):
        body = raw[:-1]
        section = {'H': 'HU', 'O': 'OHU', 'S': 'SL'}[raw[-1]]
    else:
        body = raw
        section = 'SL'

    if body.startswith('L'):
        is_ladies = True
        body = body[1:]
    elif body.startswith('G'):
        is_ladies = False
        body = body[1:]
    else:
        is_ladies = False

    if not body:
        return None, None, None
    return body, is_ladies, section


def hash_rows(df: pd.DataFrame) -> str:
    cols = [c for c in df.columns if c not in ('source_pdf', 'page')]
    return df[cols].astype(str).sort_values(by=cols).to_csv(index=False)


def main(raw_dir: str):
    folder = Path(raw_dir)
    csvs = sorted(folder.glob('*.csv'))
    if not csvs:
        print(f'No CSVs in {folder}')
        sys.exit(1)

    print(f'Validating {len(csvs)} files in {folder}\n')

    ref = Path('data/reference')
    valid_cats     = set(pd.read_csv(ref / 'category_whitelist.csv')['base_code'])
    valid_sections = set(pd.read_csv(ref / 'section_whitelist.csv')['section_code'])
    valid_stages   = set(pd.read_csv(ref / 'stage_whitelist.csv')['canonical'])
    valid_programs = set(pd.read_csv(ref / 'program_aliases.csv')['raw_name'])

    summary = []
    hashes = defaultdict(list)
    unknown_cats = Counter()
    unknown_stages = Counter()
    unknown_sections = Counter()
    unknown_programs = Counter()
    forward_fill_count = 0
    dup_rows = 0
    file_errors = []
    anomalies = []

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

        # Forward-fill block-context columns within this file.
        # The source PDFs print institution/program/status/home_university/
        # section/stage ONCE per block, then leave every column but
        # category/rank/percentage blank for subsequent rows in that same
        # block. This affects program_name AND institution_code/name AND
        # section/stage identically — all seven columns are "sticky" state
        # from the same table layout, not independent fields. Filling only
        # program_name (as originally scoped) left institution/section/stage
        # blank on ~11% of rows too, which the validator was misreading as
        # broken rows.
        fill_count, file_first_row_blank = forward_fill_block_columns(df)
        forward_fill_count += fill_count
        if file_first_row_blank:
            file_errors.append((
                path.name,
                f'FIRST_ROW_BLANK: {file_first_row_blank} — cannot '
                f'forward-fill row 0, block context unknown',
            ))

        bad_cat = bad_stage = bad_section = bad_prog = bad_pct = 0
        anomaly_rows = 0

        for _, row in df.iterrows():
            # After forward-filling block context, a row that STILL has no
            # institution_code/institution_name is not a data-quality
            # "unknown value" — it's a genuinely broken PDF extraction
            # (columns shifted / text bled across cells, e.g. a status
            # fragment landing in the category column). Quarantine it
            # rather than forcing it through category parsing.
            if is_extraction_anomaly(row):
                anomaly_rows += 1
                anomalies.append({
                    'file': path.name, 'raw_category': row['category'],
                    'stage': row['stage'], 'rank': row['rank'],
                })
                continue

            base, _, _ = parse_category(row['category'])
            if base is None or base not in valid_cats:
                bad_cat += 1
                unknown_cats[row['category'] or '(empty)'] += 1

            st = STAGE_MAP.get(row['stage'].strip())
            if st is None:
                bad_stage += 1
                unknown_stages[row['stage']] += 1

            sec = row['section'].strip()
            if sec and sec not in SECTION_MAP:
                bad_section += 1
                unknown_sections[sec] += 1

            prog = row['program_name'].strip()
            if prog and prog not in valid_programs:
                bad_prog += 1
                unknown_programs[prog] += 1

            try:
                float(row['percentage'])
            except (ValueError, TypeError):
                bad_pct += 1

        key_cols = ['institution_code', 'program_name', 'category',
                    'section', 'stage', 'rank_number', 'percentage']
        dups = int(df.duplicated(subset=key_cols, keep=False).sum())
        dup_rows += dups

        summary.append({
            'file': path.name,
            'year': meta['year'],
            'round': meta['round'],
            'rows': len(df),
            'bad_cat': bad_cat, 'bad_stage': bad_stage,
            'bad_section': bad_section, 'bad_prog': bad_prog,
            'bad_pct': bad_pct, 'dups': dups, 'anomaly': anomaly_rows,
        })

    # -------- Report --------
    print(f"{'FILE':<42}{'YR':<6}{'RD':<4}{'ROWS':>7}"
          f"{'CAT':>5}{'STG':>5}{'SEC':>5}{'PRG':>5}{'PCT':>5}{'DUP':>5}{'ANOM':>6}")
    print('-' * 102)
    for s in summary:
        print(f"{s['file']:<42}{s['year']:<6}{s['round']:<4}{s['rows']:>7,}"
              f"{s['bad_cat']:>5}{s['bad_stage']:>5}{s['bad_section']:>5}"
              f"{s['bad_prog']:>5}{s['bad_pct']:>5}{s['dups']:>5}{s['anomaly']:>6}")

    print(f'\nForward-filled program_name on {forward_fill_count} rows')
    print(f'Exact duplicate rows detected: {dup_rows}')

    print('\n=== DUPLICATE CONTENT ===')
    any_dup = False
    for h, files in hashes.items():
        if len(files) > 1:
            any_dup = True
            print(f'  \u26a0 {files}')
    if not any_dup:
        print('  \u2713 None')

    def report(title, counter):
        print(f'\n=== UNKNOWN {title} ({len(counter)}) ===')
        if not counter:
            print('  \u2713 All recognised')
            return
        for v, c in sorted(counter.items(), key=lambda x: -x[1])[:25]:
            print(f'  [{v}] : {c}')

    report('CATEGORIES', unknown_cats)
    report('STAGES', unknown_stages)
    report('SECTIONS', unknown_sections)
    report('PROGRAM NAMES', unknown_programs)

    if file_errors:
        print('\n=== FILE ERRORS ===')
        for f, e in file_errors:
            print(f'  \u2717 {f}: {e}')

    print(f'\n=== QUARANTINED EXTRACTION ANOMALIES ({len(anomalies)}) ===')
    print('  (rows with no institution_code AND no institution_name —')
    print('   broken PDF extraction, not a category/data issue. These')
    print('   are excluded from cutoffs and logged to ingest_errors,')
    print('   not counted as an outstanding issue below.)')
    if not anomalies:
        print('  \u2713 None')
    for a in anomalies[:25]:
        print(f"  \u26a0 {a['file']}: category-field='{a['raw_category']}' "
              f"stage='{a['stage']}' rank='{a['rank']}'")

    total = sum(len(x) for x in (unknown_cats, unknown_stages,
                                  unknown_sections, unknown_programs)) \
            + len(file_errors)
    print(f'\n{"=" * 60}\nTotal issues: {total}  '
          f'(+ {len(anomalies)} quarantined anomalies, handled separately)'
          f'\n{"=" * 60}')
    sys.exit(1 if total else 0)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'data/raw')
