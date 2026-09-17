#!/usr/bin/env python3
"""
CET Seat Matrix Extractor (pdfplumber-based, generalized)
===========================================================

Extracts data from Maharashtra State CET Cell "Provisional/Final Seat
Distribution" (Seat Matrix) PDFs -- built against BBA_SM.pdf but written
to generalize across other CET Cell seat-matrix PDFs (different course
types: Engineering, MBA, Pharmacy, etc.), which can differ in:
  - which reservation categories appear (OPEN/SC/ST/... vs others)
  - which course-row columns appear (SI / MS Seats / All India /
    Minority Seats / Institute Seats / In / N-In -- not all PDFs have
    all of these)
  - whether a page has a clean digital text layer at all (some are
    scanned images with no text layer)
  - how many course rows/tables appear per page

Design:
  * Categories are read from each table's own "Category" row rather
    than hard-coded, so a PDF with a different category set still works.
  * Course-row numeric field names are read from that table's own
    header row (between "Course Name" and the end), so PDFs with a
    different/extra/missing set of columns still work.
  * A page can contribute zero, one, or several course tables; each
    table can have one or more course rows before its "Category" row.
  * If a page's native text layer is missing or garbled (no table with
    a "Choice Code" header can be found, or extract_text() returns
    very little), it's rendered to an image and OCR'd (pytesseract).
    OCR text is run through a best-effort line-based fallback parser.
    Pages that still can't be matched emit a metadata-only ("partial")
    record instead of being silently dropped.
  * fitz/PyMuPDF is not used (not installable in this environment);
    pdfplumber does both text/table extraction and page-image
    rendering for the OCR fallback.

Output (per PDF):
  <stem>_courses.csv/json     one row per institute+course (master record)
  <stem>_seats_long.csv/json  one row per institute+course+alloc+cat+gender
  <stem>_page_audit.json      per-page record counts / errors / method

Usage:
    python seat_matrix_extractor_v2.py BBA_SM.pdf --out extracted
    python seat_matrix_extractor_v2.py /path/to/pdf_folder --out extracted
    python seat_matrix_extractor_v2.py file.pdf --out extracted --no-ocr
    python seat_matrix_extractor_v2.py file.pdf --out extracted --force-ocr --dpi 300
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

try:
    import pytesseract
except ImportError:
    pytesseract = None


class ParseError(Exception):
    pass


# ----------------------------------------------------------------------
# Constants that are genuinely fixed by the form layout (not by which
# course/category set a particular PDF uses).
# ----------------------------------------------------------------------
ALLOC_LABELS = {"State Level", "HU", "OHU"}
NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

INSTITUTE_RE = re.compile(r"^\s*(?P<code>\d{3,6})\s*-\s*(?P<name>.+?)\s*$")
# Colon made optional throughout: OCR frequently drops punctuation
# (e.g. "CAP Seats 60" instead of "CAP Seats: 60").
CAP_SEATS_RE = re.compile(r"CAP Seats\s*:?\s*(?P<n>\d+)", re.IGNORECASE)
PWD_COMMON_RE = re.compile(r"PWD Common Reserved Seats\s*:?\s*(?P<n>\d+)", re.IGNORECASE)
DEF_COMMON_RE = re.compile(r"DEF Common Reserved Seats\s*:?\s*(?P<n>\d+)", re.IGNORECASE)
EWS_RE = re.compile(r"Economically Weaker Section \(EWS\) Seats\s*:?\s*(?P<n>\d+)", re.IGNORECASE)
TFWS_RE = re.compile(r"TFWS Choice Code\s*:?\s*(?P<code>\S+)\s+Seats\s*:?\s*(?P<n>\d+)", re.IGNORECASE)

SKIP_PREFIXES = (
    "Admission to",
    "F:Only For Female",
    "STATE CET CELL",
    "Page ",
    "Provisional Seat Distribution",
    "Final Seat Distribution",
    "GOVERNMENT OF MAHARASHTRA",
    "STATE COMMON ENTRANCE TEST CELL",
    "Published on",
    "IMPORTANT NOTE",
    "All the Institutes are hereby informed",
)

# Minimum characters of native text below which we treat a page as
# "no usable text layer" and go straight to OCR (if enabled).
MIN_NATIVE_TEXT_LEN = 40


def slugify(label: str) -> str:
    s = label.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "field"


def row_cells(row: List[Optional[str]]) -> List[str]:
    """Ordered, non-empty, whitespace-collapsed cell values from a table row."""
    out = []
    for c in row:
        if c is None:
            continue
        c = re.sub(r"\s+", " ", c).strip()
        if c:
            out.append(c)
    return out


def take_numbers(cells: List[str], count: int, context: str) -> List[float]:
    nums = [c for c in cells if NUM_RE.match(c)]
    if len(nums) != count:
        raise ParseError(f"{context}: expected {count} numbers, got {len(nums)} in {cells!r}")
    return [int(v) if v.lstrip("-").isdigit() else float(v) for v in nums]


# ----------------------------------------------------------------------
# Institute / university header (plain text, above the table)
# ----------------------------------------------------------------------
def get_institute_header(
    text: str, carried_university: str, carried_code: str, carried_name: str
) -> Tuple[str, str, str]:
    """(university, institute_code, institute_name); falls back to carried
    values on continuation pages that omit the header."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not any(ln.startswith(p) for p in SKIP_PREFIXES)]

    university = carried_university
    institute_code = carried_code
    institute_name = carried_name
    university_candidate = None

    for ln in lines:
        m = INSTITUTE_RE.match(ln)
        if m:
            institute_code = m.group("code").zfill(5)
            institute_name = m.group("name").strip()
            if university_candidate is not None:
                university = university_candidate
            break
        if "CAP Seats" in ln or ln == "Orphan":
            break
        university_candidate = ln

    return university, institute_code, institute_name


# ----------------------------------------------------------------------
# Table-based parsing (used whenever pdfplumber's table extraction finds
# a table with a "Choice Code" header row -- true for both the clean
# native-text pages and most scanned-with-visible-gridlines pages).
# ----------------------------------------------------------------------
def find_course_header_field_names(header_row: List[Optional[str]]) -> List[str]:
    """Derive numeric field names for a course row from its own header
    row, so a different column set in another PDF still works.
    Expected header shape: 'Choice Code', 'Course Name', <field>, <field>, ...
    """
    cells = row_cells(header_row)
    try:
        cc_idx = cells.index("Choice Code")
        cn_idx = cells.index("Course Name")
    except ValueError:
        # Fallback to the known BBA_SM.pdf field order if the header text
        # itself doesn't parse as expected.
        return ["si", "ms_seats", "all_india", "minority_seats",
                "institute_seats", "orphan_in", "orphan_nin"]
    field_labels = cells[max(cc_idx, cn_idx) + 1:]
    names, seen = [], {}
    for lbl in field_labels:
        base = slugify(lbl)
        n = seen.get(base, 0)
        seen[base] = n + 1
        names.append(base if n == 0 else f"{base}_{n+1}")
    return names


def find_categories(category_row: List[Optional[str]]) -> List[str]:
    """Read the reservation-category list from a table's own 'Category'
    row instead of hard-coding it, so a PDF with a different category
    set still works. Expected shape: 'Category', <cat1>, <cat2>, ..., 'Total'."""
    cells = row_cells(category_row)
    if not cells or cells[0] != "Category":
        raise ParseError(f"expected 'Category' row, got {cells!r}")
    body = cells[1:]
    if body and body[-1] == "Total":
        body = body[:-1]
    if not body:
        raise ParseError(f"no categories found in row {cells!r}")
    return body


def parse_course_block(
    pnum: int,
    table: List[List[Optional[str]]],
    start: int,
    institution_code: str,
    institution_name: str,
    university: str,
    affiliation_status: str,
    cap_seats: int,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], int]:
    """Parse one course block (course row + its category table) starting
    at `start` (the header row index). Returns (course, long_rows, next_index)."""
    header_row = table[start]
    field_names = find_course_header_field_names(header_row)

    i = start + 1
    if i >= len(table):
        raise ParseError(f"page {pnum}: no course row after header")
    course_row = table[i]
    if not course_row or course_row[0] is None:
        raise ParseError(f"page {pnum}: malformed course row {course_row!r}")
    choice_code = re.sub(r"\s+", " ", course_row[0]).strip()
    course_name = re.sub(r"\s+", " ", (course_row[1] or "")).strip()
    rest_cells = row_cells(course_row[2:])
    vals = take_numbers(rest_cells, len(field_names), f"page {pnum} course numbers")
    i += 1

    course: Dict[str, Any] = {
        "institution_code": institution_code,
        "institution_name": institution_name,
        "university": university,
        "affiliation_status": affiliation_status,
        "cap_seats": cap_seats,
        "choice_code": choice_code,
        "course_name": course_name,
        **dict(zip(field_names, vals)),
        "pwd_common_reserved": None,
        "def_common_reserved": None,
        "ews_seats": None,
        "tfws_choice_code": "",
        "tfws_seats": None,
        "page": pnum,
        "record_type": "full",
    }

    if i >= len(table) or not table[i] or table[i][0] != "Category":
        raise ParseError(f"page {pnum}: expected 'Category' row, got {table[i] if i < len(table) else 'EOF'!r}")
    categories = find_categories(table[i])
    n_cats = len(categories)
    common_row_nums = n_cats + 1
    i += 1

    # Some seat-matrix PDFs (e.g. this PG/MBA one) don't split allocation
    # rows by gender at all -- no "General / Ladies" legend row, and each
    # allocation row is just one number per category + a total. Detect
    # which layout this table uses instead of assuming the G/L split.
    has_gender_split = bool(i < len(table) and table[i] and table[i][0] == "General / Ladies")
    if has_gender_split:
        i += 1  # G/L legend row
    alloc_row_nums = (n_cats * 2 + 1) if has_gender_split else (n_cats + 1)

    long_rows: List[Dict[str, Any]] = []
    while i < len(table):
        row = table[i]
        label = row[0].strip() if row and row[0] else ""
        cells = row_cells(row[1:]) if row else []

        if label in ALLOC_LABELS:
            vals = take_numbers(cells, alloc_row_nums, f"page {pnum} alloc row {label!r}")
            total = vals[-1]
            if has_gender_split:
                for idx, cat in enumerate(categories):
                    g, l = vals[idx * 2], vals[idx * 2 + 1]
                    for gender, seats in (("G", g), ("L", l)):
                        long_rows.append({
                            "institution_code": institution_code, "choice_code": choice_code,
                            "page": pnum, "allocation_type": label, "category": cat,
                            "gender": gender, "seats": seats,
                        })
            else:
                for cat, seats in zip(categories, vals[:-1]):
                    long_rows.append({
                        "institution_code": institution_code, "choice_code": choice_code,
                        "page": pnum, "allocation_type": label, "category": cat,
                        "gender": "", "seats": seats,
                    })
            long_rows.append({
                "institution_code": institution_code, "choice_code": choice_code,
                "page": pnum, "allocation_type": label, "category": "Total",
                "gender": "", "seats": total,
            })
            i += 1

        elif label in ("PWD", "DEF"):
            vals = take_numbers(cells, common_row_nums, f"page {pnum} {label} row")
            total = vals[-1]
            for cat, seats in zip(categories, vals[:-1]):
                long_rows.append({
                    "institution_code": institution_code, "choice_code": choice_code,
                    "page": pnum, "allocation_type": label, "category": cat,
                    "gender": "", "seats": seats,
                })
            long_rows.append({
                "institution_code": institution_code, "choice_code": choice_code,
                "page": pnum, "allocation_type": label, "category": "Total",
                "gender": "", "seats": total,
            })
            i += 1
            common_re = PWD_COMMON_RE if label == "PWD" else DEF_COMMON_RE
            key = "pwd_common_reserved" if label == "PWD" else "def_common_reserved"
            search_cells = cells + (row_cells(table[i]) if i < len(table) else [])
            for c in search_cells:
                m = common_re.search(c)
                if m:
                    course[key] = int(m.group("n"))
                    break
            if i < len(table) and any(common_re.search(c) for c in row_cells(table[i])):
                i += 1

        elif label == "Choice Code":
            # Next course block starts here (same table, another course).
            break

        else:
            all_cells = row_cells(row) if row else []
            ews_m = tfws_m = None
            for c in all_cells:
                if ews_m is None:
                    ews_m = EWS_RE.search(c)
                if tfws_m is None:
                    tfws_m = TFWS_RE.search(c)
            if ews_m:
                course["ews_seats"] = int(ews_m.group("n"))
            if tfws_m:
                course["tfws_choice_code"] = tfws_m.group("code")
                course["tfws_seats"] = int(tfws_m.group("n"))
            i += 1
            if not (ews_m or tfws_m):
                break

    return course, long_rows, i


def parse_page_tables(
    pnum: int,
    tables: List[List[List[Optional[str]]]],
    institution_code: str,
    institution_name: str,
    university: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse every course block in every table on a page (handles pages
    with multiple tables and/or multiple course rows per table)."""
    all_courses: List[Dict[str, Any]] = []
    all_long: List[Dict[str, Any]] = []

    for table in tables:
        header_idxs = [i for i, r in enumerate(table) if r and r[0] == "Choice Code"]
        if not header_idxs:
            continue
        for header_idx in header_idxs:
            if header_idx < 1:
                raise ParseError(f"page {pnum}: no affiliation/CAP-seats row before header")
            aff_row = row_cells(table[header_idx - 1])
            cap_m, affiliation_status = None, ""
            for c in aff_row:
                m = CAP_SEATS_RE.search(c)
                if m:
                    cap_m = m
                elif c != "Orphan":
                    affiliation_status = c
            if not cap_m:
                raise ParseError(f"page {pnum}: no 'CAP Seats:' found in {aff_row!r}")
            cap_seats = int(cap_m.group("n"))

            course, long_rows, _next_i = parse_course_block(
                pnum, table, header_idx, institution_code, institution_name,
                university, affiliation_status, cap_seats,
            )
            if course["ews_seats"] is None:
                raise ParseError(f"page {pnum}: never found EWS seats line")
            all_courses.append(course)
            all_long.extend(long_rows)

    return all_courses, all_long


# ----------------------------------------------------------------------
# OCR fallback for pages with no usable text layer / no parseable table
# (scanned pages, or a garbled text extraction in some other PDF).
# ----------------------------------------------------------------------
def ocr_page_text(page: "pdfplumber.page.Page", dpi: int, lang: str) -> str:
    if pytesseract is None:
        return ""
    try:
        im = page.to_image(resolution=dpi).original
        return pytesseract.image_to_string(im, lang=lang)
    except Exception:
        return ""


def fallback_parse_from_text(
    pnum: int, text: str, institution_code: str, institution_name: str, university: str
) -> Optional[Dict[str, Any]]:
    """Best-effort metadata-only extraction when the page has no
    parseable seat table (e.g. OCR text with no reliable column
    alignment). Captures whatever header info is findable instead of
    dropping the page entirely."""
    if not text or not text.strip():
        return None
    cap_m = CAP_SEATS_RE.search(text)
    if not (INSTITUTE_RE.search(text) or cap_m):
        return None  # doesn't look like a seat-matrix page at all
    return {
        "institution_code": institution_code,
        "institution_name": institution_name,
        "university": university,
        "affiliation_status": "",
        "cap_seats": int(cap_m.group("n")) if cap_m else None,
        "choice_code": "",
        "course_name": "",
        "pwd_common_reserved": None,
        "def_common_reserved": None,
        "ews_seats": None,
        "tfws_choice_code": "",
        "tfws_seats": None,
        "page": pnum,
        "record_type": "partial_ocr",
    }


# ----------------------------------------------------------------------
# Per-PDF / batch driver
# ----------------------------------------------------------------------
BASE_COURSE_FIELDS = [
    "source_pdf", "page", "university", "institution_code", "institution_name",
    "affiliation_status", "cap_seats", "choice_code", "course_name",
]
TRAILING_COURSE_FIELDS = [
    "pwd_common_reserved", "def_common_reserved",
    "ews_seats", "tfws_choice_code", "tfws_seats", "record_type",
]
LONG_FIELDS = [
    "source_pdf", "page", "institution_code", "choice_code",
    "allocation_type", "category", "gender", "seats",
]


def process_single_pdf(
    pdf_path: Path, outdir: Path, dpi: int, lang: str, use_ocr: bool, force_ocr: bool
) -> Dict[str, Any]:
    all_courses: List[Dict[str, Any]] = []
    all_long: List[Dict[str, Any]] = []
    page_audit: List[Dict[str, Any]] = []
    errors = 0
    skipped_nondata = 0
    ocr_pages = 0

    university = ""
    institution_code = ""
    institution_name = ""

    with pdfplumber.open(pdf_path) as pdf:
        n_pages = len(pdf.pages)
        print(f"  pages: {n_pages}")
        for pnum in range(1, n_pages + 1):
            page = pdf.pages[pnum - 1]
            text = page.extract_text() or ""
            university, institution_code, institution_name = get_institute_header(
                text, university, institution_code, institution_name
            )

            tables = [] if force_ocr else (page.extract_tables() or [])
            has_choice_code_table = any(
                any(r and r[0] == "Choice Code" for r in t) for t in tables
            )
            needs_ocr = force_ocr or (not has_choice_code_table and len(text) < MIN_NATIVE_TEXT_LEN)

            method = "native"
            if needs_ocr and use_ocr and pytesseract is not None:
                ocr_text = ocr_page_text(page, dpi, lang)
                if ocr_text.strip():
                    method = "ocr"
                    ocr_pages += 1
                    # Re-derive header from OCR text too, in case native text was empty.
                    university, institution_code, institution_name = get_institute_header(
                        ocr_text, university, institution_code, institution_name
                    )
                    if not has_choice_code_table:
                        text = ocr_text  # used by the metadata-only fallback below

            try:
                courses, long_rows = ([], [])
                if has_choice_code_table:
                    courses, long_rows = parse_page_tables(
                        pnum, tables, institution_code, institution_name, university
                    )
                if not courses:
                    partial = fallback_parse_from_text(
                        pnum, text, institution_code, institution_name, university
                    )
                    if partial is not None:
                        courses = [partial]
            except ParseError as e:
                errors += 1
                print(f"    p{pnum:>3}: SKIPPED - {e}")
                page_audit.append({"page": pnum, "courses": 0, "seat_rows": 0,
                                    "error": str(e), "method": method})
                continue

            if not courses:
                page_audit.append({"page": pnum, "courses": 0, "seat_rows": 0,
                                    "note": "non-data page", "method": method})
                skipped_nondata += 1
                continue

            for c in courses:
                c["source_pdf"] = pdf_path.name
            all_courses.extend(courses)
            for r in long_rows:
                r["source_pdf"] = pdf_path.name
            all_long.extend(long_rows)
            page_audit.append({"page": pnum, "courses": len(courses),
                                "seat_rows": len(long_rows), "method": method})

    # Build the course CSV field order dynamically: base fields, then
    # whatever numeric field names actually showed up (union, in first-
    # seen order), then the trailing fields.
    seen_dynamic: List[str] = []
    known = set(BASE_COURSE_FIELDS) | set(TRAILING_COURSE_FIELDS) | {"record_type"}
    for c in all_courses:
        for k in c.keys():
            if k not in known and k not in seen_dynamic:
                seen_dynamic.append(k)
    course_fields = BASE_COURSE_FIELDS + seen_dynamic + TRAILING_COURSE_FIELDS

    stem = pdf_path.stem
    outdir.mkdir(parents=True, exist_ok=True)

    with open(outdir / f"{stem}_courses.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=course_fields)
        w.writeheader()
        for c in all_courses:
            w.writerow({k: c.get(k, "") for k in course_fields})
    (outdir / f"{stem}_courses.json").write_text(
        json.dumps(all_courses, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    with open(outdir / f"{stem}_seats_long.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LONG_FIELDS)
        w.writeheader()
        for r in all_long:
            w.writerow({k: r.get(k, "") for k in LONG_FIELDS})
    (outdir / f"{stem}_seats_long.json").write_text(
        json.dumps(all_long, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    (outdir / f"{stem}_page_audit.json").write_text(
        json.dumps(page_audit, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {
        "pdf": pdf_path.name,
        "pages": n_pages,
        "courses_extracted": len(all_courses),
        "seat_rows_extracted": len(all_long),
        "pages_with_errors": errors,
        "pages_non_data": skipped_nondata,
        "pages_used_ocr": ocr_pages,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract CET Seat Matrix PDFs (pdfplumber-based, generalized)")
    ap.add_argument("path", help="PDF file or folder of PDFs")
    ap.add_argument("--out", default="extracted", help="Output directory")
    ap.add_argument("--dpi", type=int, default=300, help="OCR render DPI")
    ap.add_argument("--lang", default="eng", help="Tesseract language")
    ap.add_argument("--no-ocr", action="store_true", help="Never OCR; leave unreadable pages as-is")
    ap.add_argument("--force-ocr", action="store_true", help="OCR every page, ignoring the native text layer")
    args = ap.parse_args()

    path = Path(args.path)
    outdir = Path(args.out)
    pdfs = sorted(path.glob("*.pdf")) if path.is_dir() else [path]
    if not pdfs:
        print(f"No PDFs found at {path}", file=sys.stderr)
        return 1

    summaries = []
    for pdf_path in pdfs:
        print(f"Processing {pdf_path.name} ...")
        summaries.append(process_single_pdf(
            pdf_path, outdir, dpi=args.dpi, lang=args.lang,
            use_ocr=not args.no_ocr, force_ocr=args.force_ocr,
        ))

    print("\n=== Summary ===")
    for s in summaries:
        print(json.dumps(s, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
