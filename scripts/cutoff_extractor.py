#!/usr/bin/env python3
"""
Universal CET Batch Extractor — generic version (no hardcoded category lists).

Key changes from the previous version:
  1. CATEGORY_RE (a hand-maintained whitelist of ~90 codes) is GONE.
     Category codes are now detected structurally: a line (or a line of
     whitespace-separated tokens) consisting entirely of uppercase
     letters/digits/hyphens, with no lowercase text and no spaces inside
     a token, is treated as a category-code line. This works for any
     course type (Engineering, MBA, MCA, Pharmacy, ...) without editing
     the script, because every one of these DTE/CET cutoff PDFs prints
     category codes the same structural way (short ALL-CAPS tokens,
     alone on their own line, directly above the numeric cutoff table)
     even though the actual codes differ per exam.
  2. Stage/round markers are detected two ways: the old inline
     "Stage-I" / "Stage-IV" text, AND a bare roman numeral alone on its
     own line ("  I", "II", ...) which is how some PDFs (e.g. the 2023
     Engineering CAP round-I list) print the round instead. A lone
     "Stage" with no numeral (a stray column-header artifact) is
     ignored rather than misread as a category.
  3. Extraction technique (native text vs OCR) is now chosen ONCE per
     PDF from a small sample of pages, then used for every page in that
     PDF. No more per-page "try native, fall back to OCR" switching.
  4. Everything else (metadata regexes, section detection, rank/percent
     merging, CSV/JSON output, batch driving) is unchanged from the
     previous version.

Fixes applied in this version:
  - institution_code is padded with zfill(5). Some source PDFs render
    "1102" instead of "01102"; all DTE/CET institution codes are exactly
    5 digits, so zfill(5) normalizes them without touching codes that
    are already correct.
  - Letter-suffixed ranks (e.g. "24478A (99.99)", used by some PDFs —
    especially Round II/III — to mark provisional/re-allotted
    candidates) are now recognized. Previously RANK_VALUE_RE required a
    purely numeric rank immediately before "(", so a suffixed rank
    matched nothing at all: the value silently vanished, AND every
    later rank/category on that same row shifted left by one slot and
    got mismatched. RANK_VALUE_RE now accepts one optional trailing
    letter on the rank; merge_split_rank_lines' line-continuation check
    was widened the same way, for the case where the rank and its
    "(percentage)" land on two separate PDF lines.
    The numeric part and the letter are kept as separate fields
    (rank_number, rank_suffix) alongside the full token (rank), so a
    suffixed rank like "24478A" can never be silently merged/deduped
    against a plain rank "24478" that happens to share the same digits.
    This only *adds* matches that previously failed outright — a plain
    numeric rank still matches exactly as before — so it cannot change
    any row that was already being extracted correctly.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz

try:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
except ImportError:
    pytesseract = None
    Image = None

# ----------------------------------------------------------------------
# Auto-locate Tesseract
# ----------------------------------------------------------------------
tesseract_path = shutil.which("tesseract")
if tesseract_path and pytesseract is not None:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
elif pytesseract is not None:
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
SECTION_NAMES = [
    "Home University Seats Allotted to Home University Candidates",
    "Home University Seats Allotted to Other Than Home University Candidates",
    "Other Than Home University Seats Allotted to Other Than Home University Candidates",
    "Other Than Home University Seats Allotted to Home University Candidates",
    "State Level",
    "Minority Seats : Minority Seats Allotted to Minority Candidates",
    "Minority Seats : Minority Seats Allotted to Non Minority Candidates",
]

RANK_VALUE_RE = re.compile(
    r"(?P<rank>\d{1,6})(?P<suffix>[A-Za-z])?\s*\((?P<percentage>\d+(?:\.\d+)?)\)"
)
STAGE_RE = re.compile(r"Stage[-\s]*[IVXLCDM]+", re.IGNORECASE)

INSTITUTE_RE = re.compile(r"^\s*(?P<code>\d{3,5})\s*-\s*(?P<name>.+?)\s*$")
PROGRAM_RE = re.compile(r"^\s*(?P<code>\d{7,12}F?)\s*-\s*(?P<name>.+?)\s*$", re.IGNORECASE)
STATUS_RE = re.compile(r"^\s*Status\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
UNIVERSITY_RE = re.compile(r"^\s*Home University\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE)

RANK_ONLY_RE = re.compile(r"^\d{1,6}$")
PERCENT_ONLY_RE = re.compile(r"^\(\s*\d+(?:\.\d+)?\s*\)$")

# ----------------------------------------------------------------------
# Generic category-code + round/stage-marker detection
# ----------------------------------------------------------------------
CATEGORY_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9-]{1,14}$")

BARE_STAGE_TOKENS = {
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII",
}
IGNORED_BARE_WORDS = {"STAGE"}


def is_category_token(tok: str) -> bool:
    tok = tok.strip()
    if not tok or tok in BARE_STAGE_TOKENS or tok in IGNORED_BARE_WORDS:
        return False
    if tok.isdigit():
        return False
    return bool(CATEGORY_TOKEN_RE.match(tok))


def extract_category_tokens(line: str) -> List[str]:
    parts = line.strip().split()
    if not parts or not all(is_category_token(p) for p in parts):
        return []
    return [p.upper() for p in parts]


# ----------------------------------------------------------------------
# Text cleaning
# ----------------------------------------------------------------------
def clean_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_lines(text: str) -> List[str]:
    return [clean_text(line) for line in text.splitlines() if clean_text(line)]


def merge_split_rank_lines(lines: List[str]) -> List[str]:
    merged: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if i + 1 < n and PERCENT_ONLY_RE.match(lines[i + 1]):
            if RANK_ONLY_RE.match(line) or re.search(r"\d[A-Za-z]?$", line):
                merged.append(f"{line} {lines[i + 1]}")
                i += 2
                continue
        merged.append(line)
        i += 1
    return merged


# ----------------------------------------------------------------------
# Metadata
# ----------------------------------------------------------------------
def extract_metadata(lines: List[str]) -> Dict[str, str]:
    meta = {
        "institution_code": "",
        "institution_name": "",
        "program_code": "",
        "program_name": "",
        "status": "",
        "home_university": "",
    }
    for line in lines:
        if not meta["institution_code"]:
            m = INSTITUTE_RE.match(line)
            if m:
                # All DTE/CET institution codes are exactly 5 digits.
                # Some PDFs render "1102" instead of "01102", so pad to 5.
                # zfill(5) is a no-op on codes that already have 5 digits,
                # so this cannot corrupt any correctly-extracted row.
                meta["institution_code"] = m.group("code").zfill(5)
                meta["institution_name"] = m.group("name").strip()
                continue
        if not meta["program_code"]:
            m = PROGRAM_RE.match(line)
            if m:
                meta["program_code"] = m.group("code")
                meta["program_name"] = m.group("name").strip()
                continue
        if not meta["status"]:
            m = STATUS_RE.match(line)
            if m:
                meta["status"] = m.group("value").strip()
                continue
        if not meta["home_university"]:
            m = UNIVERSITY_RE.match(line)
            if m:
                meta["home_university"] = m.group("value").strip()
                continue
    return meta


# ----------------------------------------------------------------------
# Section detection
# ----------------------------------------------------------------------
def detect_section(line: str) -> Optional[str]:
    normalized = re.sub(r"\s+", " ", line).strip().lower()
    for sec in SECTION_NAMES:
        if normalized == sec.lower():
            return sec
    if "home university seats allotted to home university candidates" in normalized:
        return SECTION_NAMES[0]
    if "home university seats allotted to other than home university candidates" in normalized:
        return SECTION_NAMES[1]
    if "other than home university seats allotted to other than home university candidates" in normalized:
        return SECTION_NAMES[2]
    if "other than home university seats allotted to home university candidates" in normalized:
        return SECTION_NAMES[3]
    if normalized.startswith("state level"):
        return SECTION_NAMES[4]
    if ("minority seats" in normalized
            and "minority candidates" in normalized
            and "non minority" not in normalized):
        return SECTION_NAMES[5]
    if "minority seats" in normalized and "non minority" in normalized:
        return SECTION_NAMES[6]
    return None


# ----------------------------------------------------------------------
# Stage splitting (inline "Stage-I ... values ..." on one line)
# ----------------------------------------------------------------------
def split_by_stage(line: str) -> List[Tuple[str, str]]:
    parts: List[Tuple[str, str]] = []
    matches = list(STAGE_RE.finditer(line))
    if not matches:
        return [("", line)]
    if matches[0].start() > 0:
        parts.append(("", line[: matches[0].start()].strip()))
    for i, m in enumerate(matches):
        end = m.end()
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(line)
        parts.append((m.group(0), line[end:next_start].strip()))
    return parts


# ----------------------------------------------------------------------
# Structured page parser
# ----------------------------------------------------------------------
def parse_structured_page(
    page_number: int,
    lines: List[str],
    source: str,
    metadata: Dict[str, str],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    current_section: Optional[str] = None
    pending_categories: List[str] = []
    cat_cursor = 0
    categories_consumed = False
    current_stage = ""

    for line in lines:
        sec = detect_section(line)
        if sec:
            current_section = sec
            pending_categories = []
            cat_cursor = 0
            categories_consumed = False
            current_stage = ""
            continue

        bare = line.strip().upper()
        if bare in BARE_STAGE_TOKENS:
            current_stage = f"Stage-{bare}"
            cat_cursor = 0
            continue
        if bare in IGNORED_BARE_WORDS:
            continue

        cats_in_line = extract_category_tokens(line)
        if cats_in_line:
            if categories_consumed:
                pending_categories = cats_in_line.copy()
                categories_consumed = False
            else:
                pending_categories.extend(cats_in_line)
            cat_cursor = 0
            continue

        for stage, segment in split_by_stage(line):
            if stage:
                current_stage = stage
                cat_cursor = 0
            if not segment:
                continue
            values = [
                {
                    "rank": m.group("rank") + (m.group("suffix") or "").upper(),
                    "rank_number": int(m.group("rank")),
                    "rank_suffix": (m.group("suffix") or "").upper(),
                    "percentage": float(m.group("percentage")),
                    "percentage_raw": m.group("percentage"),
                    "raw": m.group(0),
                }
                for m in RANK_VALUE_RE.finditer(segment)
            ]
            if not values:
                continue
            for val in values:
                cat = (
                    pending_categories[cat_cursor]
                    if cat_cursor < len(pending_categories)
                    else ""
                )
                cat_cursor += 1
                categories_consumed = True
                records.append({
                    **metadata,
                    "page": page_number,
                    "section": current_section,
                    "stage": current_stage,
                    "category": cat,
                    "rank": val["rank"],
                    "rank_number": val["rank_number"],
                    "rank_suffix": val["rank_suffix"],
                    "percentage": val["percentage"],
                    "percentage_raw": val["percentage_raw"],
                    "raw_value": val["raw"],
                    "source": source,
                })
    return records


# ----------------------------------------------------------------------
# OCR helpers
# ----------------------------------------------------------------------
def render_page(page: fitz.Page, dpi: int = 300) -> Any:
    if Image is None:
        raise RuntimeError("Pillow not installed. Run: pip install pillow")
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def preprocess_image(img: "Image.Image") -> "Image.Image":
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray)
    gray = gray.filter(ImageFilter.SHARPEN)
    gray = ImageEnhance.Contrast(gray).enhance(1.5)
    return gray


def ocr_page(page: fitz.Page, dpi: int, lang: str = "eng") -> str:
    if pytesseract is None:
        raise RuntimeError("pytesseract not installed. Run: pip install pytesseract")
    img = render_page(page, dpi)
    processed = preprocess_image(img)
    return pytesseract.image_to_string(processed, lang=lang, config="--psm 6")


# ----------------------------------------------------------------------
# Camelot (opt-in)
# ----------------------------------------------------------------------
def run_camelot_on_page(pdf_path: Path, page_num: int) -> List[Dict]:
    try:
        import camelot
    except ImportError:
        return []
    tables = []
    for flavor in ("lattice", "stream"):
        try:
            tbls = camelot.read_pdf(str(pdf_path), pages=str(page_num), flavor=flavor)
            for t in tbls:
                acc = getattr(t, "parsing_report", {}).get("accuracy", 0)
                tables.append({
                    "flavor": flavor,
                    "page": page_num,
                    "accuracy": acc,
                    "data": t.df.values.tolist(),
                })
        except Exception:
            continue
    return tables


def camelot_to_records(tables: List[Dict], metadata: Dict, page_num: int) -> List[Dict]:
    records = []
    for tbl in tables:
        df = tbl["data"]
        if len(df) < 2:
            continue
        header = [str(c).strip() for c in df[0]]
        for row in df[1:]:
            for idx, cell in enumerate(row):
                cell = str(cell).strip()
                for m in RANK_VALUE_RE.finditer(cell):
                    cat = header[idx] if idx < len(header) else ""
                    records.append({
                        **metadata,
                        "page": page_num,
                        "section": None,
                        "stage": "",
                        "category": cat,
                        "rank": m.group("rank") + (m.group("suffix") or "").upper(),
                        "rank_number": int(m.group("rank")),
                        "rank_suffix": (m.group("suffix") or "").upper(),
                        "percentage": float(m.group("percentage")),
                        "percentage_raw": m.group("percentage"),
                        "raw_value": m.group(0),
                        "source": f"camelot_{tbl['flavor']}",
                    })
    return records


# ----------------------------------------------------------------------
# Extraction-technique auto-detection (once per PDF, not per page)
# ----------------------------------------------------------------------
def _count_value_hits(lines: List[str]) -> int:
    return sum(len(RANK_VALUE_RE.findall(line)) for line in lines)


def choose_extraction_technique(
    doc: "fitz.Document", dpi: int, lang: str, sample_size: int = 5
) -> Tuple[str, Dict[str, int]]:
    n = len(doc)
    if n <= sample_size:
        sample_idx = list(range(n))
    else:
        step = n / sample_size
        sample_idx = sorted({int(i * step) for i in range(sample_size)})

    native_hits = 0
    for i in sample_idx:
        text = doc[i].get_text("text") or ""
        lines = merge_split_rank_lines(normalize_lines(text))
        native_hits += _count_value_hits(lines)

    scores = {"native": native_hits, "ocr": -1}

    if native_hits >= len(sample_idx):
        return "native", scores

    if pytesseract is not None:
        ocr_hits = 0
        for i in sample_idx:
            try:
                text = ocr_page(doc[i], dpi, lang)
            except Exception:
                continue
            lines = merge_split_rank_lines(normalize_lines(text))
            ocr_hits += _count_value_hits(lines)
        scores["ocr"] = ocr_hits
        if ocr_hits > native_hits:
            return "ocr", scores

    return ("native" if native_hits > 0 else "ocr"), scores


# ----------------------------------------------------------------------
# Merging / deduplication
# ----------------------------------------------------------------------
SOURCE_PRIORITY = {
    "native_text": 0,
    "ocr": 2,
    "camelot_lattice": 3,
    "camelot_stream": 4,
}


def get_priority(source: str) -> int:
    return SOURCE_PRIORITY.get(source, 99)


def merge_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    records_sorted = sorted(records, key=lambda r: get_priority(r.get("source", "")))
    merged = records_sorted[0].copy()
    for rec in records_sorted[1:]:
        for key, value in rec.items():
            if key in ("source", "merged_from"):
                continue
            if not merged.get(key) and value:
                merged[key] = value
    sources = sorted({r.get("source", "") for r in records}, key=get_priority)
    merged["source"] = sources[0] if sources else ""
    merged["merged_from"] = sources
    return merged


def deduplicate_and_merge(all_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups = defaultdict(list)
    for rec in all_records:
        key = (
            rec.get("source_pdf", ""),
            rec.get("page"),
            rec.get("section", ""),
            rec.get("category", ""),
            rec.get("rank"),
        )
        groups[key].append(rec)

    merged: List[Dict[str, Any]] = []
    for recs in groups.values():
        if len(recs) == 1:
            merged.append(recs[0])
        else:
            merged.append(merge_records(recs))
    return merged


# ----------------------------------------------------------------------
# Per-PDF processing
# ----------------------------------------------------------------------
def process_single_pdf(
    pdf_path: Path,
    outdir: Path,
    dpi: int,
    lang: str,
    use_camelot: bool,
) -> Dict[str, Any]:
    doc = fitz.open(pdf_path)
    print(f"  pages: {len(doc)}")

    technique, scores = choose_extraction_technique(doc, dpi, lang)
    print(f"  extraction technique (auto-detected): {technique}  "
          f"(sample hits: native={scores['native']}, ocr={scores['ocr']})")

    all_records_raw: List[Dict[str, Any]] = []
    page_audits: List[Dict[str, Any]] = []
    camelot_all_tables: List[Dict] = []
    source_label = "native_text" if technique == "native" else "ocr"

    for pnum in range(1, len(doc) + 1):
        page = doc[pnum - 1]

        if technique == "native":
            text = page.get_text("text") or ""
        else:
            try:
                text = ocr_page(page, dpi, lang)
            except Exception:
                text = ""

        lines = merge_split_rank_lines(normalize_lines(text))
        metadata = extract_metadata(lines)
        records = parse_structured_page(pnum, lines, source_label, metadata)
        for r in records:
            r["source_pdf"] = pdf_path.name

        records_camelot: List[Dict[str, Any]] = []
        if use_camelot:
            tables = run_camelot_on_page(pdf_path, pnum)
            if tables:
                camelot_all_tables.extend(tables)
                records_camelot = camelot_to_records(tables, metadata, pnum)
                for r in records_camelot:
                    r["source_pdf"] = pdf_path.name

        all_records_raw.extend(records + records_camelot)
        page_audits.append({
            "page": pnum,
            f"{source_label}_records": len(records),
            "camelot_records": len(records_camelot),
        })
        print(f"    p{pnum:>3}: {source_label}={len(records):>3} "
              f"camelot={len(records_camelot):>3}")

    merged_records = deduplicate_and_merge(all_records_raw)

    suffixed = [r for r in merged_records if r.get("rank_suffix")]
    if suffixed:
        print(f"    letter-suffixed ranks recovered: {len(suffixed)} "
              f"(e.g. {suffixed[0]['rank']!r})")

    # Safety net: flag any institution_code that isn't exactly 5 digits.
    bad = [r for r in merged_records
           if r.get("institution_code")
           and not re.fullmatch(r"\d{5}", str(r["institution_code"]))]
    if bad:
        print(f"    WARNING: {len(bad)} rows have non-5-digit institution_code "
              f"(e.g. {bad[0]['institution_code']!r})")

    base_stem = pdf_path.stem
    outdir.mkdir(parents=True, exist_ok=True)

    csv_path = outdir / f"{base_stem}_cutoffs.csv"
    json_path = outdir / f"{base_stem}_cutoffs.json"
    audit_path = outdir / f"{base_stem}_page_audit.json"

    write_csv(csv_path, merged_records)
    write_json(json_path, merged_records)
    write_json(audit_path, page_audits)

    doc.close()
    print(f"    -> {len(merged_records)} unique rows -> {csv_path.name}")
    return {
        "pdf": str(pdf_path),
        "pages": len(page_audits),
        "technique": technique,
        "raw_records": len(all_records_raw),
        "merged_records": len(merged_records),
        "csv": str(csv_path),
    }


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------
CSV_FIELDS = [
    "source_pdf", "page", "institution_code", "institution_name",
    "program_code", "program_name", "status", "home_university",
    "section", "stage", "category", "rank", "rank_number", "rank_suffix",
    "percentage", "percentage_raw", "raw_value", "source", "merged_from",
]


def write_csv(path: Path, records: List[Dict[str, Any]]) -> None:
    if not records:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for rec in records:
            row = {k: rec.get(k, "") for k in CSV_FIELDS}
            if isinstance(row["merged_from"], list):
                row["merged_from"] = ", ".join(row["merged_from"])
            w.writerow(row)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def combine_csvs(outdir: Path) -> Optional[Path]:
    combined_path = outdir / "ALL_PDFS_cutoffs.csv"
    per_pdf = sorted(p for p in outdir.glob("*_cutoffs.csv") if p != combined_path)
    if not per_pdf:
        return None
    with combined_path.open("w", newline="", encoding="utf-8-sig") as fout:
        writer = csv.writer(fout)
        writer.writerow(CSV_FIELDS)
        for path in per_pdf:
            with path.open("r", encoding="utf-8-sig") as fin:
                rows = list(csv.reader(fin))
            if not rows:
                continue
            writer.writerows(rows[1:])
    return combined_path


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Universal CET Batch Extractor (generic, no hardcoded category lists)"
    )
    parser.add_argument("input", type=Path,
                        help="Single PDF or folder of PDFs")
    parser.add_argument("--out", type=Path, default=Path("extracted"))
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--lang", default="eng")
    parser.add_argument("--camelot", action="store_true",
                        help="Enable Camelot (usually adds nothing on these PDFs)")
    args = parser.parse_args()

    if args.input.is_file() and args.input.suffix.lower() == ".pdf":
        pdf_files = [args.input]
    elif args.input.is_dir():
        pdf_files = sorted(args.input.glob("*.pdf"))
        if not pdf_files:
            print(f"No PDF files in {args.input}", file=sys.stderr)
            return 1
    else:
        print(f"Bad input: {args.input}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    total = len(pdf_files)
    print(f"Found {total} PDF(s).  Output -> {args.out}")
    summary: List[Dict[str, Any]] = []

    for idx, pdf_path in enumerate(pdf_files, 1):
        print(f"\n[{idx}/{total}] {pdf_path.name}")
        try:
            summary.append(process_single_pdf(
                pdf_path=pdf_path,
                outdir=args.out,
                dpi=args.dpi,
                lang=args.lang,
                use_camelot=args.camelot,
            ))
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)

    combined = combine_csvs(args.out)
    write_json(args.out / "batch_summary.json", summary)
    print(f"\nDone. {total} PDFs processed.")
    if combined:
        print(f"Combined CSV -> {combined}")
    print(f"Summary      -> {args.out / 'batch_summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())