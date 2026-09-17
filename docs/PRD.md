# Product Requirements Document — v1.0

## Product Overview
A public web app that transforms official Maharashtra State CET CAP
allotment PDFs into an interactive decision-support tool. Candidates
for BCA, BBA, MBA, and MCA enter their percentile, category, and quota
and instantly see a personalised list of colleges with historical
cutoff ranges and trends.

## Target Users
- Primary: Maharashtra CET candidates filling CAP option forms
  for BCA, BBA, MBA, MCA.
- Secondary: Parents helping candidates decide.

## Problem Statement
Official CAP allotment data is published as static, multi-page PDFs
across multiple courses, years, and rounds. Comparing a candidate's
percentile against hundreds of college cutoffs, across years and
rounds, is manual, error-prone, and impossible to trend.

## Goals
- A candidate goes from "I don't know where I'll get in" to a ranked,
  personalised college list in under 60 seconds.
- Show historical cutoff ranges (min–max) per college + program +
  category + section, across all available years and rounds.
- Provide an interactive trend chart so a candidate can inspect the
  cutoff at any year × round.
- Work well on mobile and desktop.

## Non-Goals
- No login, no server-side saved profiles, no candidate data logging.
- No live CAP round updates.
- No ML cutoff prediction.
- No scraped reviews or ratings.
- No option-list download (deferred post-capstone).
- No shareable filter URLs (deferred).
- No rank estimator (deferred).

## Core Features (MVP)

### F1. Filter Panel
Inputs:
- Course family (BCA / BBA / BMS / MBA / MMS / MCA and their variants)
- Year (multi-select)
- CAP Round (multi-select)
- Base category (OPEN, SC, ST, OBC, SEBC, EWS, TFWS, VJ/DT, NT, PWD*, DEF*)
- Section (HU / OHU / SL / HU_TO_OHU / OHU_TO_HU / MI)
- Ladies-only checkbox
- Percentile (0–100, required for eligibility filter)

### F2. Result List
Each row shows:
- Institute name
- Program
- Cutoff range across selected years + rounds, format: `98.20 – 80.55`
- Data-point count backing the range
- Chance badge:
  🟢 Safe (>+3) | 🟡 Likely (+1 to +3) | 🟠 Borderline (0 to +1) | 🔴 Tough (<0)

Sorted by chance badge then by max cutoff.

### F3. College Detail View
- Full institute info
- Interactive cutoff trend chart: X = year × round, Y = percentile
- Slider / hover to read exact value
- "Visit college website" link
- "Search on Google Maps" link

### F4. Data Quality Notice
Footer noting data sources, last-refresh date, historical-only cutoffs.
Must also disclose: a small number of source rows (see
`docs/DECISIONS.md` → "Quarantine known bad category values") are
excluded from results because the source PDF extraction corrupted
them — currently 1 row out of 67,770 (0.0015%).

### F5. Seat Capacity (new — 2026-09-12)
Adds sanctioned-intake seat counts alongside the existing cutoff
data, sourced from the CET seat-matrix PDFs (BCA/BBA/MBA/MCA), so a
candidate can see not just "what percentile got in last year" but
"how many seats exist in this category at all" — useful context for
categories with very few seats, where a single-year cutoff is noisy.
- Result list / detail view gains a "Total sanctioned seats" figure
  per institute + program + category (+ ladies split where the
  source discloses it — see Assumption A11).
- Explicitly NOT a fill-rate or vacancy feature (that would require
  cross-referencing seats against actual allotments per round, which
  is future work, not in this pass).
- Data-quality notice extends to seat data: 4 rows out of 70,269 are
  quarantined (same "Services) SC" extraction-artifact pattern as
  F4), and 610 rows (9 institutes) are held out of `seats` because
  those institutes have sanctioned capacity but no cutoff-side record
  to link to yet (see DECISIONS.md).

## User Flows

### Flow A — Candidate finds eligible colleges
1. Land → filter panel.
2. Select Course, Category, Section, Percentile.
3. Click "Find colleges".
4. Result list sorted by chance badge.
5. Click a row → detail with trend chart.

### Flow B — Parent asks "which years?"
1. Candidate on result list.
2. Clicks a row → trend chart shows 2024 → 2025 → 2026.
3. Slides the marker across rounds.

### Flow C — Candidate checks seat availability (F5)
1. Candidate on result list or detail view.
2. Sees sanctioned seat count for their category at that institute.
3. Uses it to judge how competitive/thin a category is, alongside
   the existing cutoff range.

## Future Features (post-capstone)
1. Option-list builder (tick → reorder → download PDF)
2. Shareable filter URLs
3. Compare 2–3 colleges side by side
4. "Similar colleges" within ±5%
5. ML cutoff forecast
6. Additional course families

## Technical Constraints
- Python 3.11+
- pdfplumber + pandas for extraction (already done)
- SQLite local dev, PostgreSQL prod
- Streamlit UI
- No candidate data logged or sent externally
- Mandatory indexes on institution_code, base_category,
  is_ladies, section_code, percentile
- Streamlit caching on all DB reads
- Extraction scripts wrapped in try/except per page

## Success Metrics
- Query response < 1s
- Page load < 3s on 4G mobile
- Zero crashes during a 10-minute live demo
- All available course-round combinations loaded and queryable
- At least 3 institutes manually verified against source PDFs

## Acceptance Criteria
- [x] All 30 CSVs listed in data/raw are loaded and pass
      `validate_data.py` with 0 outstanding issues.
- [x] All 4 seat-matrix CSVs in data/raw/seats are loaded and pass
      `validate_seats.py` with 0 outstanding issues (70,269 rows;
      4 quarantined extraction anomalies, every Total-row
      reconciliation check passes).
- [ ] User can filter by percentile + category + section + course.
- [ ] Each result row displays a cutoff min–max range.
- [ ] Each result row displays sanctioned seat count (F5).
- [ ] Clicking a row opens detail view with interactive trend chart.
- [ ] College website link works.
- [ ] Site runs on Streamlit Community Cloud.
- [ ] No errors in logs during a 30-minute session.
- [x] README, PRD, ARCHITECTURE, RULES, TESTING exist.

## Assumptions
- A1. All courses use the same CET portal and PDF/CSV layout.
- A2. Section strings are consistent across years (validated —
  confirmed 0 unknown sections across all 30 files).
- A3. Percentile column parses cleanly to float (validated —
  confirmed 0 bad percentages across all 30 files).
- A4. Institution/program/status/home_university-status/section
  context is forward-filled within a file, since the source PDF
  prints it once per block (validated and *widened* — see
  DECISIONS.md; originally scoped to program_name only, actual
  data required institution_code, institution_name, program_code,
  status, and section too).
- A5. Ladies seats are distinct from general seats.
- A6. `home_university` and `stage` are NOT forward-filled — both
  are legitimately 100%-blank in some entire files, not
  per-block continuation gaps (validated against real data).
- A7. A small number of source rows (currently 1) may contain
  extraction corruption (unrelated text landing in the category
  column). These are quarantined, not silently dropped or forced
  into the category whitelist.
- A8. The seat-matrix source (F5) carries no year/round — it is a
  single current-capacity snapshot, not a per-year history like
  cutoffs. `capture_year` is supplied at ingest time via
  `--year`, not derived from the file (see DECISIONS.md).
- A9. Seat-matrix PWD/DEF quota seats are reported institute-wide,
  with no HU/OHU split (unlike cutoffs, which do split PWD/DEF by
  HU/OHU/SL). F5 shows these seats without a section breakdown.
- A10. MBA/MCA seat-matrix data does not disclose a ladies/general
  split at all (gender is blank for 100% of MBA/MCA rows, vs. a
  real G/L split for every BCA/BBA row) — F5 cannot show a ladies
  seat count for MBA/MCA.
- A11. 9 institution codes appear in the seat matrix with no
  matching row anywhere in the 30 cutoffs CSVs (colleges with
  sanctioned capacity but no historical allotment on record). Their
  610 seat rows are held out of `seats` (logged to
  `seats_ingest_errors`, not silently dropped) until those
  institutes are added to `institutes`.
