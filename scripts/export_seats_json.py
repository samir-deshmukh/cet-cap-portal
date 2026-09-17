"""
export_seats_json.py — Build the static per-course seat-matrix JSON files
that power the "Seat Matrix" table in the college detail slide (see
docs/PROJECT_DESCRIPTION.md and the request that added this feature).

Reads data/raw/seats/<COURSE>_SM.csv (already-extracted seat-matrix PDFs)
and writes site/data/seats_<COURSE>.json, one file per course, shaped so
the front end can render the exact same category/allocation-type layout
as the official CET seat-matrix PDF (State Level / HU / OHU / PWD / DEF
rows, categories as columns, G/L sub-columns, a Total row).

Grouping key: institution_code -> choice_code (a single institute can
have more than one choice code for the same course family, i.e. more
than one specialization/seat block — all are kept, not merged, so
nothing is silently collapsed).

Run:
    python scripts/export_seats_json.py --raw data/raw/seats --out site/data
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

COURSES = ["BBA", "BCA", "MBA", "MCA"]

# The row order the official PDF uses, top to bottom.
ALLOC_ORDER = ["State Level", "HU", "OHU", "PWD", "DEF"]
# The column order the official PDF uses, left to right (Total last).
CATEGORY_ORDER = ["OPEN", "SC", "ST", "VJDT", "VJ/DT", "NTB", "NTC", "NTD", "OBC", "SEBC", "EWS", "Total"]


def category_sort_key(cat: str) -> int:
    try:
        return CATEGORY_ORDER.index(cat)
    except ValueError:
        return len(CATEGORY_ORDER)


def alloc_sort_key(alloc: str) -> int:
    try:
        return ALLOC_ORDER.index(alloc)
    except ValueError:
        return len(ALLOC_ORDER)


def export_course(raw_path: Path) -> dict:
    """institution_code -> choice_code -> {alloc_type: [{category, G, L, total}]}"""
    out: dict = {}
    with raw_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            inst = row["institution_code"].strip()
            choice = row["choice_code"].strip()
            alloc = row["allocation_type"].strip()
            cat = row["category"].strip()
            gender = row["gender"].strip()
            seats_raw = row["seats"].strip()
            if not inst or not choice or not alloc:
                continue
            try:
                seats = int(float(seats_raw))
            except ValueError:
                continue

            out.setdefault(inst, {})
            out[inst].setdefault(choice, {})
            out[inst][choice].setdefault(alloc, {})
            bucket = out[inst][choice][alloc].setdefault(cat, {"category": cat, "G": None, "L": None, "total": None})
            if cat == "Total":
                bucket["total"] = seats
            elif gender == "G":
                bucket["G"] = seats
            elif gender == "L":
                bucket["L"] = seats
            elif not gender:
                # Some extracted CAP seat-matrix rows do not carry a G/L
                # split. Keep the actual category total instead of dropping
                # it; the UI will span the G/L pair for this unsplit value.
                bucket["total"] = seats

    # Convert the per-category dicts into sorted lists, and alloc dicts
    # into sorted {alloc_type: [...]} keyed objects (JSON needs string keys
    # anyway, and this keeps allocation order stable client-side too).
    result: dict = {}
    for inst, choices in out.items():
        result[inst] = {}
        for choice, allocs in choices.items():
            ordered_allocs = {}
            for alloc in sorted(allocs.keys(), key=alloc_sort_key):
                rows = sorted(allocs[alloc].values(), key=lambda r: category_sort_key(r["category"]))
                ordered_allocs[alloc] = rows
            result[inst][choice] = ordered_allocs
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw/seats")
    ap.add_argument("--out", default="site/data")
    args = ap.parse_args()

    raw_dir = Path(args.raw)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for course in COURSES:
        raw_path = raw_dir / f"{course}_SM.csv"
        if not raw_path.exists():
            print(f"skip {course}: {raw_path} not found")
            continue
        data = export_course(raw_path)

        json_path = out_dir / f"seats_{course}.json"
        json_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        js_path = out_dir / f"seats_{course}.js"
        js_path.write_text(
            "window.__SEAT_DATA__ = window.__SEAT_DATA__ || {};\n"
            f"window.__SEAT_DATA__['{course}'] = " + json.dumps(data, ensure_ascii=False) + ";\n",
            encoding="utf-8",
        )
        n_inst = len(data)
        n_choices = sum(len(v) for v in data.values())
        print(f"{course}: {n_inst} institutes, {n_choices} choice codes -> {json_path.name} / {js_path.name}")


if __name__ == "__main__":
    main()
