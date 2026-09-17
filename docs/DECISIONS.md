# Decision Log

## Use PostgreSQL for production, SQLite for local dev
Date: 2026-09-11
Status: Accepted
Context: Data ~70k rows now, will grow with more years.
Options: SQLite everywhere, PostgreSQL everywhere, both.
Decision: SQLite locally, PostgreSQL (Neon free tier) in production.
Reasoning: Streamlit Cloud needs a network DB; Neon free tier
covers our size.
Trade-offs: Two dialects to support. Mitigated by SQLAlchemy.
Consequences: All SQL must use SQLAlchemy parameters.
Alternatives Rejected: SQLite on Streamlit Cloud (ephemeral FS).

## Decompose category into 3 columns
Date: 2026-09-11
Status: Accepted
Context: Category codes are 3-dimensional (ladies / base / section).
Options: Store raw string only, store 1 column, store 3 columns.
Decision: Store base_category, is_ladies, section_code,
plus raw_category for audit.
Reasoning: Filtering is trivial and indexable with 3 columns.
Trade-offs: Slightly more complex ETL.
Alternatives Rejected: Single string column (unqueryable).

## Streamlit over Flask
Date: 2026-09-11
Status: Accepted
Context: Solo student developer, 7-week deadline.
Options: Flask + templates, FastAPI + React, Streamlit.
Decision: Streamlit.
Reasoning: Zero frontend work, free hosting, caching built-in.
Trade-offs: Less UI control.
Alternatives Rejected: Flask (too much boilerplate for scope).

## Forward-fill program_name within a file
Date: 2026-09-11
Status: Superseded 2026-09-12 — see "Forward-fill scope widened"
below. Kept here for history.
Context: 11% of rows have empty program_name.
Options: Drop rows, error out, forward-fill.
Decision: Forward-fill, log count, fail if first row empty.
Reasoning: The PDF only prints the name once per block.
Trade-offs: Risk of misassignment if layout interleaves blocks
(not observed in samples).
Alternatives Rejected: Rejecting the rows (data loss).

## Staging table before facts
Date: 2026-09-11
Status: Accepted
Context: Bad rows must not crash ETL.
Options: Direct load, staging then normalise.
Decision: Stage raw rows first, then parse.
Reasoning: Idempotent, auditable, debuggable.
Alternatives Rejected: Direct load (untraceable failures).

## Never log user filters
Date: 2026-09-11
Status: Accepted
Context: Constitution forbids logging candidate percentile.
Options: Log for debugging, never log.
Decision: Never log. Sentry send_default_pii=False.
Reasoning: Privacy by design.
Consequences: Debug by reproducing with fake data only.

---
# 2026-09-12 — First real validator run against all 30 CSVs (67,770 rows)

Everything below was found and fixed by actually running
`validate_data.py` against the real dataset, not by inspecting
samples. Each finding was manually verified against its source rows
before being fixed — see the reasoning under each entry.

## Whitelist gaps: missing category codes and one program alias
Date: 2026-09-12
Status: Accepted
Context: First validator run surfaced 11 unknown category values
(~3,700 rows) and 1 unknown program name (20 rows).
Findings, each individually verified against source rows:
- `NTA` was missing from category_whitelist.csv (as `GNTAH`,
  `GNTAO`, `GNTAS`, `LNTAH`, `LNTAS`, `LNTAO`) — NT1/NT2/NT3/NTB/
  NTC/NTD were present but not NTA. A real, common Maharashtra CET
  category; institute names, ranks, percentiles all check out.
- Bare `PWD` (132 rows, no H/O/S suffix) was missing — real
  category for unspecified-quota PWD candidates.
- `PWDROBC` and `DEFROBC` were missing, while every other PWDR*/
  DEFR* variant (SC, SEBC, NTA–NTD) was already listed. A gap, not
  a new category type.
- `MBA (Business Administration)` was missing from
  program_aliases.csv while `BBA (Business Administration)` was
  present — a copy-paste typo (BBA vs MBA) in the original alias
  list.
Decision: Added `NTA`, `PWD`, `PWDROBC`, `DEFROBC` to
category_whitelist.csv; added `MBA (Business Administration)` to
program_aliases.csv.
Reasoning: All five confirmed as real, valid values via source-row
inspection.
Alternatives Rejected: Dropping the ~3,700 affected rows as
"unknown" (would silently discard legitimate candidate records
across MBA, BBA, MCA, BCA files).

## Forward-fill scope widened from 1 column to 6
Date: 2026-09-12
Status: Accepted — supersedes "Forward-fill program_name within a
file" above.
Context: After the whitelist fix, 83 rows still showed no
institution_code/institution_name (which the validator was — at
that point — treating as broken/corrupted rows). Inspecting one
directly showed the row was a normal continuation row: the PDF
prints institution_code, institution_name, program_code,
program_name, status, home_university, and section once per block,
then leaves them ALL blank until the block changes — not just
program_name as originally scoped.
Investigated whether `home_university` and `stage` should also join
the fill group. Checked blank-percentage per column per file first:
found `home_university` is 100%-blank across the *entire* file for
some files (e.g. all of MBA_25_C1.csv) and `stage` is 100%-blank
across the entire file for others (e.g. all of BCA_26_C1.csv). That
is the source PDF genuinely not carrying that data for those files
— forward-filling would fabricate values, not recover them.
Decision: Forward-fill institution_code, institution_name,
program_code, program_name, status, and section as one group. Do
NOT forward-fill home_university or stage. Added a FIRST_ROW_BLANK
file-level check (a block can't inherit context if row 0 itself is
blank in one of the six fill columns).
Reasoning: Verified none of the six fill columns is ever >50% blank
in any single file (ruling out the "whole column is genuinely
empty" trap home_university/stage fell into) — confirming all six
are true per-block continuation fields, not independently-optional
ones.
Trade-offs: Forward-fill count went from 7,776 rows (program_name
only) to 16,163 rows (all six columns) — expected, since most
continuation rows leave 5–6 columns blank at once, not just one.
Alternatives Rejected: Forward-filling all 8 candidate columns
uniformly (would have fabricated home_university/stage values that
were never in the source PDF).

## Quarantine known bad category values instead of whitelisting them
Date: 2026-09-12
Status: Accepted
Context: After both fixes above, exactly 1 row remained unresolved:
BCA_24_C2.csv, `category = "GOVERNMENT"`. Inspected its context
directly — even accounting for forward-fill, this row's field
pattern doesn't match a normal candidate record. "GOVERNMENT" reads
as a fragment of institution status text (e.g. "Government Aided")
that bled into the category cell during PDF extraction — a
column-shift artifact, not a category code.
Options considered: (a) add "GOVERNMENT" to the category whitelist
anyway, (b) silently drop the row, (c) explicitly quarantine and
log it.
Decision: Added a small, individually-audited
`KNOWN_BAD_CATEGORY_VALUES` set to validate_data.py. Rows matching
it (or rows with no institution_code AND no institution_name even
after the forward-fill above) are counted separately as
"quarantined extraction anomalies" in the validator report and
excluded from `cutoffs` at ingest time — routed to `ingest_errors`,
never silently dropped without a trace.
Reasoning: This is 1 row out of 67,770 (0.0015%). Whitelisting a
value confirmed NOT to be a real category would corrupt
category-level reporting and analytics for the whole project going
forward. Silent dropping would leave no audit trail if a future
file has more of these.
Alternatives Rejected: Whitelisting "GOVERNMENT" as a category
(factually wrong, pollutes the schema); silent drop (unauditable,
violates the "log every skipped row" rule in RULES.md).

## Net result of the 2026-09-12 validation pass
- Before: 30 files, 30/30 pass filename+column checks, but 12
  distinct outstanding issues (11 categories + 1 program alias)
  affecting ~3,700 rows, plus a forward-fill gap silently
  mis-reporting 83 legitimate rows as broken.
- After: `Total issues: 0` across all 30 files / 67,770 rows.
  Exit code 0. Exactly 1 row (0.0015%) explicitly quarantined with
  a documented reason — not zero because of a false negative, but
  because it's the one row that's actually corrupted.

---
# 2026-09-12 (continued) — Seat-matrix (F5) integration

The candidate forgot to include the seat-matrix source (sanctioned
intake capacity) in the original scope. This section covers the
decisions made integrating it as a second, independent fact table
alongside `cutoffs`.

## New `seats` fact table instead of extending `cutoffs`
Date: 2026-09-12
Status: Accepted
Context: Seat matrix rows (institution + choice_code + quota lane +
category + seats) don't carry rank/percentile at all — they're
capacity, not allotment outcomes. `choice_code` matches
`cutoffs.program_code`'s format exactly, so the two are naturally
joinable, but forcing seats into the `cutoffs` schema would mean
either a giant nullable rank/percentile block on every capacity row,
or overloading `cutoffs` with a row "type" flag.
Options: (a) add seats as extra columns/rows on `cutoffs`, (b) a
separate `seats` table joined via institution_code + choice_code.
Decision: Separate `seats` table, joined via choice_code ==
program_code.
Reasoning: Keeps `cutoffs` (allotment outcomes) and `seats`
(capacity) as what they actually are — different grains, different
sources, different update cadence (seats is a single snapshot;
cutoffs is per year/round). A join is trivial; a merged schema is not
reversible without another migration.
Trade-offs: Two fact tables to query for any "seats + cutoff" view;
mitigated since the query layer isn't built yet (Phase 4).
Alternatives Rejected: Overloading `cutoffs` with nullable
rank/percentile columns for capacity rows (would make every cutoffs
query need a `WHERE row_type = ...` guard).

## Reuse atomic base_categories; no PWD/DEF-prefixed category codes
Date: 2026-09-12
Status: Accepted
Context: The seat matrix's `category` column only ever contains the
atomic codes (OPEN, SC, ST, OBC, SEBC, VJDT/VJ-DT, NTB, NTC, NTD, or
the 'Total' subtotal marker) — never the PWD-/DEF-prefixed compound
codes (PWDOPEN, DEFSC, ...) that appear in `cutoffs.category`.
Instead, PWD/DEF show up as a value of the *separate*
`allocation_type` column, alongside HU/OHU/State Level, applied
uniformly across all 9 atomic categories.
Options: (a) synthesize compound codes (e.g. PWD + OPEN ->
"PWDOPEN") to match cutoffs' base_category convention, (b) keep
category atomic and model PWD/DEF as their own dimension.
Decision: (b). `seats.base_category` references the same
`base_categories` table cutoffs already uses, unmodified — no new
whitelist entries needed, since every value seat matrix produces
(OPEN, SC, ST, OBC, SEBC, VJ/DT, NTB, NTC, NTD) already exists there.
`allocation_type` becomes its own `allocation_lanes` reference table
(HU, OHU, SL, PWD, DEF).
Reasoning: Synthesizing compound codes would have required adding
`PWDVJDT`/`DEFVJDT` to category_whitelist.csv purely to satisfy this
one source — codes that have never appeared in 67,770 cutoffs rows
because nobody has ever been allotted a VJDT seat under PWD/DEF quota
(seats=0 for that combination in the real data). Inventing a category
code with zero real-world evidence for it violates the "verify
against real rows before whitelisting" rule (RULES.md). Keeping the
dimensions separate needed no such invention.
Alternatives Rejected: Synthesized compound codes (whitelist
pollution with unverifiable codes); a single opaque
`raw_category + raw_allocation_type` string with no normalisation
(unqueryable, defeats the purpose of a fact table).

## allocation_lanes: a new dimension, not reuse of `sections`
Date: 2026-09-12
Status: Accepted
Context: HU and OHU in the seat matrix's `allocation_type` match
`sections.section_code` exactly. But PWD and DEF also appear as
`allocation_type` values, and cutoffs data proves PWD/DEF categories
*do* have HU/OHU/SL variants there (e.g. `PWDOPENH` and `PWDOPENS`
both exist in the real cutoffs corpus) — yet the seat matrix reports
PWD/DEF as a single flat lane with no HU/OHU split at all (verified
by inspecting a full institution block: HU, OHU, PWD, DEF each
appear exactly once per choice_code, each with their own 9-category +
Total set).
Options: (a) force PWD/DEF rows into section_code='SL' as a guess,
(b) model allocation_type as its own dimension with its own
whitelist.
Decision: (b) — new `allocation_lanes` reference table (HU, OHU, SL,
PWD, DEF), independent of `sections`.
Reasoning: Silently mapping PWD/DEF to section='SL' would assert a
fact not in the source data (that seat-matrix PWD/DEF seats are
specifically State-Level ones) when the source simply doesn't
disclose that split. Modelling it as its own dimension makes the
gap visible instead of hiding it inside a guessed value.
Trade-offs: `seats` and `cutoffs` are not directly comparable on the
section/lane dimension for PWD/DEF rows — documented as PRD
Assumption A9.
Alternatives Rejected: Guessing section='SL' for PWD/DEF (asserts
an unverified fact); guessing section='HU' for the first PWD/DEF row
seen per institution (arbitrary, no basis in the data).

## 'VJDT' vs 'VJ/DT' spelling — alias table, not a whitelist edit
Date: 2026-09-12
Status: Accepted
Context: BBA_SM.csv, MBA_SM.csv, and MCA_SM.csv all spell the
Vimukta Jati / Denotified Tribe category `VJDT` (no slash);
BCA_SM.csv spells it `VJ/DT`, matching category_whitelist.csv's
existing entry. Same category, two spellings, because the seat
matrix and cutoffs sources come from different original PDF
extractions.
Options: (a) add `VJDT` as a second whitelist entry, (b) a small
alias table resolved before the whitelist check.
Decision: (b) — data/reference/seat_category_aliases.csv maps
`VJDT` -> `VJ/DT` (and `VJ/DT` -> itself, for the BCA file).
Reasoning: Adding `VJDT` as a second first-class whitelist entry
would let the same real-world category exist under two codes in
`base_categories`, silently splitting any category-level rollup
across cutoffs and seats. An alias resolved at parse time keeps
exactly one canonical code.
Alternatives Rejected: Second whitelist entry (would fragment
category-level analytics between the two spellings).

## 'Total' subtotal rows: cross-validated, not treated as a category
Date: 2026-09-12
Status: Accepted
Context: Every (institution_code, choice_code, allocation_type)
block in the seat matrix ends with its own `category = 'Total'` row
— the source PDF's own subtotal for that block.
Options: (a) drop Total rows entirely, (b) whitelist 'Total' as if
it were a real category, (c) keep the rows but flag them and use
them as a cross-check instead of another category.
Decision: (c). `seats.is_total` is TRUE for these rows and
`base_category` is left NULL (not joined to base_categories at all).
validate_seats.py additionally sums each block's non-Total rows and
asserts it equals that block's Total row — a "TOTAL-ROW
RECONCILIATION" check with no equivalent in the cutoffs validator,
since the seat matrix's own printed subtotal gives us ground truth
the cutoffs source doesn't have.
Reasoning: Whitelisting 'Total' as a category would double-count
seats in any `SUM(seats) GROUP BY base_category` rollup. Dropping it
would throw away a free data-quality check. Verified 0 reconciliation
mismatches across all 78,269 real rows (BBA/BCA/MBA/MCA) — a strong
signal the seat-matrix extraction is internally consistent.
Alternatives Rejected: Whitelisting 'Total' as a category (silent
double-counting); dropping the rows (losing a real cross-check for
free).

## Quarantine 'Services) SC' (seat-matrix equivalent of GOVERNMENT)
Date: 2026-09-12
Status: Accepted
Context: MBA_SM.csv, institution_code=02508, choice_code=0250864810
has `category = "Services) SC"` on all 4 lanes (HU/OHU/PWD/DEF) of
that one block. Inspected surrounding rows directly: the pattern
(same institution, same choice_code, otherwise-normal SC-adjacent
category sequence) matches institution-name/status text bleeding
into the category cell — the same class of column-shift artifact as
`GOVERNMENT` in BCA_24_C2.csv, not a new category.
Decision: Added `KNOWN_BAD_CATEGORY_VALUES = {'Services) SC'}` to
validate_seats.py, following the exact same quarantine discipline as
validate_data.py (logged to seats_ingest_errors, excluded from
`seats`, never silently dropped or whitelisted).
Reasoning: 4 rows out of 70,269 (0.006%). Same reasoning as the
original GOVERNMENT decision — whitelisting a confirmed-corrupted
value would pollute category-level reporting.
Alternatives Rejected: Whitelisting "Services) SC"; silent drop.

## capture_year is an ingest-time argument, not source data
Date: 2026-09-12
Status: Accepted
Context: Unlike cutoffs (year/round parsed from the filename), the
seat-matrix source has no year or round anywhere in its columns or
filenames (BBA_SM.csv, not BBA_SM_2026.csv) — it's a single
current-capacity snapshot, not a per-year history.
Options: (a) hardcode a year in the ingest script, (b) require
`--year` as an explicit CLI argument with no default.
Decision: (b). `ingest_seats.py --year 2026 ...` is required; there
is no default.
Reasoning: Hardcoding a year risks silently mislabelling every row
if the source is ever refreshed for a new admission cycle without
the code being updated. Requiring the caller to state it explicitly
makes the assumption visible and forces a conscious choice each time
new seat-matrix PDFs are ingested. Documented as PRD Assumption A8.
Alternatives Rejected: Hardcoding 2026 (silent staleness risk on
next year's re-ingest); inferring from the most recent cutoffs year
(would break the moment cutoffs and seats are refreshed at different
times, which is expected — seat matrices update far less often than
per-round cutoffs).

## institution_code: derive from choice_code, not the numeric column
Date: 2026-09-12
Status: Accepted
Context: The seat matrix's `institution_code` column round-trips
through some tooling as a number (e.g. `2113`), losing the leading
zero that `institutes.institution_code` (TEXT, zero-padded to 5
digits, e.g. `02113`) requires for the foreign key to resolve — even
though the raw CSV text itself is fine ("02113"). `choice_code`
(e.g. `0211310110`) is a string and its first 5 characters always
preserve the zero-padded institution code.
Decision: `ingest_seats.py` derives the canonical institution_code
from `choice_code[:5]`, and cross-checks it against the
(zero-padded) `institution_code` column as a sanity check — raising
if they disagree, rather than picking one silently.
Reasoning: This is more robust to whatever tool a future re-export
of this CSV uses to write institution_code, since choice_code's
string format is far less likely to be reinterpreted as a number.
Trade-offs: Adds one extra parsing step per row; negligible cost
(70k rows insert in well under a second on the dev machine).
Alternatives Rejected: Zero-padding the institution_code column
directly and trusting it (would have silently produced wrong
institution_codes if this file is ever re-exported through a path
that also mangles choice_code, with no cross-check to catch it).

## 9 institutions in seats with no cutoffs-side record: quarantined, not backfilled
Date: 2026-09-12
Status: Accepted
Context: Cross-referencing all seat-matrix institution codes against
every institution_code that appears anywhere in the 30 cutoffs CSVs
found 9 with no match at all (02711, 02799, 02810, 04757, 04759,
05628, 05666, 05672, 06305) — colleges with sanctioned seats on
record but no candidate ever allotted there across 2024–2026 in this
dataset (a real, plausible situation: newly approved intake, or a
program with seats nobody has filled yet).
Options: (a) fabricate institution_name for these 9 from the seat
matrix's own source_pdf/page (which doesn't contain a name column at
all), (b) let the foreign key fail and quarantine those rows.
Decision: (b). `seats.institution_code` keeps its FK to `institutes`;
rows for these 9 institutes fail on ingest and are logged to
`seats_ingest_errors` with reason "FOREIGN KEY constraint failed" —
610 rows out of 70,269, confirmed by an end-to-end test ingest
(69,655 inserted, 4 quarantined as extraction anomalies, 610 held
out on this FK, 0 rows unaccounted for).
Reasoning: There is no institution name anywhere in the seat-matrix
CSVs to backfill `institutes` with — inventing one would be a
fabrication, not a recovery. Quarantining with a clear, auditable
reason is consistent with how every other class of bad row in this
project is handled (RULES.md "never swallow exceptions silently").
Trade-offs: Seat capacity for these 9 institutes won't show up in
F5 until `institutes` gets a real name for them from another source
(e.g. the AICTE/DTE institute directory) — out of scope for this
pass.
Alternatives Rejected: Fabricating a placeholder institution_name
like "Unknown (02711)" (would silently pollute `institutes` with
non-source data); dropping the FK constraint entirely (would let
future seat-matrix refreshes reference nonexistent institutes with
no warning).

## Net result of the seat-matrix (F5) integration
- 4 files, 70,269 rows validated: `Total issues: 0` (4 quarantined
  extraction anomalies, 0 unknown categories, 0 unknown allocation
  lanes, 0 Total-row reconciliation mismatches).
- End-to-end test ingest (fresh SQLite, base_categories + institutes
  seeded from existing reference data): 69,655 rows inserted into
  `seats`, 4 quarantined, 610 held out on 9 missing institutes —
  every row accounted for (read == inserted + skipped + failed, all
  4 files).

## seed_reference_tables.py: bridging institutes/base_categories until ingest.py exists
Date: 2026-09-12
Status: Accepted
Context: The "end-to-end test ingest" result directly above (69,655 /
4 / 610) was produced against a database that already had
`institutes` and `base_categories` populated. Nothing in this repo
actually does that populating yet — the intended long-term source is
the cutoffs `ingest.py` (Phase 2), which "is not yet built" (see
README). Concretely: a genuinely fresh clone running the documented
Quick Start commands in order gets `institutes` and `base_categories`
both empty, and `ingest_seats.py` fails essentially every row with
`FOREIGN KEY constraint failed` — not the 610-row result documented
above. The documented result was real but not reproducible from what
was shipped.
Options: (a) leave it undocumented and let whoever runs the Quick
Start hit the wall of FK failures, (b) just fix the README wording to
say seat ingestion isn't runnable standalone yet, (c) write a small
bridge script that derives `institutes` and `base_categories` from
data already in the repo (the 30 cutoffs CSVs and the category
whitelist) so `ingest_seats.py` is actually runnable end-to-end now,
without waiting on Phase 2.
Decision: (c), plus updating the README so the Quick Start includes
the bridge step. `scripts/seed_reference_tables.py`: `base_categories`
loads straight from `data/reference/category_whitelist.csv` (columns
already match 1:1). `institutes` is derived from every
(institution_code, institution_name) pair across the cutoffs CSVs;
970 unique codes were found, 29 of which have more than one distinct
raw name string across years (typos/punctuation variants, e.g.
"S.N.D. College of Engineering & Reserch" vs "...& Research") — the
most frequent name per code is kept as canonical and every variant is
printed to stdout so the choice is auditable, not silent.
`institute_type` / `affiliation_status` are left NULL: the source
`status` column is free text mixing affiliation, autonomy, minority
status, and home-university in one string per row (528 of 970 codes
have more than one distinct `status` string across rows) and isn't
reliably splittable here — that parsing belongs in the real
`ingest.py`, not this bridge.
Reasoning: Re-running seed + ingest against a genuinely fresh DB now
reproduces the documented 69,655 / 4 / 610 result exactly, including
the same 9 orphan institution codes (02711, 02799, 02810, 04757,
04759, 05628, 05666, 05672, 06305) called out above — confirming the
bridge doesn't change which rows are considered orphaned, it just
makes the documented result actually reproducible. Added
`tests/test_seed_and_ingest_end_to_end.py` to pin these exact totals
and codes as a regression test, since this is the kind of gap that's
easy to reintroduce silently (e.g. if schema.sql changes) and hard to
notice without an integration test running seed → ingest end to end.
Trade-offs: `institutes.institution_name` for the 29 multi-variant
codes is a best-guess majority pick, not a verified canonical name —
acceptable for a temporary bridge, not for the final ingest.py.
This script should be retired once `ingest.py` owns `institutes`
properly (including the `status`/`home_university` parsing it
currently skips).
Alternatives Rejected: Option (a) (ship the gap silently) — already
misled one review pass into treating a non-reproducible README claim
as verified. Option (b) (docs-only fix) — leaves seat ingestion
genuinely blocked until Phase 2, when the data needed to unblock it
(cutoffs CSVs, category whitelist) is already sitting in the repo.

## Second review pass: institute-name recency rule, and two real data-loss bugs the first pass's success masked
Date: 2026-09-12
Status: Accepted
Context: A second, more skeptical pass over the fix above — checking it
from a data-correctness and idempotency angle rather than just
"does it run and match the printed total" — found three further
problems. The first pass's own success was what hid these: nothing
had ever gotten past the FK failures before, so nobody had looked at
whether the loaded data was actually *right*, or what happens on a
second run.

**(a) Institute-name selection was picking stale names.** The
seed script's original rule (majority name by raw row count, across
all years) happened to reproduce the documented 69,655/4/610 totals,
which made it look correct. But checking the 29 multi-name codes
individually found at least 14 are genuine institution renames
correlated with year (e.g. institution_code 01321: "INSTITUTE OF
MANAGEMENT STUDIES MAHAVIDYALAYA WARUD" in every 2024 row, "Swami
Vivekananda College Of Management..." in every 2025/2026 row) — not
typos. A frequency vote over all years picks whichever name has more
historical rows, which is wrong whenever the rename is recent (row
volume tracks how long a name has been in use, not which name is
current). Fixed: canonical name is now the majority name in the
code's most recent year, not across all years — verified this
changes the picked name for 14 of the 29 codes, all toward the more
current one, with zero unresolved ties.

**(b) `seats` UNIQUE key missing `program_family` — real cross-family
data loss.** Cross-referencing the seat-matrix CSVs directly (not
through the ingest script) found `choice_code` is reused across
program families with different seat counts for each — e.g.
institution_code 01111 / choice_code 0111110110 means one specific
BCA seat allocation and a completely different MBA one. The `seats`
table's UNIQUE constraint didn't include `program_family`, so
whichever family's file happened to be processed first for a given
key won, and the others were silently dropped by INSERT OR IGNORE —
undetected because `ingest_file()` incremented `rows_inserted` on
"no exception raised" without checking `cursor.rowcount`. On the
real dataset this affected roughly 20,647 rows and — the part that
matters — 8,207 of those had genuinely different seat counts between
the colliding rows (not harmless exact duplicates). Fixed: added
`program_family` to the UNIQUE key (confirmed 0 remaining collisions
in the full dataset once family-scoped), and rewrote the insert path
to check `cursor.rowcount` and log a real "duplicate" bucket
separately from "inserted" instead of assuming success.

**(c) `raw_gender` nullable inside that same UNIQUE key.** SQL
treats NULL as distinct from NULL, so a nullable column inside a
UNIQUE constraint provides no dedup at all for rows where it's NULL.
43,431 of 70,269 rows (62% — the PWD/DEF/HU/OHU/State-Level lanes,
which don't carry a gender split) had NULL `raw_gender`, so every one
of them would silently duplicate on a second ingest run — verified:
before this fix, re-running `ingest_seats.py` against an
already-loaded DB inserted 43,177 unwanted extra rows instead of
reporting 69,655 duplicates and 0 new inserts. Fixed: `raw_gender` is
now `NOT NULL DEFAULT ''` end to end (schema and ingest script both),
matching how every other raw text column here is already handled.
Verified: re-running ingest twice on the same DB now reports
`inserted=0 duplicate=69655` the second time, and `SELECT COUNT(*)
FROM seats` stays at 69,655 across both runs.

Reasoning: (b) and (c) are true data-loss/data-duplication bugs, not
edge-case polish — either one would have silently corrupted sanctioned
seat-capacity figures the moment this pipeline saw a second run or a
new year's data. Neither was visible under the first pass because
nothing had successfully ingested before that pass's fix, so
`rows_inserted` had never been checked against reality. All three are
now covered by `tests/test_seed_and_ingest_end_to_end.py`, including a
second ingest run in the same test to make (c) impossible to
reintroduce silently.
Trade-offs: (a) is still a heuristic (most-recent-year majority, not
an authoritative registry) — could still be wrong for a code renamed
twice within one year, which doesn't currently occur in this data but
isn't structurally ruled out. `institute_type` / `affiliation_status`
remain out of scope for this bridge script either way (see prior
entry).
Alternatives Rejected: For (b), scoping the UNIQUE key more narrowly
(e.g. just adding a source-file discriminator instead of the semantic
`program_family` column) — rejected because it would hide the same
class of bug again for any other column that turns out to be reused
across contexts, where `program_family` at least reflects a
real semantic dimension of the data. For (c), leaving `raw_gender`
nullable and instead deduplicating in application code — rejected
because it moves a data-integrity guarantee out of the schema (where
every other constraint in this project lives) and into
whichever script happens to run next.

## ingest.py: reusing validate_data.py's forward-fill instead of re-deriving it
Date: 2026-09-12
Status: Accepted
Context: Phase 2's cutoffs ingest.py finally got built. The obvious risk,
having just fixed a from-scratch reimplementation of the seat-matrix
pipeline's bugs (see prior entries), was writing this one from scratch
too and re-deriving logic that validate_data.py had already discovered
and hardened against the real data — specifically the forward-fill of
block-context columns (institution_code, institution_name, program_code,
program_name, status, section), which the source PDFs only print once
per block, leaving every following row in that block blank for those
columns. A first draft of this exact script, written independently
without reusing that logic, checked for row completeness before any
forward-fill and silently dropped 7,777 of 67,770 rows (11.5%) as a
result — rows that were completely valid once filled, and that
validate_data.py already proved were valid (0 issues on the same 30
files).
Decision: refactored validate_data.py to expose forward_fill_block_columns()
and is_extraction_anomaly() as standalone functions (previously inlined
in main()), verified byte-identical VALIDATION_REPORT.txt output after
the refactor, and had ingest.py import them directly — along with
STAGE_MAP, SECTION_MAP, parse_category, and parse_filename, none of
which ingest.py re-derives. Also extended seed_reference_tables.py to
seed `sections` and `stages` (straightforward static reference data,
same pattern as base_categories) since ingest.py needs both.
Reasoning: verified against the actual `cutoffs` table (not just the
script's printed total): 67,769 rows inserted on a fresh run — exactly
67,770 raw rows minus the single already-documented quarantined anomaly
(BCA_24_C2.csv, category field "GOVERNMENT") — 0 duplicates, 0 failures.
Re-running is a true no-op (0 new inserts, 67,769 correctly detected as
duplicates via cursor.rowcount, same discipline as ingest_seats.py).
Ran cutoffs and seat-matrix ingestion into the same database together:
cutoffs=67,769, seats=69,655, no FK conflicts. Added
tests/test_ingest_cutoffs_end_to_end.py pinning these exact totals so
the forward-fill dependency can't be silently dropped again.
`programs.program_family` is deliberately populated from the filename
(BCA/BBA/MBA/MCA — matching how seats.program_family is scoped), not
from program_aliases.csv's own finer sub-classification (e.g.
"BCA_VISUAL_ARTS") — that CSV is only used here to look up `level`
(UG/PG/Integrated) per raw program name. `program_full` isn't stored;
`programs` has no column for it currently.
Trade-offs: rank_number remains nullable in the schema even though a
nullable column inside a UNIQUE key is exactly the class of bug fixed
for seats.raw_gender — left as-is because every real row currently has
a valid digit rank_number (verified directly), but this is a latent
risk if a future data source ever omits it; worth revisiting if that
changes.
Alternatives Rejected: Re-deriving category/stage/section parsing
independently in ingest.py "to keep it self-contained" — rejected
because that duplication is exactly the mechanism that caused the
forward-fill bug in the first draft; a second copy of validated logic
only stays correct until the two copies drift.

## Eligibility query: percentile direction, category exactness, and the zero-cutoff artifact
Date: 2026-09-13
Status: Accepted
Context: First candidate-facing feature — "what colleges would I have
qualified for at this percentile?" Two things needed to be verified
against real data before writing the query, not assumed: which
direction the comparison goes, and whether category should ever widen
beyond an exact match.
Decision: A cutoff row is the percentile of the last (lowest-performing)
candidate admitted to that seat, so a candidate qualifies when their
percentile >= the cutoff. Verified this empirically (percentile and
rank_number move in opposite directions consistently in the real data)
rather than assuming CET convention. Category matching is exact — a
candidate in category X is never shown category Y cutoffs, even more
permissive ones — broadening categories is a different, more permissive
rule a user would have to explicitly ask for. One known data artifact
(exactly 1 row, rank_number=0 AND percentile=0.0 — year 2026 round 3,
institution 03218, MCA/OPEN) is filtered out of every eligibility query;
it would otherwise look like a seat anyone qualifies for.
Reasoning: query logic lives in app/queries.py, separate from the
Streamlit UI in app/main.py, specifically so it's unit-testable without
a browser. tests/test_eligibility_query.py checks the eligibility rule
itself (every returned cutoff <= candidate percentile), monotonicity
(a higher percentile never returns fewer options), sort order, category
exactness, and the artifact-row exclusion — all against a real seeded +
ingested database, not fixtures. Smoke-tested app/main.py by actually
booting it headless (`streamlit run ... --server.headless true`) and
confirming HTTP 200 with no exceptions in the log, rather than only
checking that the file parses.
Trade-offs: results across all years/rounds are shown together unless a
year is picked — a given institute/program can appear multiple times
(once per year/round it had a cutoff at or below the candidate's
percentile). This is useful for the trend view but could look like
duplication in a plain eligibility list; worth adding a "latest round
only" toggle if that reads as noisy in practice.
Alternatives Rejected: Falling back to broader categories when the
requested one has no data (e.g. showing OPEN alongside SC) — rejected
as a silent behavior change a candidate could easily misread as "you
qualify" when they don't, under their own category.

## Institute city/website enrichment
Date: 2026-09-13
Status: Accepted
Context: The 3-input search needs a real city field on institutes,
which nothing in the CET CAP source PDFs provides directly. A
manually-compiled source CSV (college_code, college_name, city,
website; 989 rows) was supplied to fill this gap.
Decision: scripts/enrich_institutes.py UPDATEs (never INSERTs)
institutes.city / institutes.website, with three safeguards verified
against the actual file rather than assumed:
  1. 7 rows list TWO institution codes in one cell ("02668,02688") for
     what's the same physical college under separate CET codes (e.g.
     one per program family) — split into one row per code before
     joining, not dropped or truncated to the first code.
  2. After splitting, 27 codes had more than one source row. 25 of
     those agree on city (just differently-phrased names, or one row
     missing a website) and are safely collapsed, preferring a row
     that has a real website over one that doesn't. The remaining 2
     (institution_code 02672, 02684) genuinely DISAGREE on city between
     their two source rows — quarantined rather than guessing which is
     right.
  3. website == "Unknown" (52 rows) is stored as NULL, never as the
     literal string "Unknown", so the UI never renders a fake link.
  4. A name-similarity check (difflib ratio, normalized text) between
     each source row's college_name and the DB's institution_name for
     that code, catching the case where a code maps to the wrong
     college in the source file. Verified empirically that NONE of the
     current data trips this (minimum similarity found: 0.35, well
     above the 0.30 threshold) — this is a safety net for future
     updates to the source file, not evidence of an existing problem.
Reasoning: coverage is 967/969 institutes with a verified city (99.8%),
916/969 with a website — checked against the actual `institutes` table
after running, not just the script's printed count. Idempotent (UPDATE,
re-running is a clean no-op on unchanged input). schema.sql gained
`city`/`website` columns (nullable — NULL means "not enriched or
genuinely unknown", never faked as empty string); enrich_institutes.py
also ALTERs an existing older DB that predates these columns, so it
works whether the DB was created fresh or not.
Trade-offs: institution_code 02672 and 02684 (2 institutes, 0.2% of
969) have no city and will never appear in a city-filtered search until
someone manually resolves which of the two conflicting source rows is
correct.
Alternatives Rejected: Silently picking one of the two conflicting rows
per ambiguous code (e.g. "first one wins") — rejected because a wrong
city silently attached to a real college is worse than that college
being briefly unsearchable by city; the CITY_CONFLICT quarantine reason
is printed explicitly so it's a 2-minute manual fix, not a silent gap.

## 3-input search: city/percentage/course, list-then-detail UX
Date: 2026-09-13
Status: Accepted
Context: Product direction changed from the single eligibility-lookup
page (category required up front) to a 3-input search — City,
Percentage, Course only — with category deferred to a per-college
detail panel opened on click, not a separate page.
Decision:
  - No category on screen 1. Confirmed with the user: the list's
    headline "highest cutoff" number MIXES every category together
    (MAX percentile cleared, any category, per college) rather than
    defaulting to OPEN-only. Trade-off, stated explicitly to the user
    before building: a reserved-category cutoff can be a college's
    winning number on the list, which does NOT mean a general
    candidate can actually get that specific seat — only whoever holds
    that category can. The results caption says this plainly ("across
    every category — not just the category you belong to"), and the
    detail panel is where the real, category-specific answer lives.
  - City has no real database column (see "Institute city/website
    enrichment" above) — matching is a case-insensitive substring
    match against the enriched `city` field; institutes with no
    enriched city are excluded from a city-filtered search rather than
    guessed into matching.
  - "Course" means program_family only (BCA/BBA/MBA/MCA) — confirmed
    with the user, not the finer program_aliases.csv sub-classification
    already used elsewhere (see ingest.py's decision entry).
  - Architecture: src/cet_cap/search.py holds the pure pandas logic
    (filter_by_city, summarize_colleges) with zero DB access, so it's
    unit-testable with synthetic DataFrames
    (tests/test_search.py) independent of whether the underlying query
    or database changes. src/cet_cap/queries.search_cutoffs_for_course
    is the single "load everything" background fetch — one query per
    search, not filtered by city or category, covering every
    year/round/section/stage/category for the chosen course that the
    percentage clears. The detail panel (st.dialog, not a new page/URL)
    filters that SAME already-loaded frame down to one institution_code
    in pandas — no second cutoffs query per click. Seat-matrix data is
    the one thing that needs its own query on click
    (seat_matrix_for_institute), since seat capacity isn't part of the
    cutoffs data at all; it returns the most recent capture_year only
    (not summed across years) and keeps is_total rows separate from the
    per-category breakdown so the UI never double-counts a lane's
    capacity by accidentally summing the total with its own components.
Reasoning: tests/test_search.py (7 tests, synthetic data, no DB) pins
the aggregation rules directly: highest-cutoff-per-college,
years_on_record/matching_rows counts, sort order, city substring
matching, and that an unenriched (NULL city) row never matches a city
search. tests/test_search_end_to_end.py (5 tests, real seeded +
ingested + enriched DB) pins the actual queries.py<->search.py wiring:
the list-column shape matches exactly what screen 1 is allowed to show,
the "mixes categories" behavior is verified to actually happen (not
just claimed) by checking at least one college's winning row is a
non-OPEN category, the detail-panel data really is a pure filter of the
already-loaded frame, and the seat matrix's total-vs-breakdown
separation holds a real numeric invariant (a lane's total >= its
largest single category row) rather than just "some numbers came back".
Both app/Home.py boot states (screen 1 static widgets, and the
db-not-found error path) were smoke-tested by actually starting
Streamlit headless and checking for exceptions in the log, not just
that the file parses.
Trade-offs: same as the original eligibility page — a given college can
appear more than once across different searches’ underlying data if
run at different percentages, but within one summarize_colleges() call
it's always exactly one row per college by construction (tested
directly). The category-mixing trade-off above is the main one worth
someone occasionally re-confirming as the project's actual users are
observed.
Alternatives Rejected: Defaulting the headline number to OPEN-only
category (stricter, avoids the reserved-category-cutoff-looks-like-a-
guarantee risk) — the user explicitly chose category-deferred/mixed
after the trade-off was explained; documented here so it's a recorded
decision, not a default nobody chose on purpose.

## export_search_index.py: rank/percentile must come from the same row
Date: 2026-09-13
Status: Accepted
Context: The search-widget export (a static-site addition, alongside
`export_trends_json.py`) computed the "closing cutoff" for a
college+course+year by taking MIN(percentile) and MAX(rank_number) as
two SEPARATE aggregate functions within one GROUP BY. This mirrors
export_trends_json.py's own MIN(percentile)-collapses-stages logic —
but that script's grouping is per (year, round), where the pairing is
guaranteed (verified empirically: 0 mismatches across all 9,273
multi-row groups in the real data, since within one round, percentile
falling and rank rising track the same continuous fill-up process).
export_search_index.py collapses across MULTIPLE ROUNDS within a
year instead — independent admission events — and checking that same
pairing assumption against the real data found it broken in 184 of
2,597 multi-row groups (~7%): e.g. institution 01102/BCA/2026 exported
percentile=1.1857 (which really belongs to the row with rank_number=
7101) paired with rank=7432 from a completely different row. The
exported record described a rank and percentile that never actually
occurred together.
Decision: rewrote the query with a window function
(`ROW_NUMBER() OVER (PARTITION BY institution_code, program_family,
year ORDER BY percentile ASC, rank_number DESC)`) so the exported rank
always comes from the exact row that has the exported percentile, with
ties broken toward the higher rank_number for a deterministic choice.
Reasoning: checked real-world impact before deciding how urgent this
was — `site/index.html`'s `getStatus()` (which decides
safe/borderline/reach) only ever reads `percentile`, never `cutoff`
(rank), so the bug never affected eligibility classification. It only
affected the "Closing Rank" number shown next to "Closing Percentile"
in the detail panel and results table (site/index.html lines ~1656-
1657, ~1809-1810) — a real but purely cosmetic inconsistency, not a
wrong-eligibility bug. Fixed anyway since a displayed number that
doesn't match its own displayed percentile is still misleading.
Verified the fix directly: re-ran the export and checked every one of
the 1,433 records' (percentile, rank) pair against the real `cutoffs`
table — 0 mismatches (down from 184). Added
tests/test_export_search_index.py to pin this: it re-derives the
export from a fresh database and asserts every record's rank actually
co-occurred with its percentile in a real cutoffs row, plus the
1,433-record and 46-excluded-institutes counts the README already
claimed (both confirmed accurate independently).
Trade-offs: none identified — the window-function version is not
meaningfully slower (same data volume, one extra sort) and is strictly
more correct.
Alternatives Rejected: Leaving it as a "just cosmetic" bug since
eligibility logic wasn't affected — rejected because a page showing a
rank next to a percentile that were never actually paired together is
the kind of small trust-eroding inconsistency a careful reviewer (or
a sharp student) would notice and reasonably wonder what else is
wrong with the data.

## Self-contained offline build: inline the data into index.html
Date: 2026-09-13
Status: Accepted
Context: `site/index.html` was built to load its data via dynamically-
created `<script src="data/...">` tags specifically to avoid the
`fetch()`-under-`file://` restriction — verified working from a
desktop machine. A user without a PC (phone-only, Chrome) opened the
extracted zip's `site/index.html` directly and got empty City/Course
dropdowns. The dynamic-`<script>` workaround, while correct for the
"double-click on desktop Chrome" case, doesn't cover every mobile
browser's file-access restrictions, and — critically — `loadScriptOnce`
failed silently on error (logged to devtools console only), so a user
without easy access to devtools had no way to know what went wrong.
Decision: made `index.html` fully self-contained. The content of
`data/search_index.js` and all 4 `data/cutoffs_<FAMILY>.js` files is
inlined directly as `<script>` blocks at the top of the page, so
`window.__SEARCH_INDEX__`/`window.__CUTOFF_DATA__` are populated before
any app logic runs — no file load of any kind happens for the common
case. `loadScriptOnce` was extended with an optional `alreadyLoaded()`
check: if the data it would load is already present (true for this
build), it resolves immediately without creating a `<script>` element
at all. This keeps the same function working unchanged for a
server-hosted deployment (where the separate `data/*.js` files are
still generated and still used) — the inlined build and the
served-from-files build share the exact same JS logic.
Reasoning: Verified with Node + jsdom (a headless DOM implementation)
rather than assumed: loaded the assembled index.html with
`resources: undefined` (no external resource loading permitted at
all — deliberately worse than any real file:// restriction, to
prove it works even so), and confirmed `citySelect` populates with
162 real city options and `courseSelect` with all 4 real courses,
and that `window.__CUTOFF_DATA__` contains all 4 program families
with their real group counts (4,629–7,162 groups each). This is a
stronger test than "trust the code reads correctly" — it actually
executes the page's JavaScript and inspects the resulting DOM state.
Trade-offs: `index.html` grows from ~140KB to ~6.2MB (the inlined
JSON). Page-load parse time increases correspondingly, though this is
still fast on a modern phone (data is static, no rendering work
happens until the user interacts). The separate `site/data/*.json`
files are still generated by the export scripts and left in place —
useful for anyone who does deploy this behind a real server and would
rather keep `index.html` small and fetch data separately.
Alternatives Rejected: Telling every user to run a local server (the
first fix attempted) — works, but requires a computer and a terminal,
which not everyone doing a live demo has in the moment. A
fully self-contained file has zero preconditions: email it, AirDrop
it, open it from any file manager, on any device.

## 2026-09-16 — Per-college precomputed cutoff payloads

The master SQLite database remains a build-time source of truth and is never placed under `site/`. The static site now has `site/data/college_cutoffs/<COURSE>/<COLLEGE>.js` payloads generated from the verified cutoff exports. A drawer loads only the selected college's payload, rather than a 2–6 MB course-wide cutoff file. This removes runtime aggregation/reconstruction from the drawer and materially reduces initial drawer data transfer while preserving every raw `pdf_rows` value needed for the PDF-style display. Publicly displayed cutoff values are still intentionally public; this design protects the private DB/internal data rather than claiming the visible values are secret.
