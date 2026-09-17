# CET CAP Decision Support

Interactive web tool that lets State CET candidates explore
historical CAP cutoff data across BCA, BBA, MBA, and MCA
programs, and identify colleges they can realistically
get into.

## Status

Phase 1 (data validation) is **complete and passing** against the
real dataset: 30 CSVs, 67,770 rows, `Total issues: 0`. See
`docs/DECISIONS.md` for exactly what was found and fixed to get
there, and `VALIDATION_REPORT.txt` in this repo for the full raw
output of the final run.

Seat-matrix data (F5 — sanctioned intake capacity) is **also
integrated and passing**: 4 CSVs, 70,269 rows, `Total issues: 0`
(4 rows quarantined as known PDF-extraction artifacts). See
`docs/DECISIONS.md` → "2026-09-12 (continued) — Seat-matrix (F5)
integration" and `SEATS_VALIDATION_REPORT.txt` for the full run.
`ingest_seats.py` loads 69,655 of 70,269 rows cleanly (verified
against the actual `SELECT COUNT(*) FROM seats`, not just the
script's own printed total — see `docs/DECISIONS.md` "Second review
pass"); the remaining 610 are held out because 9 institutes exist in
the seat matrix with no matching record yet in the cutoffs-derived
`institutes` table (documented, not silently dropped). It's also
idempotent — running it again against the same database reports
`inserted=0 duplicate=69655`, not a growing table.

**`ingest.py` for the cutoffs source (Phase 2) is now built** and
loads 67,769 of 67,770 rows cleanly (verified against the actual
`SELECT COUNT(*) FROM cutoffs`) — the 1 remaining row is the single
documented extraction anomaly (`BCA_24_C2.csv`, category field
`"GOVERNMENT"`). It's idempotent, same as `ingest_seats.py`: re-running
reports `inserted=0 duplicate=67769`. It reuses `validate_data.py`'s
forward-fill and category/stage/section logic directly rather than
re-deriving it — see `docs/DECISIONS.md` "ingest.py: reusing
validate_data.py's forward-fill instead of re-deriving it" for why that
matters (a from-scratch rewrite of this exact script silently dropped
11.5% of rows by skipping that step).

`scripts/seed_reference_tables.py` now seeds `institutes`,
`base_categories`, `sections`, and `stages` — run it once before either
`ingest.py` or `ingest_seats.py`; skipping it fails almost every row
with `FOREIGN KEY constraint failed`.

**Phase 3 (candidate-facing portal):** `site/index.html` — a static
site with cascading Course → Branch → College → Category (+ Ladies
toggle) → Quota/Type filters and an interactive percentile-vs-year
trends chart, built against the real ingested cutoffs data via
pre-exported JSON (`site/data/cutoffs_*.json`, `site/data/manifest.json`).
`scripts/export_trends_json.py` regenerates those JSON files from
`db/cet_cap.db` whenever you re-ingest new data. See
`docs/README_TRENDS_WIDGET.md` for what's built vs. deferred and
`docs/cutoff-trends-graph-spec.md` for the original spec.

The percentile/city/course **eligibility search** widget above the
trends chart (originally a hardcoded demo list with fake fees/
placement data, carried over from a reference-site template) now
also runs on real data: `scripts/export_search_index.py` exports
`site/data/search_index.json` (1,433 real college+course records,
923 institutes) from `db/cet_cap.db`, and the page fetches it at
load instead of using the old hardcoded `CONFIG.colleges` array.
Fields the template had but the real data doesn't (fees, placement
stats, "about" text) are simply omitted rather than faked — see the
script's module docstring for exactly which cutoff row represents
"the" cutoff for a college+course (single-percentile search has no
category/quota selector, so a documented choice had to be made).

City search needs `scripts/enrich_institutes.py` run once — it
populates `institutes.city`/`.website` from a manually-compiled source
CSV (nothing in the CET CAP PDFs has city/website data). Coverage:
967/969 institutes (99.8%). See `docs/DECISIONS.md` "Institute
city/website enrichment" for how ambiguous/conflicting source rows are
handled (quarantined, not guessed).

## Quick start (dev)

```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Your 30 source CSVs are already in data/raw/.
# Re-run the validator any time you add more:
python scripts/validate_data.py data/raw/

# Seat-matrix (sanctioned intake capacity) CSVs are in data/raw/seats/.
python scripts/validate_seats.py data/raw/seats/

# One-time (per fresh DB) — seeds institutes/base_categories/sections/
# stages that both ingest scripts need via foreign key. Safe to re-run.
python scripts/seed_reference_tables.py --db db/cet_cap.db

python scripts/ingest.py data/raw/ --db db/cet_cap.db                 # cutoffs
python scripts/ingest_seats.py data/raw/seats/ --year 2026 --db db/cet_cap.db  # seats

# City/website enrichment — needed for the site's city search to work.
python scripts/enrich_institutes.py data/reference/institute_contacts.csv --db db/cet_cap.db

# Export the trends JSON the site reads from
python scripts/export_trends_json.py --db db/cet_cap.db --out site/data

# Export the real data behind the eligibility-search widget (Step 01/02
# at the top of the site — previously placeholder/demo colleges)
python scripts/export_search_index.py --db db/cet_cap.db --out site/data

# Serve the site — just open site/index.html directly (double-click,
# or drag it into a browser tab). It loads its data via <script src>
# tags, not fetch(), so it works straight off file:// with no server.
```

## Project structure
See `docs/ARCHITECTURE.md`.

## Contributing
See `docs/RULES.md` and `docs/AGENTS.md`.

## Docs
- docs/PRD.md
- docs/ARCHITECTURE.md
- docs/AGENTS.md
- docs/RULES.md
- docs/DECISIONS.md — **read this first**, it documents every fix
  made against the real data on 2026-09-12, cutoffs and seat matrix
  both
- docs/TESTING.md

### Runtime cutoff data strategy

The static site does **not** ship the master SQLite database. Cutoff data for college drawers is precomputed at build time into small per-college JavaScript payloads under `site/data/college_cutoffs/<COURSE>/<COLLEGE>.js`. The drawer loads only the selected college's payload. The source DB remains the authoritative build-time source, and the export is validated against the canonical cutoff rows before deployment.

To regenerate the per-college payloads from the canonical course exports:

```bash
python scripts/export_college_cutoffs.py --data-dir site/data --out site/data/college_cutoffs
```

## Admin import foundation
A server-side FastAPI admin foundation is included under `backend/`. Uploaded PDFs are stored outside `site/`, fingerprinted with SHA-256, identified, and held at `REVIEW_REQUIRED` before any extraction/production mutation. Create an admin with `python -m backend.admin.create_admin --username admin`, set `CET_ADMIN_SESSION_SECRET`, then run `uvicorn backend.main:app --host 127.0.0.1 --port 8000`.


## Admin Phase 2 — Real PDF Processing Pipeline

Part 2 connects the Admin Import workflow to the project's existing CET extractors and validation gates.

### Processing flow

`REVIEW_REQUIRED → EXTRACTING → NORMALIZING → VALIDATING → COMPARING → STAGED → REVIEW_REQUIRED`

Cutoff PDFs use `scripts/cutoff_extractor.py`, then the existing `validate_data.py` gate. The supplied `clean_data.py` is used only for its documented safe quarantine of genuinely blank-category rows; it does not guess missing values. Seat PDFs use `scripts/seat_matrix_extractor.py` and the existing `validate_seats.py` gate.

Every extracted row is copied into `import_staging_records` before any production fact table is touched. Import artifacts and validation output are kept under `data/import_work/` and `data/import_staging/`. Existing production `cutoffs` and `seats` rows are not modified by Part 2.

Start processing from the import detail page after reviewing the detected year, round, course family and data type. The processing endpoint requires `SUPER_ADMIN` or `DATA_ADMIN`.

The uploaded extractor scripts are preserved as `scripts/cutoff_extractor.py` and `scripts/seat_matrix_extractor.py`.


## Admin Part 3 — Review, Approval, Production Commit & Rollback

The Admin pipeline now has a real review boundary after staging. Reviewers can inspect
import metadata, validation results and staged rows through the Review Center. An
approval is server-side and transactional: validated staged cutoff/seat rows are
promoted into the production fact tables and a release record plus release-item
audit trail is created. If the transaction fails, production changes are rolled
back and the import is marked failed.

Rejected imports are quarantined with a mandatory reason. Releases can be rolled
back by a SUPER_ADMIN; rollback deletes only production row IDs recorded as
INSERT operations for that release.

Part 4 remains responsible for export generation, publishing, verification and
final launch audit. The public website is not modified by the approval step.
