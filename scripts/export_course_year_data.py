"""Export verified cutoff data into course/year runtime files.

Each generated file contains all normalized groups and all raw PDF rows for one
course/year. Runtime can load every year for the selected course in parallel,
then access college data directly without scanning a multi-year course file.
The SQLite database remains the build-time source of truth.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

COURSES = ["BBA", "BCA", "MBA", "MCA"]

def export_course(src_path: Path, out_root: Path):
    src = json.loads(src_path.read_text())
    course = src["course"]
    years = sorted({int(r["year"]) for r in src.get("pdf_rows", [])})
    out_dir = out_root / course
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for year in years:
        pdf_rows = [r for r in src.get("pdf_rows", []) if int(r["year"]) == year]
        by_college = {}
        for r in pdf_rows:
            by_college.setdefault(str(r['college']), []).append(r)
        year_dir = out_dir / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        for college, rows in by_college.items():
            college_branches = {}
            for r in rows:
                college_branches[str(r['branch'])] = src.get('branches', {}).get(str(r['branch']), f"#{r['branch']}")
            used_sections = sorted({r['section'] for r in rows})
            sections = {code: src.get('sections', {}).get(code, code) for code in used_sections}
            college_payload = {
                'course': course,
                'year': year,
                'college': college,
                'branches': college_branches,
                'sections': sections,
                'pdf_rows': rows,
            }
            ctext = json.dumps(college_payload, ensure_ascii=False, separators=(",", ":"))
            key = f"{course}:{year}:{college}"
            (year_dir / f"{college}.js").write_text(
                "window.__COLLEGE_YEAR_DATA__=window.__COLLEGE_YEAR_DATA__||{};"
                + f"window.__COLLEGE_YEAR_DATA__[{json.dumps(key)}]={ctext};\n"
            )
        results.append((year, len(by_college), len(pdf_rows)))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out_root = Path(args.out)
    manifest = {}
    for course in COURSES:
        results = export_course(Path(args.data_dir) / f"cutoffs_{course}.json", out_root / "courses")
        manifest[course] = [year for year, *_ in results]
        print(course + ": " + ", ".join(f"{y} ({c} colleges, {r} rows)" for y,c,r in results))
    manifest_text = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    (out_root / "courses" / "manifest.js").write_text(
        "window.__COURSE_YEAR_MANIFEST__=" + manifest_text + ";\n"
    )
    (out_root / "courses" / "manifest.json").write_text(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    main()
