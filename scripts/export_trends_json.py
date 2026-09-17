"""
export_trends_json.py — Build the static per-course JSON files that power
the "Cutoff Trends" chart on the website's results section (see
docs/cutoff-trends-graph-spec.md). Read-only against the ingested
`cutoffs` DB; writes one JSON file per program_family into
site/data/.

Grouping key: (program_id "branch", institution_code "college",
base_category, is_ladies, section_code). This is exactly the tuple the
UI's Course -> Branch -> College -> Category -> Quota/Type filters narrow
down to (see spec §3).

Multi-stage-within-a-round handling (discovered while building this,
NOT anticipated in the original spec):
    A single (year, round) can carry more than one `stage_code`
    (Stage-I, Stage-II, ... — supplementary allotment stages run within
    the same CAP round, not to be confused with the round itself). Where
    that happens, percentile decreases stage-over-stage as more seats
    fill (verified empirically: the max-rank_number row within a
    (year, round) group always matches the min-percentile row). This
    export takes MIN(percentile) per (year, round) as that round's
    closing cutoff — the number a candidate would actually need to have
    cleared to hold a seat by the end of the round — rather than
    treating every stage as its own chart point (which would draw
    2-3x the intended points and make the round-line noisy/misleading).
    ~10-21% of (year, round) groups (varies by course) have >1 stage.

Run:
    python scripts/export_trends_json.py --db db/cet_cap.db --out site/data
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def load_ref_maps(conn: sqlite3.Connection) -> tuple[dict, dict]:
    cur = conn.cursor()
    cur.execute('SELECT base_code, category_full, category_group FROM base_categories')
    categories = {code: {'full': full, 'group': grp} for code, full, grp in cur.fetchall()}
    cur.execute('SELECT section_code, section_full FROM sections')
    sections = {code: full for code, full in cur.fetchall()}
    return categories, sections


def export_family(conn: sqlite3.Connection, family: str, categories: dict, sections: dict) -> dict:
    cur = conn.cursor()

    # Closing cutoff per (branch, college, category, ladies, section, year, round):
    # MIN(percentile) collapses multi-stage rounds to the round's final
    # closing value (see module docstring). MAX(rank_number) is carried
    # alongside as the corresponding rank for that same closing row.
    cur.execute('''
        SELECT
            p.program_id, p.program_name_raw,
            c.institution_code, i.institution_name,
            c.base_category, c.is_ladies, c.section_code,
            c.year, c.round,
            MIN(c.percentile) AS closing_percentile,
            MAX(c.rank_number) AS closing_rank
        FROM cutoffs c
        JOIN programs p ON p.program_id = c.program_id
        JOIN institutes i ON i.institution_code = c.institution_code
        WHERE p.program_family = ?
              AND NOT (c.rank_number = 0 AND c.percentile = 0.0)
        GROUP BY p.program_id, c.institution_code, c.base_category,
                 c.is_ladies, c.section_code, c.year, c.round
        ORDER BY p.program_id, c.institution_code, c.base_category,
                 c.is_ladies, c.section_code, c.year, c.round
    ''', (family,))

    branches: dict[str, str] = {}
    colleges: dict[str, str] = {}
    used_categories: set[str] = set()
    used_sections: set[str] = set()
    groups: dict[tuple, list] = {}

    for (pid, pname, inst_code, inst_name, base_cat, is_ladies, section,
         year, round_no, pct, rank) in cur.fetchall():
        branches[str(pid)] = pname
        colleges[inst_code] = inst_name
        used_categories.add(base_cat)
        used_sections.add(section)
        key = (pid, inst_code, base_cat, is_ladies, section)
        groups.setdefault(key, []).append({
            'year': year, 'round': round_no,
            'percentile': round(pct, 4),
            'rank': rank,
        })

    group_list = [
        {
            'branch': pid, 'college': inst_code, 'category': base_cat,
            'ladies': bool(is_ladies), 'section': section,
            'points': sorted(pts, key=lambda pt: (pt['year'], pt['round'])),
        }
        for (pid, inst_code, base_cat, is_ladies, section), pts in groups.items()
    ]

    # Raw-PDF-shaped rows are kept separately for the drawer. Unlike the
    # trend `groups` above (which intentionally collapses multiple stages
    # within a round to a closing cutoff), these rows preserve every raw
    # cutoff line needed to reconstruct the college's PDF-style tables.
    cur.execute('''
        SELECT
            c.program_id, c.institution_code, c.base_category, c.is_ladies,
            c.section_code, c.year, c.round, c.stage_code,
            c.rank_number, c.percentile, c.raw_category
        FROM cutoffs c
        JOIN programs p ON p.program_id = c.program_id
        WHERE p.program_family = ?
              AND NOT (c.rank_number = 0 AND c.percentile = 0.0)
        ORDER BY c.program_id, c.institution_code, c.year, c.round,
                 c.section_code, c.stage_code, c.base_category,
                 c.is_ladies, c.source_page, c.rank_number
    ''', (family,))
    pdf_rows = [
        {
            'branch': pid, 'college': inst, 'category': cat,
            'ladies': bool(ladies), 'section': section,
            'year': year, 'round': round_no, 'stage': stage,
            'rank': rank, 'percentile': round(pct, 7),
            'raw_category': raw_category,
        }
        for (pid, inst, cat, ladies, section, year, round_no, stage,
             rank, pct, raw_category) in cur.fetchall()
    ]

    return {
        'course': family,
        'branches': {str(k): v for k, v in branches.items()},
        'colleges': colleges,
        'categories': {c: categories.get(c, {'full': c, 'group': 'Other'}) for c in used_categories},
        'sections': {s: sections.get(s, s) for s in used_sections},
        'groups': group_list,
        'pdf_rows': pdf_rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--db', default='db/cet_cap.db')
    ap.add_argument('--out', default='site/data')
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    categories, sections = load_ref_maps(conn)

    cur = conn.cursor()
    cur.execute('SELECT DISTINCT program_family FROM programs ORDER BY program_family')
    families = [r[0] for r in cur.fetchall()]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for family in families:
        data = export_family(conn, family, categories, sections)
        out_path = out_dir / f'cutoffs_{family}.json'
        json_text = json.dumps(data, separators=(',', ':'))
        out_path.write_text(json_text)

        # Browser runtime data is generated separately by
        # export_course_year_data.py, which splits this verified export into
        # course/year files. Do not emit a course-wide JS payload here: that
        # was the source of the mobile loading bottleneck this architecture
        # replaces.
        size_kb = out_path.stat().st_size / 1024
        manifest.append({'course': family, 'file': out_path.name,
                          'groups': len(data['groups']), 'colleges': len(data['colleges'])})
        print(f'{family}: {len(data["groups"])} groups, '
              f'{len(data["colleges"])} colleges, {size_kb:.1f} KB -> {out_path}')

    (out_dir / 'manifest.json').write_text(json.dumps({'courses': manifest}, indent=2))
    conn.close()


if __name__ == '__main__':
    main()
