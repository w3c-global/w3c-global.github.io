# Platform backup and recovery

The local institution database can now be backed up consistently, independently checked and restored into a separate inspection copy. Recovery inspection denies platform changes and agent proposals, does not start the worker, invalidates copied browser sessions and uses a separate session cookie. It does not automatically replace the operating database or make a restored service safe for live financial execution.

## Create and verify a local backup

Use the repository's Python environment with the tooling requirements installed. The output directory must be new. Keep backups in private, business-controlled storage: they contain institution records, hashed local passwords, invitation/agent credential hashes and session records. They are excluded from Git and deployment artifacts. The backup is not separately encrypted by this script; use encrypted storage and appropriate access controls.

```text
python agentu/scripts/recovery.py backup --source .local-platform/platform.sqlite3 --out .build/recovery-<run>/backup
python agentu/scripts/recovery.py verify .build/recovery-<run>/backup
```

The script uses [SQLite's backup API](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.backup), which supports a database being accessed by other clients. It writes a self-contained database, checks it and then creates `manifest.json`. The manifest records the backup time window, file digest, logical-record digest and institution evidence heads. A failed or timed-out copy has no completed manifest; retain it for investigation and retry into a new directory. Existing output files and directories are never overwritten.

Independent verification reconstructs account balances from balanced journals and reservations from pending actions. It checks posted journals against their hash-linked audit evidence, daily/agent usage, action-to-journal links, approval independence, reversal sources, active owner/policy records, pending work links, history pointers and final comparison rows against their original journals. Every raw document and storage version participates in the logical digest. It does not independently authenticate a bank statement, prove the historical authority of an administrator, or detect a complete privileged rewrite accompanied by a replacement manifest. External evidence anchoring remains outstanding.

This backup covers the institution platform database. Guided-demonstration JSON sessions in `.local-demo`, website/source files, hosted Cognito identities and provider configuration are separate assets. Development passwords and assumed local email ownership are not a substitute for hosted identity recovery.

## Restore and inspect

```text
python agentu/scripts/recovery.py restore .build/recovery-<run>/backup --out .build/recovery-<run>/restored
python agentu/backend/local.py --recovery --platform-db .build/recovery-<run>/restored/platform.sqlite3 --port 4323
```

Open `http://127.0.0.1:4323/agentu/app/` and sign in using an existing development identity from the backup. Restored sessions are expired deliberately, while the source database and its sessions remain intact. The report `recovery.json` records that change and the verified institution state. A subsequent inspection login creates new session records in the restored copy; its original backup stays unchanged.

The banner identifies **Recovery inspection**. Transfer, approval, agent, membership, registration and invitation-acceptance writes are denied at the HTTP boundary, even if a caller bypasses the interface. Only sign-in and sign-out can update inspection authentication records. The worker is not started, so no restored job is dispatched. Source authority records remain available for investigation; inspection is not reauthorization of previously revoked users, invitations, policies or machine credentials.

Compare the recorded balances, held funds, journal/audit heads, unresolved actions, running jobs, comparisons and membership state with independent records. A recovery point can predate a payment, revocation or approval: reconcile all subsequent activity and reauthorize identities before any cutover. Do not simply point a writable service at an older snapshot. New institutional workflows and provider outcomes after the backup must be accounted for explicitly.

Stop the inspection process when finished. The tooling retains the source, backup and restored copy; cleanup is separate. If the restored snapshot predates ordered history, follow `history-upgrade.md` on an isolated copy before inspecting those lists, preserving the original backup.

## Hosted recovery remains to be verified

The AWS platform-table template enables point-in-time recovery and retention; the table has not been deployed. AWS [restores into a new table](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/pointintimerecovery_restores.html); a table restore alone does not restore the complete service. Restore settings including IAM policies, monitoring, tags, deletion protection and PITR need explicit attention.

The hosted drill must use account `032312375271`, London, and record all of the following before it is considered complete:

1. Preserve incident evidence, pause new operations and stop execution/dispatch. Identify a recovery point and affected institutions, and retain the current table for comparison.
2. Restore the designated stage's platform table into a new isolated recovery table. Keep application and worker roles from using it while inspection is incomplete. Record the source table ARN, recovery time and actual restoration duration.
3. Restore or verify encryption, table protection, PITR, tags, monitoring and narrowly scoped operator access. Recover the corresponding Cognito configuration and identities; do not silently reactivate revoked identities or credentials from an older point.
4. Reconstruct the ledger, reservations, usage, actions, work queue and evidence from a consistent recovered dataset. Compare independent audit anchors and provider records. DynamoDB scans of a changing live table are not a substitute for a consistent recovery image.
5. Account for every provider submission, response, callback, approval, membership change and credential revocation after the recovery point. Prove that pending and retried work cannot duplicate external execution.
6. Use a controlled deployment to bind the verified table to a quarantined service, test hosted sign-in and institution boundaries, verify observability, and authorize resumption only after reconciliation. Retain a documented rollback route.

No hosted restore, production cutover, recovery time objective or recovery point objective has been verified. The local drill does not establish those claims. Incident ownership, provider recovery procedures and independent retention must be completed before live financial use.

## Recorded local drill — 7 September 2026, London

The running rehearsal database was backed up and restored to a separate directory. Both copies reconstructed 45 linked audit events, five balanced journals, four operations, three agent runs and two completed comparisons. Account balances remained GBP 2.35m Operating and GBP 150k Reserve, with zero reserved. Two copied live sessions were invalidated in the restored copy.

Browser inspection required a fresh sign-in, showed the recovery banner and balances, and exposed no financial mutation controls. Its 390-pixel layout had no page-level horizontal overflow. Returning to the original workspace retained the existing sign-in and operating controls. The temporary recovery server was stopped after the drill. Automated tests also cover nonzero reservations, live WAL data, missing jobs/pointers, altered balances/meters, coordinated journal rewrites against audit evidence, mismatched comparison journals, file corruption, failed backups and denied recovery HTTP writes.
