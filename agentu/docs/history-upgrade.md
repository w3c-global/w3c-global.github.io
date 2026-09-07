# Ordered history and existing-data upgrade

Operations, agent runs and reconciliations are ordered by creation time across all pages, newest first. Equal millisecond timestamps use the record ID as a stable tie-breaker. A new record arriving ahead of a cursor does not duplicate or displace the older records that follow that cursor; refresh to see newer arrivals. This is a live list, not a frozen export.

The API writes a small `HISTORY#<collection>#...` pointer in the same transaction as each new record. Reads load the current original record, so approvals, cancellation and review changes do not leave a stale status in the history. Pages contain at most 40 records even when the requested limit is 60: each pointer and original participates in the transaction's complete read set. Journal and audit collections retain ascending sequence order for verification and exports.

`GET /api/platform/institutions/{id}/actions/{action_id}` returns the current operation under the same institution-membership checks. Agent-run links use this route, including when the operation is older than the first history page. **All operations** returns to the complete list. Text and status filters apply to loaded operation records; load more for earlier activity.

## Existing institutions

New institutions initialize history version 1. Existing institutions need the bounded upgrade below. An incomplete index returns `history_upgrade_required`, rather than an apparently complete but partial activity list. Other domain records and original journal/audit entries are preserved.

Before a local upgrade, stop the older HTTP server and worker and take a consistent SQLite backup. Run:

```text
python agentu/scripts/migrate_history.py --sqlite .local-platform/platform.sqlite3 --institution <institution-id>
```

The script checks at most 30 source records in an atomic batch, retains its progress and processes at most 100 batches per invocation by default. `--max-batches` can bound a run further. Exit status 2 means progress was saved and the same command should be rerun. A completed rerun is a no-op. Restart the local server from the upgraded code, refresh the application, and verify the histories and a linked operation.

For AWS, deploy this revision to **both** the API and worker and let older invocations finish before migration. Use an authorized operator identity with read/write access to the business platform table; the ordinary GitHub release role does not gain database access. The script verifies account `032312375271` and region `eu-west-2`, then resolves the actual table from the named stack's `PlatformTable` output:

```text
python agentu/scripts/migrate_history.py --stage sandbox --profile agentu --institution <institution-id>
```

Repeat for every existing institution in the selected environment; use `--stage demo` only for that environment. Record the affected institutions and completion results. Do not run old writers during the upgrade: they do not maintain the new index. The final batch marks history available and appends one `history_index_upgraded` audit event. Old ID-order cursors must be discarded by refreshing the list.

## Recovery and rollback

A failed batch writes no partial index or progress. Fix the underlying source/permission problem and rerun. A malformed source timestamp or conflicting pointer must be investigated; the migration does not rewrite source evidence to make it pass.

Prefer rollback revisions that maintain version 1 indexes. If an emergency rollback used older writers, redeploy compatible API/worker code, drain the older invocations, and run the same migration with `--rebuild`. This invalidates the finished index, preserves original records and existing valid pointers, and scans for missing pointers. Repeating `--rebuild` during an unfinished upgrade resumes the saved progress; issuing it again after completion deliberately starts a new rebuild. Verify the completed history before returning it to users.

Local verification stopped and restarted the rehearsal server, retained a pre-upgrade backup and deliberately interrupted the migration after its first batch. Resumption preserved all 129 existing source records, added nine history pointers and one audit event. The resulting snapshot has 45 linked audit events and the original five balanced journals. Tests cover histories beyond 40 records, timestamp ties, concurrent new records, idempotent replay, current statuses, tenant boundaries, failed-batch rollback, resumable upgrades and older-writer rebuilds. A mocked DynamoDB read verifies that the 40-record page uses 82 checked items. A deployed DynamoDB upgrade remains unverified.
