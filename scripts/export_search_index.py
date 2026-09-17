"""
export_search_index.py — Build site/data/search_index.json, the real
data behind the "Step 01/02" eligibility-search widget on the site
(percentile + city + course -> ranked college list -> detail slide).

This is a SEPARATE export from export_trends_json.py's cutoffs_*.json,
which is grouped by (branch, category, quota) for the trends chart.
This one needs one representative row PER (institution, course) plus
city/website — data the trends export doesn't carry — so it queries
the DB directly rather than reusing that file.

Which cutoff represents "the" cutoff for a college+course?
    The site's original search UI takes a single percentile input with
    no category/quota selector, so it needs ONE number per
    (institution, program_family). We use:
        base_category = 'OPEN', is_ladies = 0, most recent year on
        record, the single row with the MINIMUM percentile across ALL
        sections (HU/OHU/SL/...) and rounds that year — percentile and
        rank_number are taken from that SAME row (see build_index()'s
        SQL comment for why that has to be enforced explicitly, not
        assumed).
    Rationale: a real candidate almost always qualifies for at least
    one section (their home-university one, or State Level / OHU if
    not), so the easiest section they could actually have used is the
    right comparison — not one arbitrarily fixed section. Restricting
    to a single section (e.g. State Level only) would drop 795 of 969
    institutes that have no SL-lane OPEN row at all.
    This mirrors export_trends_json.py's own MIN(percentile)-collapses-
    stages logic, applied across sections instead of stages, for the
    same reason: it's the number a candidate needed to actually clear
    to hold *a* seat, not every quota's seat. Unlike that export
    though, this one collapses across MULTIPLE ROUNDS, not just stages
    within one round — that turned out to matter (see build_index()).

Institutes with no OPEN/non-ladies cutoff row in any year (46 of 969)
are excluded from this index rather than guessed at — same
quarantine-not-fabricate rule as the rest of this project (see
docs/DECISIONS.md).

Seats: `total_seats` is the sum of seats-table `is_total=1` rows for
that institution+program_family at the most recent capture_year,
across every choice_code and lane. Since one institution+family can
have multiple choice_codes (branches/specializations), this is a
combined-capacity approximation, not a single branch's seat count.
Left null when the seat matrix has no rows for that institution+family
(the 9 institutes missing from `institutes` entirely, or families the
seat-matrix PDFs didn't cover for that institute).

Run:
    python scripts/export_search_index.py --db db/cet_cap.db --out site/data
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def build_index(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.cursor()

    # One history row per (institution_code, program_family, year).
    # Keep BOTH ends of that year's OPEN/non-ladies cutoff range across
    # every section and round. The site's graph needs the actual yearly
    # highest and lowest percentiles, not the gap from the user's score.
    # For the representative current percentile/rank, use the lowest
    # percentile row (the easiest OPEN/non-ladies cutoff) so the existing
    # eligibility/search semantics remain unchanged.
    cur.execute('''
        WITH yearly AS (
            SELECT
                c.institution_code,
                p.program_family,
                c.year,
                MIN(c.percentile) AS low_percentile,
                MAX(c.percentile) AS high_percentile
            FROM cutoffs c
            JOIN programs p ON p.program_id = c.program_id
            WHERE c.base_category = 'OPEN' AND c.is_ladies = 0
                  AND NOT (c.rank_number = 0 AND c.percentile = 0.0)
            GROUP BY c.institution_code, p.program_family, c.year
        ),
        lowest_rows AS (
            SELECT
                c.institution_code, p.program_family, c.year,
                c.percentile, c.rank_number,
                ROW_NUMBER() OVER (
                    PARTITION BY c.institution_code, p.program_family, c.year
                    ORDER BY c.percentile ASC, c.rank_number DESC
                ) AS rn
            FROM cutoffs c
            JOIN programs p ON p.program_id = c.program_id
            WHERE c.base_category = 'OPEN' AND c.is_ladies = 0
                  AND NOT (c.rank_number = 0 AND c.percentile = 0.0)
        )
        SELECT
            y.institution_code,
            y.program_family,
            y.year,
            y.low_percentile,
            y.high_percentile,
            r.rank_number
        FROM yearly y
        JOIN lowest_rows r
          ON r.institution_code = y.institution_code
         AND r.program_family = y.program_family
         AND r.year = y.year
         AND r.rn = 1
        ORDER BY y.institution_code, y.program_family, y.year
    ''')

    by_key: dict[tuple[str, str], list[dict]] = {}
    for institution_code, family, year, low, high, rank in cur.fetchall():
        by_key.setdefault((institution_code, family), []).append(
            {'year': year, 'percentile': low, 'low': low, 'high': high, 'rank': rank}
        )

    # Graph-only yearly ranges. Keep this query separate from the college-list
    # history query above so the graph can never accidentally consume the
    # all-years eligibility aggregation. These values are calculated directly
    # from the underlying cutoff rows for EACH individual year.
    cur.execute('''
        SELECT
            c.institution_code,
            p.program_family,
            c.year,
            MIN(c.percentile) AS low_percentile,
            MAX(c.percentile) AS high_percentile
        FROM cutoffs c
        JOIN programs p ON p.program_id = c.program_id
        WHERE c.base_category = 'OPEN' AND c.is_ladies = 0
              AND NOT (c.rank_number = 0 AND c.percentile = 0.0)
        GROUP BY c.institution_code, p.program_family, c.year
        ORDER BY c.institution_code, p.program_family, c.year
    ''')
    graph_by_key: dict[tuple[str, str], list[dict]] = {}
    for institution_code, family, year, low, high in cur.fetchall():
        graph_by_key.setdefault((institution_code, family), []).append({
            'year': year, 'low': low, 'high': high
        })

    # institutes: name/city/website
    cur.execute('SELECT institution_code, institution_name, city, website FROM institutes')
    institutes = {row[0]: {'name': row[1], 'city': row[2], 'website': row[3]}
                  for row in cur.fetchall()}

    # seats: summed is_total rows at each institution+family's own most
    # recent capture_year (not a single global year, in case future
    # runs mix capture years across families).
    cur.execute('''
        SELECT institution_code, program_family, SUM(seats)
        FROM seats
        WHERE is_total = 1
              AND capture_year = (
                  SELECT MAX(s2.capture_year) FROM seats s2
                  WHERE s2.institution_code = seats.institution_code
                        AND s2.program_family = seats.program_family
              )
        GROUP BY institution_code, program_family
    ''')
    seat_totals = {(r[0], r[1]): r[2] for r in cur.fetchall()}

    # Compact drawer metadata: enough information to populate the drawer's
    # college-specific Category and Quota/Type controls immediately, without
    # loading/scanning any cutoff payload. The actual cutoff rows remain in
    # the lazy college/year runtime files.
    cur.execute('SELECT base_code, category_full FROM base_categories')
    category_labels = {code: full for code, full in cur.fetchall()}
    cur.execute('SELECT section_code, section_full FROM sections')
    section_labels = {code: full for code, full in cur.fetchall()}
    cur.execute('''
        SELECT p.program_family, c.institution_code, c.is_ladies, c.year,
               c.base_category, c.section_code
        FROM cutoffs c
        JOIN programs p ON p.program_id = c.program_id
        WHERE NOT (c.rank_number = 0 AND c.percentile = 0.0)
        ORDER BY p.program_family, c.institution_code, c.is_ladies,
                 c.year, c.base_category, c.section_code
    ''')
    drawer_meta = {}
    for family, inst, ladies, year, base_cat, section in cur.fetchall():
        key = (inst, family)
        meta = drawer_meta.setdefault(key, {
            'years': set(),
            'categories': {False: {}, True: {}},
        })
        meta['years'].add(int(year))
        cat_map = meta['categories'][bool(ladies)]
        sec_set = cat_map.setdefault(base_cat, set())
        sec_set.add(section)

    records = []
    skipped_no_institute = 0
    for (institution_code, family), history in sorted(by_key.items()):
        inst = institutes.get(institution_code)
        if inst is None:
            skipped_no_institute += 1
            continue
        history.sort(key=lambda h: h['year'])
        latest = history[-1]
        graph_history = sorted(
            graph_by_key.get((institution_code, family), []),
            key=lambda h: h['year']
        )[-4:]
        records.append({
            'id': f'{institution_code}_{family}',
            'institution_code': institution_code,
            'name': inst['name'],
            'city': inst['city'],
            'website': inst['website'],
            'course': family,
            'cutoff': latest['rank'],
            'percentile': latest['percentile'],
            'seats': seat_totals.get((institution_code, family)),
            'history': history[-4:],
            # Graph-only data: exact min/max percentile for each individual year.
            # This is intentionally separate from `history`, which is also used
            # by the college-list eligibility/status logic across all years.
            'graph_history': graph_history,
            'drawer': {
                'y': sorted(drawer_meta.get((institution_code, family), {}).get('years', set())),
                'c': {
                    '0': [
                        {'v': cat, 'q': sorted(secs)}
                        for cat, secs in sorted(drawer_meta.get((institution_code, family), {}).get('categories', {}).get(False, {}).items())
                    ],
                    '1': [
                        {'v': cat, 'q': sorted(secs)}
                        for cat, secs in sorted(drawer_meta.get((institution_code, family), {}).get('categories', {}).get(True, {}).items())
                    ],
                },
            }
        })

    if skipped_no_institute:
        print(f'Skipped {skipped_no_institute} (institution_code, family) '
              f'rows with no matching institutes record.')
    return records, category_labels, section_labels


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    records, category_labels, section_labels = build_index(conn)
    conn.close()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'search_index.json'
    json_text = json.dumps(records, ensure_ascii=False)
    out_path.write_text(json_text)

    # .js sibling for the same reason as cutoffs_*.js in
    # export_trends_json.py: index.html loads this via <script src>
    # instead of fetch(), since fetch() of local files is blocked
    # under file:// and <script src> isn't.
    js_path = out_dir / 'search_index.js'
    # Small global label map used by the compact drawer catalog. Keeping labels
    # out of every college record cuts the inline search payload substantially.
    label_obj = {
        'categories': category_labels,
        'sections': section_labels,
    }
    labels_json = json.dumps(label_obj, ensure_ascii=False, separators=(',', ':'))
    js_path.write_text(
        f'window.__SEARCH_INDEX__ = {json_text};\n'
        f'window.__DRAWER_LABELS__ = {labels_json};\n'
    )
    (out_dir / 'drawer_labels.json').write_text(json.dumps(label_obj, ensure_ascii=False, indent=2))

    cities = sorted({r['city'] for r in records if r['city']})
    courses = sorted({r['course'] for r in records})
    size_kb = out_path.stat().st_size / 1024
    print(f'{len(records)} college+course records, '
          f'{len(cities)} cities, courses={courses}, '
          f'{size_kb:.1f} KB -> {out_path} (+ {js_path.name})')


if __name__ == '__main__':
    main()
