from .db import connect

def ensure_part4_schema():
    with connect() as c:
        cols={r['name'] for r in c.execute('PRAGMA table_info(data_releases)')}
        for name, typ in [('published_at','TIMESTAMP'),('verified_at','TIMESTAMP'),('manifest_json','TEXT'),('backup_path','TEXT')]:
            if name not in cols: c.execute(f'ALTER TABLE data_releases ADD COLUMN {name} {typ}')
