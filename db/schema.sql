-- CET CAP Decision Support — Schema v1.0 (final)
-- SQLite (dev) / PostgreSQL (prod)

PRAGMA foreign_keys = ON;

-- ============================================================
-- REFERENCE
-- ============================================================

CREATE TABLE IF NOT EXISTS institutes (
    institution_code   TEXT PRIMARY KEY,
    institution_name   TEXT NOT NULL,
    home_university    TEXT,
    institute_type     TEXT,
    affiliation_status TEXT,
    -- city/website: NULL by default (seed_reference_tables.py never sets
    -- these), populated by scripts/enrich_institutes.py from a manually
    -- supplied city/website source — see that script's docstring. NULL
    -- (not '') for "not enriched yet or genuinely unknown", so a UI can
    -- tell "we don't know" apart from "known to be blank".
    city                TEXT,
    website             TEXT,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS programs (
    program_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    program_family     TEXT NOT NULL,
    program_name_raw   TEXT NOT NULL,
    level              TEXT,
    UNIQUE(program_family, program_name_raw)
);

CREATE TABLE IF NOT EXISTS base_categories (
    base_code      TEXT PRIMARY KEY,
    category_full  TEXT,
    category_group TEXT
);

CREATE TABLE IF NOT EXISTS sections (
    section_code TEXT PRIMARY KEY,
    section_full TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stages (
    stage_code TEXT PRIMARY KEY
);

-- Seat-matrix quota lane. Distinct from `sections` (HU/OHU/SL): the
-- seat-matrix source only exposes 5 flat lanes (HU, OHU, SL, PWD, DEF)
-- with no HU/OHU split for PWD/DEF, whereas `cutoffs.section_code`
-- captures a finer HU/OHU/SL split even for PWD/DEF categories. See
-- docs/DECISIONS.md "Seat matrix: new allocation_lane dimension".
CREATE TABLE IF NOT EXISTS allocation_lanes (
    lane_code TEXT PRIMARY KEY,
    lane_full TEXT NOT NULL
);

-- ============================================================
-- FACT
-- ============================================================

CREATE TABLE IF NOT EXISTS cutoffs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    year             INTEGER NOT NULL,
    round            INTEGER NOT NULL,
    institution_code TEXT NOT NULL REFERENCES institutes(institution_code),
    program_id       INTEGER NOT NULL REFERENCES programs(program_id),
    base_category    TEXT NOT NULL REFERENCES base_categories(base_code),
    is_ladies        BOOLEAN NOT NULL DEFAULT 0,
    section_code     TEXT NOT NULL REFERENCES sections(section_code),
    stage_code       TEXT NOT NULL REFERENCES stages(stage_code),
    home_university  TEXT,
    rank_number      INTEGER,
    rank_suffix      TEXT,
    percentile       REAL NOT NULL,
    raw_category     TEXT NOT NULL,
    raw_program_name TEXT NOT NULL,
    source_pdf       TEXT NOT NULL,
    source_page      INTEGER,
    ingested_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(year, round, institution_code, program_id,
           base_category, is_ladies, section_code, stage_code,
           rank_number, percentile)
);

CREATE INDEX IF NOT EXISTS idx_cutoffs_filter
    ON cutoffs(year, round, program_id, base_category,
               is_ladies, section_code);

CREATE INDEX IF NOT EXISTS idx_cutoffs_inst_prog
    ON cutoffs(institution_code, program_id);

CREATE INDEX IF NOT EXISTS idx_cutoffs_percentile
    ON cutoffs(percentile DESC);

-- Sanctioned-intake seat matrix (capacity), separate from `cutoffs`
-- (which records the rank/percentile that got allotted). One row per
-- institution + program (choice_code) + quota lane + category
-- (+ gender where disclosed). `choice_code` joins to
-- `cutoffs.program_code` (same CAP option-code format).
--
-- `is_total` rows are the source PDF's own per-lane subtotal
-- ("Total" category) — they are NOT a real reservation category and
-- must be excluded from any SUM(seats) rollup by category, or seats
-- will be double-counted. Kept for cross-validation instead of
-- silently dropped (see docs/DECISIONS.md).
CREATE TABLE IF NOT EXISTS seats (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_year        INTEGER NOT NULL,
    program_family      TEXT NOT NULL,
    institution_code    TEXT NOT NULL REFERENCES institutes(institution_code),
    choice_code         TEXT NOT NULL,
    allocation_lane      TEXT NOT NULL REFERENCES allocation_lanes(lane_code),
    base_category       TEXT REFERENCES base_categories(base_code),
    is_total            BOOLEAN NOT NULL DEFAULT 0,
    is_ladies           BOOLEAN,
    seats               INTEGER NOT NULL,
    raw_category        TEXT NOT NULL,
    raw_allocation_type TEXT NOT NULL,
    -- NOT NULL, default '' rather than nullable: SQL treats NULL as
    -- distinct from NULL in a UNIQUE constraint, so if this were
    -- nullable, every row with unspecified gender (43,431 of 70,269 —
    -- PWD/DEF/HU/OHU/State-Level lanes routinely carry no gender split)
    -- would bypass the UNIQUE key entirely and re-ingesting the same
    -- file twice would duplicate all of them. See docs/DECISIONS.md
    -- "seats UNIQUE constraint missing program_family" (same entry).
    raw_gender          TEXT NOT NULL DEFAULT '',
    source_pdf          TEXT NOT NULL,
    source_page         INTEGER,
    ingested_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- program_family is part of the key: choice_code is NOT unique
    -- across program families on its own (the same choice_code string
    -- has been observed reused between e.g. BCA and MBA seat-matrix
    -- files with genuinely different seat counts) — see
    -- docs/DECISIONS.md "seats UNIQUE constraint missing program_family".
    UNIQUE(capture_year, program_family, institution_code, choice_code,
           allocation_lane, raw_category, raw_gender)
);

CREATE INDEX IF NOT EXISTS idx_seats_lookup
    ON seats(institution_code, choice_code, capture_year);

CREATE INDEX IF NOT EXISTS idx_seats_category
    ON seats(base_category, allocation_lane, is_total);

-- ============================================================
-- STAGING & AUDIT
-- ============================================================

CREATE TABLE IF NOT EXISTS staging_cutoffs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    raw_row     TEXT NOT NULL,
    loaded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed   BOOLEAN DEFAULT 0,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file    TEXT NOT NULL,
    rows_read      INTEGER,
    rows_inserted  INTEGER,
    rows_skipped   INTEGER,
    -- See seats_ingest_log.rows_duplicate for why this exists as its own
    -- column rather than being folded into rows_inserted.
    rows_duplicate INTEGER DEFAULT 0,
    rows_failed    INTEGER,
    ingested_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ingest_errors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file  TEXT NOT NULL,
    source_page  INTEGER,
    source_row   INTEGER,
    raw_category TEXT,
    raw_program  TEXT,
    reason       TEXT,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS staging_seats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    raw_row     TEXT NOT NULL,
    loaded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed   BOOLEAN DEFAULT 0,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS seats_ingest_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file    TEXT NOT NULL,
    rows_read      INTEGER,
    rows_inserted  INTEGER,
    rows_skipped   INTEGER,
    -- Rows that raised no error and weren't quarantined, but whose
    -- INSERT OR IGNORE was a no-op because the row already existed
    -- under the `seats` UNIQUE key (see that table's comment). Tracked
    -- separately from rows_failed because sqlite3 doesn't raise for
    -- this — it has to be detected via cursor.rowcount, and silently
    -- folding it into "inserted" (as an earlier version of this script
    -- did) hid a real bug where cross-family rows were colliding and
    -- being dropped.
    rows_duplicate INTEGER DEFAULT 0,
    rows_failed    INTEGER,
    ingested_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS seats_ingest_errors (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file         TEXT NOT NULL,
    source_page         INTEGER,
    institution_code    TEXT,
    choice_code         TEXT,
    raw_allocation_type TEXT,
    raw_category        TEXT,
    reason              TEXT,
    ingested_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
