"""Build the fast static runtime data from the verified SQLite DB.

SQLite remains the source of truth. The public site receives only:
  * the compact search index + drawer metadata, and
  * one small course/year/college JS payload per college/year.

No master SQLite DB or course-wide cutoff JSON is placed under site/.
"""
from __future__ import annotations
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--out', default=str(ROOT / 'site' / 'data'))
    ap.add_argument('--site-root', default=str(ROOT / 'site'))
    args = ap.parse_args()
    out_path = Path(args.out)
    site_root = Path(args.site_root)
    temp = ROOT / 'data' / 'processed' / 'runtime_cutoffs'
    if temp.exists(): shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=True)
    # Remove old public cutoff payloads and runtime directories.
    for stale in out_path.glob('cutoffs_*.json'):
        stale.unlink()
    for stale in out_path.glob('cutoffs_*.js'):
        stale.unlink()
    for stale in out_path.glob('catalog_*.js'):
        stale.unlink()
    for stale in out_path.glob('drawer_labels.*'):
        stale.unlink()
    if (out_path / 'college_cutoffs').exists(): shutil.rmtree(out_path / 'college_cutoffs')
    if (out_path / 'courses').exists(): shutil.rmtree(out_path / 'courses')

    # Build verified intermediate data outside site/.
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'export_trends_json.py'),
                    '--db', args.db, '--out', str(temp)], check=True)
    # Build compact search/drawer metadata directly from SQLite and sync its
    # exact copy into index.html.
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'export_search_index.py'),
                    '--db', args.db, '--out', str(out_path)], check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'sync_inline_search_index.py'),
                    '--index', str(site_root / 'index.html'),
                    '--search', str(out_path / 'search_index.json')], check=True)
    # Build only tiny college/year payloads for the drawer.
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'export_course_year_data.py'),
                    '--data-dir', str(temp), '--out', str(out_path)], check=True)
    # Intermediate course-wide JSON is build-only; never leave it in the project.
    # Keep a machine-readable manifest inside the public tree for release verification.
    import json
    runtime_files = []
    for f in sorted(out_path.rglob('*')):
        if f.is_file(): runtime_files.append({'path': str(f.relative_to(out_path)), 'bytes': f.stat().st_size})
    (out_path / 'runtime-manifest.json').write_text(json.dumps({'files': runtime_files}, indent=2), encoding='utf-8')
    shutil.rmtree(temp, ignore_errors=True)
    (out_path / 'search_index.json').unlink(missing_ok=True)
    (out_path / 'drawer_labels.json').unlink(missing_ok=True)
    print('Fast college/year runtime data built successfully.')

if __name__ == '__main__':
    main()
