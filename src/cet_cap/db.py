"""
src/cet_cap/db.py — Engine/connection helper for the query layer.
See docs/ARCHITECTURE.md "Application Layers" (Query: src/cet_cap/db.py,
queries.py) and "API Architecture" (direct DB access from Streamlit via
SQLAlchemy with parameterised queries — not raw sqlite3/psycopg2 calls).

Dev uses a local SQLite file; prod (per ARCHITECTURE.md) points this at
a PostgreSQL DATABASE_URL instead — same engine interface either way, so
queries.py doesn't need to know which one it's talking to.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine


def get_engine(db_path_or_url: str) -> Engine:
    """`db_path_or_url` is either a bare filesystem path to a SQLite
    file (dev) or a full DATABASE_URL (prod, e.g. postgresql://...)."""
    if '://' in db_path_or_url:
        url = db_path_or_url
    else:
        url = f'sqlite:///{db_path_or_url}'
    engine = create_engine(url)
    if url.startswith('sqlite'):
        with engine.connect() as conn:
            conn.exec_driver_sql('PRAGMA foreign_keys = ON')
    return engine
