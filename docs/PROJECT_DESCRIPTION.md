# CET CAP Decision Support — Project Description

This is the current, verified state of the project as of 2026-09-13.
Every number below was checked against a fresh, from-scratch run — none
of it is aspirational. `docs/DECISIONS.md` has the full reasoning
behind every non-obvious choice; this file is the map of what exists
and how it fits together.

---

## PART A — What this project is

A Streamlit web app that turns Maharashtra State CET CAP admission
data (cutoffs + seat-matrix PDFs, already extracted to CSV) into a
searchable decision-support tool. A candidate enters their city,
percentage, and course; the app shows every college whose historical
cutoff they'd have cleared, most competitive first; clicking a college
opens a detail panel with their actual category's cutoff trend, seat
matrix, and college website — without leaving the page.

---

## PART B — Project structure (as it actually exists)

```
cet-cap-portal/
├── README.md                     — setup + how to run, kept in sync with reality
├── requirements.txt
├── .gitignore
├── .streamlit/
│   ├── config.toml
│   └── secrets.toml.example
├── VALIDATION_REPORT.txt         — checked-in output of validate_data.py (reproducible)
├── SEATS_VALIDATION_REPORT.txt   — checked-in output of validate_seats.py (reproducible)
│
├── data/
│   ├── raw/                      — 30 cutoffs CSVs (BCA/BBA/MBA/MCA, 2024–2026, all rounds)
│   │   └── seats/                — 4 seat-matrix CSVs (one per program family)
│   ├── processed/                — empty, reserved
│   └── reference/
│       ├── category_whitelist.csv
│       ├── section_whitelist.csv
│       ├── stage_whitelist.csv
│       ├── program_aliases.csv
│       ├── allocation_lane_whitelist.csv
│       ├── seat_category_aliases.csv
│       └── institute_contacts.csv   — manually-compiled city/website source (989 rows)
│
├── db/
│   └── schema.sql                — full schema, see PART D
│
├── scripts/
│   ├── validate_data.py          — cutoffs QA gate (also exposes shared parsing helpers)
│   ├── validate_seats.py         — seat-matrix QA gate
│   ├── seed_reference_tables.py  — seeds institutes/base_categories/sections/stages
│   ├── ingest.py                 — loads cutoffs CSVs into the `cutoffs` table
│   ├── ingest_seats.py           — loads seat-matrix CSVs into the `seats` table
│   └── enrich_institutes.py      — populates institutes.city/.website
│
├── src/cet_cap/
│   ├── __init__.py
│   ├── db.py                     — SQLAlchemy engine (SQLite dev / PostgreSQL prod)
│   ├── queries.py                — all SQL, parameterised, no UI code
│   └── search.py                 — pure pandas aggregation, no DB access
│
├── app/
│   └── Home.py                   — the only page: 3-input search + detail dialog
│
├── tests/                        — 34 tests, all passing (see PART E)
│
└── docs/
    ├── PRD.md
    ├── ARCHITECTURE.md
    ├── AGENTS.md
    ├── RULES.md
    ├── DECISIONS.md              — read this for the "why" behind everything below
    └── PROJECT_DESCRIPTION.md    — this file
```

---

## PART C — What's built, verified, end to end

### Phase 1 — Data validation
- `validate_data.py`: 30 cutoffs CSVs, 67,770 rows, **0 issues** (1
  extraction anomaly quarantined and logged, not silently dropped).
  Output matches `VALIDATION_REPORT.txt` byte-for-byte on every re-run.
- `validate_seats.py`: 4 seat-matrix CSVs, 70,269 rows, **0 issues** (4
  known extraction anomalies quarantined). Matches
  `SEATS_VALIDATION_REPORT.txt` byte-for-byte.

### Phase 2 — Ingestion into SQLite
- `seed_reference_tables.py` → `institutes` (969, with recency-correct
  names for the 29 codes that were renamed across years),
  `base_categories` (48), `sections` (7), `stages` (9).
- `ingest.py` (cutoffs) → **67,769 rows** in `cutoffs` (67,770 read, 1
  quarantined, 0 duplicates, 0 failures). Idempotent: re-running
  reports `inserted=0 duplicate=67769`.
- `ingest_seats.py` (seat matrix) → **69,655 rows** in `seats` (70,269
  read, 4 quarantined, 610 held out — 9 institutes that exist only in
  the seat matrix, not in any cutoffs file — 0 duplicates). Idempotent
  in the same way.
- `enrich_institutes.py` → **967/969 institutes (99.8%) have a
  verified city**, 916/969 have a website. The 2 unresolved codes have
  genuinely conflicting source data and are printed explicitly rather
  than guessed.

All four scripts were run together against one database in the same
sitting, with no foreign-key conflicts, and the printed totals were
checked against `SELECT COUNT(*)` on the actual tables every time —
not just trusted at face value.

### Phase 3 — The app
`app/Home.py`, one page:
1. **Search**: City (free text), Percentage, Course (BCA/BBA/MBA/MCA).
2. **Results list**: college name, highest historical cutoff cleared
   (compared across every year/round on record, any category), city.
   Nothing else is shown here by design.
3. **Detail panel** (`st.dialog`, opens over the same page — not a new
   URL): category selector, cutoff trend chart across years/rounds,
   full year/round/stage/section breakdown, website link, seat matrix
   (sanctioned totals kept separate from the per-category breakdown so
   nothing is double-counted).

One background query loads everything for a search; the detail panel
filters that same in-memory data — it does not re-query the database
per click. Seat-matrix data is the one exception that needs its own
query, since it isn't part of the cutoffs data at all.

---

## PART D — Database schema (current, not aspirational)

Reference tables: `institutes` (now including `city`/`website`),
`programs`, `base_categories`, `sections`, `stages`, `allocation_lanes`.

Fact tables:
- `cutoffs` — `UNIQUE(year, round, institution_code, program_id,
  base_category, is_ladies, section_code, stage_code, rank_number,
  percentile)`.
- `seats` — `UNIQUE(capture_year, program_family, institution_code,
  choice_code, allocation_lane, raw_category, raw_gender)`, with
  `raw_gender` **NOT NULL DEFAULT ''** rather than nullable — a
  nullable column inside a UNIQUE key doesn't dedupe (SQL treats
  `NULL != NULL`), which was a real bug found and fixed here (see
  DECISIONS.md).

Audit tables: `staging_cutoffs`, `ingest_log`, `ingest_errors` (cutoffs
side); `staging_seats`, `seats_ingest_log`, `seats_ingest_errors` (seat
side) — each ingest script logs real duplicates and quarantined rows
here, not just a printed count.

---

## PART E — Test coverage (34 tests, all passing)

- `test_seats_normalization.py` — unit tests for seat-matrix parsing rules.
- `test_seed_and_ingest_end_to_end.py` — pins the seed→ingest_seats
  pipeline's exact totals and the fix for two real bugs (missing
  `program_family` in the UNIQUE key; nullable `raw_gender`).
- `test_ingest_cutoffs_end_to_end.py` — pins `ingest.py`'s exact totals
  and guards against the forward-fill regression found during
  development (an earlier draft dropped 11.5% of rows).
- `test_eligibility_query.py` — the original single-category lookup
  (`find_eligible_colleges`), still used internally by the detail panel's
  logic; checks the eligibility direction, sort order, and category
  exactness against a real database.
- `test_search.py` — pure logic for the 3-input search
  (`filter_by_city`, `summarize_colleges`), synthetic data, no database.
- `test_search_end_to_end.py` — the same logic against a real seeded +
  ingested + enriched database: city filtering only matches enriched
  rows, the results list has exactly the columns the UI is allowed to
  show, category-mixing is verified to actually happen (not just
  claimed), and the seat matrix's total-vs-breakdown split holds a real
  numeric invariant.

Every end-to-end test builds its own temporary database from the real
CSVs — none of this is fixture data standing in for the real thing.

---

## PART F — Known limitations (stated plainly, not hidden)

1. **City has no real database column upstream** — it comes from a
   manually-compiled CSV, not the CET PDFs. 2 of 969 institutes
   (02672, 02684) have conflicting city data in that source file and
   are excluded from city search until resolved by hand.
2. **The results list mixes categories.** Since category isn't an
   input on screen 1, a college's "highest cutoff" can come from a
   reserved category (SC/ST/etc.), which doesn't mean a general
   candidate can get that specific seat — only the detail panel gives
   the category-accurate answer. This was a deliberate, discussed
   trade-off, not an oversight.
3. **`rank_number` remains nullable** in the `cutoffs` schema even
   though a nullable column inside a UNIQUE key is exactly the bug
   class fixed for `seats.raw_gender` — left as-is because every real
   row currently has a valid rank, but flagged in DECISIONS.md as a
   latent risk if that ever changes.
4. Seat-matrix figures reflect the **year passed to `ingest_seats.py`
   at ingest time** (currently 2026) — the source data itself doesn't
   carry a year.

---

## PART G — How to run it

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python scripts/validate_data.py data/raw/
python scripts/validate_seats.py data/raw/seats/

python scripts/seed_reference_tables.py --db db/cet_cap.db
python scripts/ingest.py data/raw/ --db db/cet_cap.db
python scripts/ingest_seats.py data/raw/seats/ --year 2026 --db db/cet_cap.db
python scripts/enrich_institutes.py data/reference/institute_contacts.csv --db db/cet_cap.db

streamlit run app/Home.py -- --db db/cet_cap.db
pytest tests/ -q
```
