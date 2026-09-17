import argparse, json
from pathlib import Path

COURSES = ["BBA", "BCA", "MBA", "MCA"]

def export_course(src_path, out_root):
    src = json.loads(Path(src_path).read_text())
    course = src["course"]
    by_col = {}
    for row in src.get("pdf_rows", []):
        by_col.setdefault(str(row["college"]), []).append(row)
    out_dir = Path(out_root) / course
    out_dir.mkdir(parents=True, exist_ok=True)
    for college, rows in by_col.items():
        branch_ids = {str(r["branch"]) for r in rows}
        payload = {
            "course": course, "college": college,
            "branches": {str(k): v for k, v in src.get("branches", {}).items() if str(k) in branch_ids},
            "categories": src.get("categories", {}),
            "sections": src.get("sections", {}),
            "pdf_rows": rows,
        }
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        (out_dir / f"{college}.js").write_text(
            "window.__COLLEGE_CUTOFF_DATA__=window.__COLLEGE_CUTOFF_DATA__||{};"
            + f"window.__COLLEGE_CUTOFF_DATA__[{json.dumps(course+":"+college)}]={text};\n"
        )
    return len(by_col), sum(map(len, by_col.values()))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    args=ap.parse_args()
    total=0
    for course in COURSES:
        n,r=export_course(Path(args.data_dir)/f"cutoffs_{course}.json", args.out)
        total += r
        print(f"{course}: {n} college files, {r} pdf rows")
    print(f"Total exported pdf rows: {total}")

if __name__ == "__main__": main()
