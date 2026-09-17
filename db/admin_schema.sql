PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS admin_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('SUPER_ADMIN','DATA_ADMIN','REVIEWER','READ_ONLY')), is_active INTEGER NOT NULL DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_login_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS import_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL UNIQUE, original_filename TEXT NOT NULL, stored_path TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, status TEXT NOT NULL, data_type TEXT, course_family TEXT, year INTEGER, round TEXT, confidence REAL, created_by INTEGER REFERENCES admin_users(id), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, error_message TEXT);
CREATE TABLE IF NOT EXISTS import_job_steps (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE, step TEXT NOT NULL, status TEXT NOT NULL, message TEXT, progress INTEGER, started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, finished_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS import_events (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE, event_type TEXT NOT NULL, message TEXT NOT NULL, progress INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS import_errors (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE, severity TEXT NOT NULL, code TEXT, message TEXT NOT NULL, details TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS data_releases (id INTEGER PRIMARY KEY AUTOINCREMENT, release_key TEXT UNIQUE, source_job_id INTEGER REFERENCES import_jobs(id), source_sha256 TEXT, status TEXT, approved_by INTEGER REFERENCES admin_users(id), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, notes TEXT);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id INTEGER REFERENCES admin_users(id), action TEXT NOT NULL, entity_type TEXT, entity_id TEXT, before_json TEXT, after_json TEXT, reason TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_import_jobs_status ON import_jobs(status);
CREATE INDEX IF NOT EXISTS idx_import_events_job ON import_events(job_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS import_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE,
    result_type TEXT NOT NULL,
    raw_artifact_path TEXT,
    normalized_artifact_path TEXT,
    validation_status TEXT,
    rows_extracted INTEGER DEFAULT 0,
    rows_validated INTEGER DEFAULT 0,
    rows_quarantined INTEGER DEFAULT 0,
    rows_duplicate INTEGER DEFAULT 0,
    summary_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS import_staging_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE,
    result_type TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_page INTEGER,
    raw_json TEXT NOT NULL,
    normalized_json TEXT,
    validation_status TEXT NOT NULL DEFAULT 'PENDING',
    validation_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_import_results_job ON import_results(job_id);
CREATE INDEX IF NOT EXISTS idx_import_staging_job ON import_staging_records(job_id, result_type);

CREATE TABLE IF NOT EXISTS data_release_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id INTEGER NOT NULL REFERENCES data_releases(id) ON DELETE CASCADE,
    table_name TEXT NOT NULL,
    row_id INTEGER NOT NULL,
    operation TEXT NOT NULL CHECK(operation IN ('INSERT','UPDATE')),
    before_json TEXT,
    after_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_release_items_release ON data_release_items(release_id);
CREATE INDEX IF NOT EXISTS idx_release_items_row ON data_release_items(table_name,row_id);
