"""Sync the generated search_index.json into the self-contained index.html.
The main page keeps the search index inline so city/course/college search works
instantly and without a fetch. This build step makes the inline copy identical
to the generated JSON/JS copy, including compact drawer metadata.
"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--index', default=str(ROOT / 'site/index.html'))
    ap.add_argument('--search', default=str(ROOT / 'site/data/search_index.json'))
    args = ap.parse_args()
    html_path = Path(args.index)
    search_path = Path(args.search)
    html = html_path.read_text()
    data = json.loads(search_path.read_text())
    labels_path = search_path.with_name('drawer_labels.json')
    labels = json.loads(labels_path.read_text())
    replacement = 'window.__SEARCH_INDEX__ = ' + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';'
    pattern = r'window\.__SEARCH_INDEX__\s*=\s*\[.*?\];'
    new_html, n = re.subn(pattern, replacement, html, count=1, flags=re.S)
    if n != 1:
        raise SystemExit('Could not find exactly one inline __SEARCH_INDEX__ assignment in index.html')
    label_repl = 'window.__DRAWER_LABELS__ = ' + json.dumps(labels, ensure_ascii=False, separators=(',', ':')) + ';'
    label_pattern = r'window\.__DRAWER_LABELS__\s*=\s*\{.*?\};'
    new_html, n2 = re.subn(label_pattern, label_repl, new_html, count=1, flags=re.S)
    if n2 == 0:
        # Insert immediately after the search index script assignment.
        new_html = new_html.replace(replacement, replacement + '\n' + label_repl, 1)
    html_path.write_text(new_html)
    print(f'Synced {len(data)} search records into {html_path}')

if __name__ == '__main__':
    main()
