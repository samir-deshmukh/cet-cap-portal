"""Generate deployment-specific robots.txt and sitemap.xml without inventing a domain."""
from __future__ import annotations
import argparse
from pathlib import Path
from urllib.parse import urljoin

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--site',default='site')
    ap.add_argument('--base-url',required=True,help='Absolute production HTTPS URL')
    args=ap.parse_args(); base=args.base_url.rstrip('/')+'/'
    if not base.startswith('https://'): raise SystemExit('base-url must use https://')
    site=Path(args.site); site.mkdir(parents=True,exist_ok=True)
    (site/'robots.txt').write_text('User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /preview/\nSitemap: '+urljoin(base,'sitemap.xml')+'\n',encoding='utf-8')
    # Current public site is a single static entry route; query/filter states are not canonical pages.
    (site/'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n  <url><loc>'+base+'</loc></url>\n</urlset>\n',encoding='utf-8')
if __name__=='__main__': main()
