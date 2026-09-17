# CAP CET Portal — Launch Audit

Status is based on repository inspection only. Deployment has **not** been performed. The production domain, legal/business details and live browser/device checks are not supplied, so those items are explicitly marked NEEDS INPUT or MANUAL.

| # | Requirement | Current status | Issue / change | Files affected | Verification | Final status |
|---|---|---|---|---|---|---|
| 1 | Canonical URLs | NEEDS INPUT | Static site has one public document route; production domain is unknown. Do not invent it. | `site/index.html` | After domain is supplied, add absolute self-canonical and verify page source. | NEEDS INPUT |
| 2 | Open Graph | NEEDS INPUT | No supplied production-domain/share-image facts. | `site/index.html` | Supply approved 1200×630 image/domain, then verify social preview. | NEEDS INPUT |
| 3 | Security headers | IMPLEMENTED (admin backend) / MANUAL (host) | Admin API sends nosniff, referrer, permissions, CSP and optional HSTS. Public static host must configure equivalent headers. | `backend/main.py` | Inspect live response headers and test all resources after deployment. | MANUAL |
| 4 | robots.txt | READY TO GENERATE | Domain is unknown; generator requires an explicit HTTPS base URL and does not invent one. | `scripts/generate_public_seo.py` | Run with real production URL and fetch `/robots.txt`. | NEEDS INPUT |
| 5 | sitemap.xml | READY TO GENERATE | Current public site has one static route. Generator requires real production URL. | `scripts/generate_public_seo.py` | Run with real production URL; validate XML and listed URL. | NEEDS INPUT |
| 6 | Unique titles | PARTIAL | Main title is present; only one current public HTML route is supplied. Privacy/404 have specific titles. | `site/index.html`, `site/privacy.html`, `site/404.html` | Inspect page source for each public route. | READY |
| 7 | Meta descriptions | NEEDS INPUT | Accurate production description must be confirmed against final visible content. | `site/index.html` | Add/verify one description after final copy is approved. | NEEDS INPUT |
| 8 | Alt text | MANUAL | No meaningful `<img>` tags were found in the supplied main HTML. Verify future assets. | `site/index.html` | Accessibility inspector/manual review. | READY / MANUAL |
| 9 | Internal links | PARTIAL | Main app is primarily interaction-driven; crawlable public route set is small. | `site/index.html`, `site/privacy.html`, `site/404.html` | Crawl links and test every destination after deployment. | MANUAL |
| 10 | Custom 404 | IMPLEMENTED | Added branded static `404.html`. HTTP 404 status depends on hosting configuration. | `site/404.html` | Request nonexistent URL and verify HTTP 404, not only page appearance. | MANUAL |
| 11 | Privacy policy | DRAFT / NEEDS LEGAL REVIEW | Added transparent draft without inventing legal claims or business details. | `site/privacy.html` | Supply real operator/data-tool details and obtain legal review. | NEEDS INPUT |
| 12 | HTTPS | NEEDS INPUT | Deployment not performed. | Host/deployment config | Verify HTTPS, certificate, redirect and mixed-content status on live domain. | MANUAL |
| 13 | Exposed secrets | PARTIAL | Public-tree verifier rejects common private-key/API-key patterns during publishing; Git-history review remains a manual launch task. | `backend/admin/publishing.py` | Run repository/history secret scan before launch; rotate any exposed secret. | MANUAL |
| 14 | Mobile responsiveness | MANUAL | Existing responsive frontend preserved; no live device test performed. | `site/index.html` | Test phone portrait/landscape, tablet, laptop and desktop. | MANUAL |
| 15 | Page speed | MANUAL | Runtime build keeps compact search index and course/year payloads; no live performance measurement performed. | `scripts/build_runtime_data.py`, `site/data/*` | Measure deployed site with agreed tooling and compare bottlenecks. | MANUAL |
| 16 | Working forms | N/A for public site / ADMIN FORM PRESENT | Public site has no identified contact form; admin login/upload forms exist and require live integration/security testing. | `site/index.html`, `backend/main.py` | Submit valid/invalid admin forms in a deployed environment. | MANUAL |
| 17 | Form feedback | PARTIAL | Admin import processing has status/timeline feedback. Live network/error/duplicate testing remains manual. | `backend/main.py` | Test loading, validation, server errors and repeated submissions. | MANUAL |
| 18 | Clear CTA | MANUAL | Existing public interaction flow preserved; final CTA review is visual/manual. | `site/index.html` | Confirm primary next action is clear on real device. | MANUAL |
| 19 | Accessibility | MANUAL | Existing keyboard/dropdown work preserved; no automated or full manual accessibility audit run here. | `site/index.html`, `site/404.html`, `site/privacy.html` | Keyboard, zoom, contrast, focus and screen-reader checks. | MANUAL |
| 20 | Browser testing | MANUAL | No deployed browser matrix test performed. | Whole project | Chrome, Firefox, Edge, Safari; Android Chrome and iOS Safari where available. | MANUAL |

## Part 4 implementation

- Release publishing is transactional at the data layer and atomic at the public-tree layer.
- A verified backup is created before replacing `site/`.
- Runtime output is generated from the production SQLite database; seat-matrix public data is generated from the project's existing verified seat CSV source.
- JSON artifacts are parsed before publish; private DB/key files and common secret patterns are rejected from the public tree.
- Published releases record manifest, backup path, publish time and verification time.
- Data Health, Release History, Audit Log and Publish actions are available in the admin area.
- Rollback remains restricted to `SUPER_ADMIN` and is audited.

## Required before real deployment

1. Supply the final production HTTPS domain.
2. Supply/approve the Open Graph share image and final metadata/copy.
3. Supply operator/legal/privacy details and obtain legal review.
4. Configure the chosen host's security headers, HTTPS redirect and 404 behavior.
5. Run the manual mobile, accessibility, performance and browser matrix.
6. Create admin users with strong passwords and set `CET_ADMIN_SESSION_SECRET`; do not commit secrets.
