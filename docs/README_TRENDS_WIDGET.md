# Cutoff Trends widget — what's in this delivery

This adds the interactive "cutoff trends across years" chart to the
`id="results"` section (Step 02) of the site, built against your **real
ingested CAP cutoffs data** — not the mock `CONFIG.colleges` list already
in that file, which is left untouched.

## Files

- `site/index.html` — your original page, with the trends widget added
  (new CSS in "SECTION 11B", new markup inside `#results`, new JS module
  `Trends` near the bottom of the `<script>` block).
- `site/data/cutoffs_{BBA,BCA,MBA,MCA}.json` — one file per course,
  pre-exported from the DB. Each holds every (branch, college, category,
  ladies, quota) combination that has at least one real cutoff row, with
  only the years/rounds that actually exist for it.
- `site/data/cutoffs_{BBA,BCA,MBA,MCA}.js` — the same data, wrapped so
  it assigns onto `window.__CUTOFF_DATA__` instead of being plain JSON.
  This is what `index.html` actually loads (via `<script src>`, not
  `fetch()`) — see "Running it" below for why. `export_trends_json.py`
  writes both automatically.
- `site/data/manifest.json` — a small summary of what's in each file
  (group/college counts), for your own reference.
- `scripts/export_trends_json.py` — regenerates the JSON files from
  `db/cet_cap.db` whenever you re-ingest new data. Run from your project
  root: `python scripts/export_trends_json.py --db db/cet_cap.db --out site/data`
- `docs/cutoff-trends-graph-spec.md` — the spec this was built from.

## Running it

Just open `site/index.html` directly — double-click it, or drag it into
a browser tab. The chart loads its data via `<script src="data/cutoffs_*.js">`
tags (not `fetch()`), so it isn't affected by the browser's usual block
on `fetch()` of local files under `file://`. No local server needed.

## What I actually had to build to get real data

Your zip had the raw per-round CSVs and the ingest scripts, but no
built database yet, so I ran your own pipeline rather than re-deriving
the parsing logic myself:

```
python scripts/seed_reference_tables.py --db db/cet_cap.db
python scripts/ingest.py data/raw/ --db db/cet_cap.db
```

67,769 of 67,770 rows ingested cleanly (1 skipped — the known
rank=0/percentile=0.0 artifact your `queries.py` already filters out
elsewhere).

**One real data wrinkle the original spec didn't anticipate:** a single
`(year, round)` can carry more than one `stage_code` (Stage-I, Stage-II,
...) — supplementary allotment stages within the same CAP round, present
in 10–21% of (year, round) groups depending on course. Percentile
reliably decreases stage-over-stage as more seats fill. The export takes
the **minimum percentile per (year, round)** as that round's closing
cutoff (the number a candidate actually needed by the end of the round),
rather than plotting every stage as its own point. This is documented in
`export_trends_json.py`'s module docstring — worth a look if a chart
value ever looks off against a source PDF, since it means the chart
shows the *final* value for a round, not necessarily every intermediate
one.

## What's in v1 vs. deferred

Built: Course → Branch → College → Category (+ Ladies toggle) → Quota/Type
cascading filters, per-round lines with real gaps (never fabricated),
a toggleable "final cutoff per year" overlay, and four insight badges
(highest, lowest, year-over-year, data-point count) — all per §1–§5 of
the spec.

Deferred (flagged in the original spec as optional / v2): the
multi-college comparison overlay and the cutoff-range band. Both reuse
the same JSON shape, so they're a follow-up rather than a rebuild if you
want them next.

## Drawer cutoff-table behavior (current)

The per-college drawer has a different purpose from the page-level trends
widget. The Course/Specialization is already fixed by the Step-01 result, so
there is no duplicate Course selector in the drawer.

Inside the drawer, **Category and Quota/Type are selection/highlight controls,
not table filters**. Their options are still populated only from real cutoff
rows for that exact college/course (and the Ladies toggle state). After the
user chooses a category and quota/type, the drawer shows **all cutoff lines
for that college for the selected year**, including the other categories and
quota sections that exist in the source data. The selected category + quota
intersection is highlighted.

The export therefore carries two deliberately separate data layers:

- `groups`: the normalized per-category/per-quota trend data used by the
  filterable trends widget.
- `pdf_rows`: raw cutoff-row data (`stage`, `round`, `raw_category`, rank and
  percentile) used only to reconstruct the college-specific PDF-style tables.

This separation is intentional. It prevents the category/quota filter from
accidentally deleting the other columns that were present in the original
CAP cutoff PDF.
