import sqlite3, json, time
from pathlib import Path
BASE=Path(__file__).resolve().parents[2]
DB=BASE/'db'/'cet_cap.db'
SCHEMA=BASE/'db'/'admin_schema.sql'
def connect():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def init_admin_schema():
 with connect() as c: c.executescript(SCHEMA.read_text())
def event(c,job_id,event_type,message,progress=None): c.execute('INSERT INTO import_events(job_id,event_type,message,progress) VALUES (?,?,?,?)',(job_id,event_type,message,progress))
def update_status(c,job_id,status,message=None):
 c.execute('UPDATE import_jobs SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(status,job_id));
 if message:event(c,job_id,'STATUS',message)
