# Project Rules

## General
- Python 3.11+. No other languages for core logic.
- No feature without a task or spec entry.
- Modular code. One responsibility per file.

## Code Organization
- scripts/ = runnable ETL and diagnostics.
- src/cet_cap/ = importable library code.
- app/ = Streamlit UI only.

## Naming Conventions
- snake_case for functions and variables.
- PascalCase for classes.
- UPPER_SNAKE for constants.
- Category codes verbatim uppercase (GOPEN, SC, PWDROBC).

## Type Safety
- Type hints on every function signature.
- Use `from __future__ import annotations` where helpful.

## Error Handling
- Never swallow exceptions silently.
- Every ingest row failure logged to ingest_errors.
- Every quarantined extraction anomaly logged to ingest_errors too
  — quarantining is not the same as silently dropping.
- UI errors shown to user via st.error, never traceback.

## SQL & Database Rules
- NEVER build SQL with f-strings.
- Always use SQLAlchemy text() with :param bindings.
- Writes go through staging first.
- Every load idempotent (ON CONFLICT DO NOTHING).

## Security Rules
- Parameterised SQL only.
- Secrets in .streamlit/secrets.toml, never committed.
- Sentry send_default_pii=False.
- Never log user percentile, category, section.

## Privacy Rules
- No user input stored server-side.
- No analytics that report filter values.
- Streamlit gatherUsageStats = false.

## Dependency Rules
- Pin exact versions.
- Justify additions in DECISIONS.md.

## UI Rules
- Percentile input: reject outside [0, 100].
- All filters from controlled dropdowns.
- Cache DB reads with @st.cache_data(ttl=300).
- Provide "Refresh data" button.

## Boolean Handling
- Use SQLAlchemy Boolean type for cross-dialect compatibility.
- Raw SQL: 1/0 on SQLite, TRUE/FALSE on Postgres.

## Reference Data Rules
- Never add a code to a whitelist (category/section/stage/program)
  without inspecting real source rows for it first — institute
  name, rank, and percentile should all look like a plausible
  candidate record. If a value looks like leaked status/institution
  text instead of a code, it goes in `KNOWN_BAD_CATEGORY_VALUES`
  (or an equivalent quarantine list), not the whitelist.
- Never forward-fill a column without first checking it isn't
  legitimately blank for an entire file — check the % of blank
  rows per file before assuming a per-block continuation pattern.

## Testing Rules
- parse_category must have a unit test per case.
- Every bug fix gets a regression test.

## Git Rules
- Small commits, imperative messages.
- Never commit data/raw/ or secrets.
- Branch per feature.

## Performance Rules
- Query < 1s on local SQLite.
- No N+1 query loops in Streamlit.
- Use materialised views for latest-cutoff lookups.

## Forbidden Practices
- No vibe coding.
- No f-string SQL.
- No logging of user filters.
- No SQLite in production.
- No schema changes outside db/schema.sql.
