# Interactive Cutoff-Trends Graph — Product & Technical Spec

**Target location:** the `id="results"` section ("Step 02 — Your eligible colleges") of the site. This is the second `.section` block after the hero, and it currently holds only the eligible-colleges table. The chart is a new widget added to that section, above or below the table — it does not replace the per-college gap chart already inside each drawer.

## Assumptions (stated up front, since they shape the whole design)

1. **Data source.** The live site (`deepseek_html_20260913_431815.html`) is a static HTML/JS file with all data hardcoded in `CONFIG`. The real data lives in the `cet-cap-portal` SQLite/Postgres schema (`cutoffs`, `programs`, `institutes`, `base_categories`, `sections`). I'm assuming the graph will be fed by a **pre-exported JSON file** built from that DB (a script using `cet_cap.queries`-style SQL, run at data-refresh time), not a live API call per filter change — this matches the site's current "static file, no backend" architecture. If you're planning an actual backend/API for the site, the querying logic below still applies, just server-side instead of client-side; say so and I'll adjust.
2. **"Quota/Type"** = the `section_code` dimension (`HU`, `OHU`, `HU_TO_OHU`, `OHU_TO_HU`, `SL`, `MI-MIN`, `MI-NONMIN`) — this is what produces combinations like Home–Home (HU) and Other–Other (OHU).
3. **"Category"** = `base_category` (`OPEN`, `SC`, `ST`, `OBC`, `EWS`, `TFWS`, `PWD` variants, etc.), optionally crossed with the `is_ladies` flag.
4. **"Round"** = the `round` integer (C1–C4, parsed from filename), not `stage_code` — `stage_code` is blank in a large share of source files and isn't reliable enough to chart on.
5. **Scope of one chart** = one program family (course) + one institute + one category + one quota/type at a time, with round as either separate lines or a toggle (see §1). Comparing across colleges is a separate widget (§5), not the same chart, to keep each view honest about what it's showing.

If any of these don't match what you intended, flag it before I go further — everything below is built on them.

---

## 1. Handling missing data

The core rule: **the chart only ever draws what exists, and always tells you what it's not showing.**

- **Query first, chart second.** For the selected (category, quota, course, college) tuple, query all `(year, round)` cutoff rows that exist. Never assume a year or round belongs on the axis — derive the x-axis from the actual distinct years present in the filtered result, not from a hardcoded year list (unlike `CONFIG.historyYears` in the current mock, which is a fixed array — that pattern will silently show a flat/fabricated line for years with no data if reused here).
- **Gaps, not zeros or interpolation.** A year with no row for that exact combination is a **gap in the line**, never a 0, never a straight line drawn through it. Zero looks like "cutoff was 0" (a real, catastrophic-looking value); interpolation fabricates a trend that never happened. Concretely: don't feed the charting layer a dense array with nulls-as-zero; feed it only the points that exist, and if using a library, use its explicit "skip null" / `spanGaps: false` option rather than relying on default behavior.
- **Rounds as separate series, not merged.** Round 1–4 cutoffs for the same year are genuinely different quantities (later rounds fill from a different applicant pool). Default view: **one line per round** (up to 4 lines), each only plotting the years/rounds where that round actually has data for this combination. A round with data in only 1 of 4 years shows as a single point, not a line — a lone point is still meaningful and shouldn't be hidden just because it can't form a line.
- **Round toggle, not forced overlay.** Give the user checkboxes to show/hide individual rounds (all on by default). If a round has zero data across every year for the current filter combo, don't render its checkbox at all — an empty toggle that does nothing is worse than no toggle.
- **No silent aggregation across rounds by default.** Averaging or taking min/max across rounds hides real information (e.g. a college that only fills in round 3–4 looks identical to one that closes in round 1). Offer a **"Final cutoff (last round each year)"** view as an explicit, separately-labeled option rather than the default — see §5 for why this is still a useful widget on its own.
- **Sparse combinations get a distinct empty state**, not a chart with one dot on it that looks broken. Threshold: if the filtered result has 0 rows → empty state (§4). If it has 1–2 points total across all rounds/years → still draw the chart (real data, however thin) but show a small inline note ("Limited data: 2 data points available") so the user doesn't mistake sparse-but-real for a rendering bug.
- **Never let one filter combination's absence imply the college doesn't accept that category.** This mirrors the open issue already logged for the search/eligibility side of this project: an institute can legitimately admit a category with no historical cutoff row for it (e.g. no candidate applied in that lane, not "seats don't exist"). The chart and any surrounding copy should say **"no cutoff data on file for this combination"**, never **"not offered"** or **"not applicable"** — the data layer doesn't have positive confirmation either way, and the seat-matrix table (`seats`, `allocation_lanes`) is a better source for "does capacity exist" if that's ever needed.

## 2. Category and Quota/Type filter design

- **Two dependent dropdowns, not independent ones.** Populate Category from the distinct `base_category` values that actually have at least one cutoff row for the currently selected course (and college, if already chosen) — not the full `category_whitelist.csv` (~20+ codes), most of which won't apply to any given course/college. Same for Quota/Type against `section_whitelist.csv`. This keeps users from selecting a combination that's guaranteed empty.
- **Cascading, not simultaneous, population.** Order: Course → College → Category → Quota/Type. Each downstream dropdown re-queries its available options whenever an upstream one changes, and clears/reflows its own selection if the current one is no longer valid (e.g. switching course away from one where "PWD-Open" had data). Show a brief "updating…" state during the re-filter rather than a jarring instant wipe.
- **Grouped, not flat, category list.** Use `category_group` from `category_whitelist.csv` (Open / Reserved / Special / Minority) as `<optgroup>` labels so a ~15–20 item dropdown doesn't read as an undifferentiated wall of acronyms. Same idea for quota/type: group Home-University-family codes (HU, OHU, HU_TO_OHU, OHU_TO_HU) separately from State Level and Minority lanes.
- **Full names on hover/label, codes in the compact view.** `OPEN` / `HU` are fine as the default label given limited space, but show `category_full` / `section_full` (e.g. "Home University Seats Allotted to Other Than Home University Candidates") as a tooltip or in a searchable-dropdown's expanded row — several of these codes are genuinely not self-explanatory (`HU_TO_OHU` vs `OHU_TO_HU` in particular).
- **Searchable, not paginated**, given the category list alone can run 15–20+ entries. A plain `<select>` with optgroups is an acceptable v1 if you want to stay dependency-free (matches the current site's zero-JS-library style); a typeahead combobox is the nicer v2.
- **Ladies-only as a separate toggle, not folded into Category.** `is_ladies` is its own boolean column, not a `base_category` value — expose it as a checkbox ("Ladies seats only") next to the Category dropdown rather than doubling the category list with "OPEN (Ladies)" pseudo-entries.
- **Empty-combination handling in the dropdown itself:** if a Category/Quota pairing has zero rows for the current Course+College, either (a) grey it out with a "no data" suffix rather than removing it (so users understand it's a real code that just doesn't apply here, not a typo), or (b) omit it entirely from the list. Given how sparse some combinations are (per your data constraints), I'd lean toward (b) for Category/Quota and reserve (a) only for Course/College, where users are more likely to expect a specific college to be there and be confused by its silent disappearance.

## 3. Data processing logic

Two layers: a **build-time export** (Python, against the existing schema) and a **client-time filter** (JS, against the small pre-filtered JSON it receives). Doing the heavy join once at build time, not per page load, is what keeps the static site fast and keeps the client-side code simple enough to reason about for correctness.

**Build-time (Python, reusing the existing query style in `cet_cap/queries.py`):**

```sql
SELECT
    c.year, c.round, c.base_category, c.is_ladies, c.section_code,
    p.program_family, p.program_id, p.program_name_raw,
    c.institution_code, i.institution_name,
    c.percentile AS cutoff_percentile,
    c.rank_number AS cutoff_rank
FROM cutoffs c
JOIN programs p   ON p.program_id = c.program_id
JOIN institutes i ON i.institution_code = c.institution_code
WHERE NOT (c.rank_number = 0 AND c.percentile = 0.0)   -- reuse ZERO_CUTOFF_ARTIFACT_FILTER
ORDER BY p.program_family, c.institution_code, c.base_category, c.section_code, c.year, c.round
```

- Group the result by `(program_family, institution_code, base_category, is_ladies, section_code)` — that's the exact tuple the two dropdowns plus course/college selection narrow down to. Each group becomes one JSON record: `{ course, college_code, category, is_ladies, quota, points: [{year, round, percentile, rank}] }`.
- **Don't pre-fill missing years/rounds with nulls in the export** — only emit points that exist. Let the client derive its own axis from whatever's present; this is what makes gaps (§1) automatic rather than something the frontend has to detect and special-case.
- Emit a **separate small index file** (or a header section in the same JSON) listing, for each course, which colleges/categories/quotas have *any* data — this is what powers the cascading-dropdown filtering in §2 without the client needing to scan every point.
- One export per course (program_family) if the combined file gets large (four courses × several thousand institute/category/quota/year/round rows could add up) — course is already the first thing the user picks, so it's a natural split point for lazy-loading only the file you need.

**Client-time (JS):**

1. On Course change → fetch (if not cached) that course's JSON file.
2. Filter to selected College (if chosen) → build the set of Categories/Quotas with data (§2).
3. On Category + Quota selection → pull the matching group's `points` array.
4. Bucket `points` by `round` → up to 4 arrays, each sorted by `year`.
5. Hand each round's array to the chart as its own series, with **no gap-filling** — the chart plots exactly the (year, percentile) pairs each round actually has.


### Drawer-specific display rule

The college drawer uses Category and Quota/Type differently from the
page-level trend widget. Course is already fixed by the Step-01 result and is
not shown again. Category and Quota/Type are populated only from combinations
that exist in the selected college's cutoff data, but once selected they act
as **highlight controls**, not table filters. The drawer displays all cutoff
columns/sections/stages present for that college and selected Ladies state for
the chosen year, using the raw PDF category labels and highlighting the
selected category + quota intersection. A separate `pdf_rows` export preserves
raw rows for this reconstruction; the normalized `groups` export remains the
source for the filterable trend widget.

## 4. UX for empty / partial / incomplete states

| State | What's shown |
|---|---|
| No selection made yet | Chart area shows a neutral placeholder ("Pick a category and quota to see the trend") — never an empty axis with no explanation. |
| Valid selection, zero data | Replace the chart with a short message: *"No cutoff data on file for [Category] · [Quota] · [Course]."* Suggest the nearest available alternative if easy to compute (e.g. "Data is available for OPEN · HU for this course"). |
| Valid selection, 1–2 points only | Draw the chart (real data), but add a small inline caption noting how few points are available, so sparsity reads as "the truth" rather than "the page is broken." |
| Valid selection, some rounds missing for some years | Normal case — each round's line simply doesn't extend into years it lacks; round toggle checkboxes reflect exactly which rounds have any data at all for this combination. |
| Selection changes while loading | Show the axis/legend skeleton in a muted state rather than blanking to white, so it doesn't read as an error. |

## 5. Additional insights/widgets for the same section

All of these reuse the same filtered dataset, so they're cheap to add once §3's grouping exists:

- **Highest / lowest cutoff badges** for the current selection (e.g. "Best: 99.55 (2024, R1)" / "Lowest: 96.10 (2022, R4)").
- **Year-over-year delta** — small up/down arrow + percentile-point change between the two most recent years that both have data (skip years silently if one side is missing, don't compute a delta across a gap).
- **Round-wise closing trend** — a compact row showing R1→R2→R3→R4 for the most recent year only, useful for "how much does it loosen up by the last round" at a glance, independent of the multi-year chart.
- **"Final cutoff per year"** mini-view — last available round each year, as a single line; explicitly labeled as such (this is the aggregation flagged as opt-in in §1, not the default).
- **College comparison overlay** — same category/quota/course, multiple colleges as separate lines on one chart. Cap the count you allow at once (e.g. 4–5) so it doesn't turn into unreadable spaghetti, and apply the same no-fabrication rule per college.
- **Cutoff range band** — min–max percentile across all colleges for the selection, as a shaded band behind an individual college's line, giving instant context ("is this college near the top or bottom of the range").

## 6. Implementation recommendations

- **Chart type:** multi-line time series, x = year, one line per round (or per college in the comparison view). Line charts communicate trend-over-time far better than bars for this data, and make gaps visually obvious as literal breaks in the line rather than something you have to infer from bar absence.
- **Library vs. hand-rolled:** the existing per-college gap chart in the drawer is hand-rolled CSS/divs with no library, which keeps the site dependency-free. For a genuine multi-series line chart with gaps, per-point tooltips, and a round toggle, a small library earns its weight — recommend **Chart.js** (via `cdnjs.cloudflare.com`, one `<script>` tag, no build step, so it fits the site's current zero-tooling approach) with `spanGaps: false` and `null` for missing points rather than `0`. If you'd rather stay at true zero dependencies, a hand-rolled SVG line chart is very doable given you're already comfortable with the div-based bar chart's math — it's more code to maintain but keeps full control over exactly how gaps render.
- **Data schema (client-side JSON shape):**
  ```json
  {
    "course": "MCA",
    "groups": [
      {
        "college_code": "0001",
        "college_name": "VJTI",
        "category": "OPEN",
        "is_ladies": false,
        "quota": "HU",
        "points": [
          { "year": 2022, "round": 1, "percentile": 99.10 },
          { "year": 2023, "round": 1, "percentile": 99.20 },
          { "year": 2024, "round": 2, "percentile": 98.80 }
        ]
      }
    ]
  }
  ```
- **State management:** this doesn't need a framework — the existing site is vanilla JS with a global `CONFIG` object and direct DOM manipulation, so a plain module-level state object (`{course, college, category, quota, isLadies, visibleRounds}`) with a single `renderChart()` function that re-reads it and re-draws is consistent with the current code style and easiest to keep the drawer's separate gap-chart code from interfering with.
- **Caching:** cache each course's fetched JSON in a JS `Map` keyed by course, so switching Category/Quota within the same course never re-fetches.

---

Anything above you want changed before this goes into code — in particular, confirm the data-export assumption in point 1, and whether you want a full college-comparison overlay in v1 or just the single-college trend to start.
