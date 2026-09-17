# Admin Part 3 — Review, Approval and Rollback

## Review boundary
Imports stop at `REVIEW_REQUIRED` after extraction, normalization, validation and
comparison. Production fact tables are untouched until an authorized reviewer
approves the import.

## Approval
`POST /admin/api/imports/{job_id}/approve` is available to `SUPER_ADMIN`,
`DATA_ADMIN`, and `REVIEWER`. The server checks that the import is waiting for
review and that its validation result is `PASS` with no non-VALID staged rows.
The production promotion runs inside one SQLite transaction.

## Release records
Every successful promotion creates a `data_releases` record and one
`data_release_items` record per newly inserted production fact row. The item
stores the table, row id, operation and after-image so rollback is deterministic.

## Rejection
`POST /admin/api/imports/{job_id}/reject` requires a reason and changes the
import to `QUARANTINED`. The action is written to `audit_log`.

## Rollback
`POST /admin/api/releases/{release_id}/rollback` is restricted to `SUPER_ADMIN`.
Only row IDs recorded as INSERT operations for that release are deleted. The
rollback itself is transactional and audited.

## Review API
`GET /admin/api/imports/{job_id}/review` returns import metadata, result summaries
and a bounded preview of staged rows. `GET /admin/releases` lists release history.

## Part 4 boundary
Publishing/exporting is intentionally not performed here. The next stage starts
from the committed release and generates the public runtime data, validates it,
publishes it, and verifies the public site.
