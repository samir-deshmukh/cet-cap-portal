# AGENTS.md

## Project Overview
CET CAP Decision Support — parses official State CET admission PDFs,
stores cutoffs in PostgreSQL, serves a Streamlit app for candidates.

## Architecture Overview
See ARCHITECTURE.md.

## Repository Structure
- data/reference/  → whitelists (committed)
- data/raw/        → source CSVs (gitignored)
- scripts/         → ETL and validation
- db/schema.sql    → database DDL
- src/cet_cap/     → query layer
- app/             → Streamlit app
- docs/            → this and other docs

## Development Commands
- `python -m venv .venv && source .venv/bin/activate`
- `pip install -r requirements.txt`
- `python scripts/validate_data.py data/raw/`
- `python scripts/ingest.py data/raw/`
- `streamlit run app/Home.py`

## Build Commands
None — pure Python.

## Testing Commands
- `pytest`

## Code Conventions
- Python 3.11+, PEP8, 4-space indent.
- Type hints on all functions.
- One module = one responsibility.
- No global mutable state.

## Architecture Conventions
- ETL: extract → staging → validate → load.
- UI: queries only through src/cet_cap/queries.py.
- Never bypass SQLAlchemy for writes.

## Database Conventions
- All schema changes via db/schema.sql and Alembic (later).
- Never change schema by hand in production.
- Always parameterise SQL.

## API Conventions
No external API.

## Security Requirements
- Never commit secrets.
- Sentry send_default_pii=False.
- Never log user percentile/category/section.
- Parameterised SQL only.

## Dependency Guidelines
- Pin exact versions in requirements.txt.
- No new dependency without a reason in DECISIONS.md.

## Testing Requirements
- Unit tests for parse_category.
- Integration test for ingest on a small fixture CSV.
- No test may touch the production DB.

## Verification Checklist
Before any commit:
- validate_data.py exits 0.
- pytest passes.
- No secrets in git status.
- No raw data files staged.

## Forbidden Changes
- Do not rename files in data/reference/.
- Do not modify db/schema.sql without updating ARCHITECTURE.md.
- Do not remove indexes.
- Do not add a category/program/section value to a whitelist
  without first inspecting its source rows (institute name, rank,
  percentile) to confirm it is real data and not an extraction
  artifact — see docs/DECISIONS.md for the standard this project
  now holds itself to.

## Additional Documentation
- docs/PRD.md, docs/ARCHITECTURE.md, docs/RULES.md,
  docs/DECISIONS.md, docs/TESTING.md
