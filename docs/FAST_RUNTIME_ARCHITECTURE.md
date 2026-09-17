# Fast Runtime Data Architecture

The SQLite database remains the single source of truth and is never copied into `site/`.

## Runtime flow

1. The build pipeline reads the verified SQLite database.
2. `export_search_index.py` creates the compact search index and compact college-specific drawer metadata.
3. `export_trends_json.py` creates a build-only intermediate export outside `site/`.
4. `export_course_year_data.py` converts that verified export into tiny `course/year/college.js` files.
5. `sync_inline_search_index.py` makes the copy embedded in `site/index.html` identical to the generated search index.
6. The intermediate course-wide JSON is deleted before the build finishes.

## Browser behavior

The browser does **not** load a course-wide cutoff dataset when opening a college drawer.

For example, MBA college `06307` uses:

`site/data/courses/MBA/2026/06307.js`

The Category and Quota/Type controls are populated immediately from compact metadata already in the search index. The full cutoff rows are loaded only for the selected college and selected year.

Switching from 2026 to 2025 loads only the 2025 file for that college.

## Accuracy rule

The generated college/year files are not hand-edited. They are generated from the verified database export and checked against the SQLite cutoff rows. Any missing, extra, or altered cutoff row should fail validation before deployment.

## Public data boundary

The master SQLite database and build-only course-wide cutoff JSON files are kept outside `site/`. The public site contains only the data needed for its read-only UI.
