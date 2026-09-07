# Corrections and reconciliation

The institution application includes independently reviewed journal reversals and statement reconciliation. These controls operate on the durable sandbox ledger. They do not reverse bank instructions, authenticate a bank statement or resolve a real provider balance by themselves.

## Correcting a journal

In **Ledger**, choose **Request reversal** on an original journal and record the correction reason. The server derives the exact opposite postings from that journal; the caller cannot supply a different amount or destination. The request appears in **Operations** as a ledger reversal, linked to the original journal sequence and hash.

Every reversal requires independent approval, including amounts below the policy's automatic threshold. Current institution/account status, proposer/reviewer authority, currency and transaction/daily limits, liquidity floor, original-journal hash and available funds are checked again at approval. Funds are reserved while approval is pending and released on cancellation, decline, a failed review or expiry. A pending or posted reversal prevents a second reversal of the same original journal, even with a different request key or simultaneous request.

Approval appends a new balanced journal with `reversal_of` evidence. The original journal and original settled action remain intact. The linked correction explains the later accounting effect. A cancelled, declined, expired or blocked reversal permits a new governed request; all attempts remain in the audit trail.

The supported original entries are this platform's two-account internal transfers and sandbox funding journals. Reversals cover the complete journal. Partial corrections use a new governed transfer; a correction entry itself cannot be recursively reversed. A subsequent adjustment requires a new governed transfer or owner-posted sandbox funding entry. Gross daily usage is not refunded by a reversal, and historical agent usage remains recorded.

HTTP command: `reversal_propose`, body `journal_sequence` (integer) and `reason` (10–500 characters). It returns a normal action and uses the existing approve/decline/cancel/expiry commands. Agents cannot submit this correction command through their proposal API.

## Importing a statement

Open **Reconciliation**, select **Import statement**, then choose the account, statement currency, reference, dates and opening/closing balances. Use the CSV template, select a UTF-8 file or paste CSV. File uploads and pastes are mutually exclusive.

```csv
external_id,booked_date,amount,reference,description
bank-001,2026-09-06,-75000.00,<unique-operation-reference-or-journal-id>,Treasury allocation
```

| Column | Format |
| --- | --- |
| `external_id` | Required; unique within this statement, 1–100 characters |
| `booked_date` | Required; valid `YYYY-MM-DD`, inside the selected statement period |
| `amount` | Required CSV signed currency units; positive inbound, negative outbound, at most two decimals, no thousands separators |
| `reference` | Optional; exact operation reference or journal ID for automatic matching |
| `description` | Optional; up to 250 characters; normal CSV quoting supports commas, quotes and line breaks |

The JSON API instead uses integer minor units for amounts and balances. For GBP, CSV `-75000.00` becomes JSON `-7500000`. No currency conversion takes place. The statement's opening balance plus signed movements must equal its closing balance before comparison begins. Negative supplied balances are accepted for investigating differences; they do not grant overdraft authority to the ledger.

Files are capped at 5 MB and statements at 10,000 transactions. Imports use atomic batches of up to 20 rows and can resume from their saved uploaded-row count. Reimporting the same source, statement reference and ledger cut-offs returns the existing comparison. Changing its metadata conflicts. To replace a cancelled or incorrect import, use a distinct statement reference; the previous evidence is retained. Duplicate external IDs inside one statement are rejected. Overlapping separate statements do not post or duplicate ledger entries.

The browser computes a source-file SHA-256; the API records the declared hash and a separately computed chain over the normalized rows. The source is explicitly labelled `user_supplied_statement`. A caller-declared file hash and a successful comparison do not establish bank authenticity.

## Choosing a ledger snapshot

The **Opening journal cut-off** is exclusive; **Closing journal cut-off** is inclusive. For example, opening cut-off 1 and closing cut-off 3 compare account movements in journals 2 and 3. The worker also scans earlier journals to reconstruct that account's opening balance. Use opening cut-off 0 to compare from the beginning. Journal sequences are institution-wide; rows for other accounts are included in the integrity scan but excluded from the account's movement comparison.

The closing cut-off cannot exceed the current journal sequence. Later postings leave the report's snapshot unchanged. A missing global journal sequence stops comparison and records a failure. The comparison can be retried after source integrity is restored. A supplied statement's booked dates and the selected ledger cut-offs are separate inputs; the operator is responsible for selecting the period/range to compare, including legitimate booking delays.

The existing durable worker processes ledger and statement pages under leases, outside the browser request. A reclaimed or cancelled lease cannot repeat a completed page. Comparisons do not reserve funds, adjust balances or run a model. Review data and source hashes remain institution-scoped.

## Investigating and reviewing

Automatic matching requires an exact unique reference or journal ID, the same signed amount and an unused ledger row. The worker never guesses among ambiguous references or uses amount-only matching. For example, repeated `sandbox-funding` references require unique journal IDs or documented manual matching.

The comparison shows statement/ledger closing balances, opening/closing differences, matched counts and unmatched rows. An importer or administrator can manually match two unmatched rows with the same signed amount, recording the evidence. They can remove a match to reopen investigation. Each change creates a new comparison revision and audit event. A review based on an older revision is rejected.

A different active owner or approver must review the completed comparison. Anyone who imported or manually changed its rows is excluded from reviewing it. A clean result becomes **reconciled**. Outstanding rows or opening/closing differences require explicit acknowledgement and a reason; that result becomes **accepted with exceptions**. Exceptions remain visible. Acceptance does not create a balancing adjustment or claim that unexplained money has been resolved.

Final reviewed comparisons cannot be changed or cancelled. A later investigation creates a new comparison. Uploading, queued, running, ready or failed comparisons can be cancelled by their importer or an administrator. Failed comparisons can resume their existing progress after the underlying problem is resolved.

## API and export

All routes below use the human institution API and its verified identity and membership checks:

- Collection: `GET /api/platform/institutions/{tenant}/reconciliations`.
- Detail: `GET /api/platform/institutions/{tenant}/reconciliations/{id}`.
- Rows: append `/statement` or `/ledger`; standard `after` cursor and `limit` apply.
- Commands: `reconciliation_create`, `reconciliation_append`, `reconciliation_finish`, `reconciliation_match`, `reconciliation_unmatch`, `reconciliation_approve`, `reconciliation_cancel`, `reconciliation_retry`.

Create requires account ID, matching currency, source SHA-256, statement reference, period start/end, opening/closing balances and total rows; optional `file_name`, `ledger_start` and `ledger_end` select provenance and cut-offs. Append sends `reconciliation_id`, zero-based `offset` and up to 20 normalized rows. Finish validates completeness and queues comparison. Matching/unmatching and review send the current `revision`; match supplies `statement_index` and `journal_sequence`. Review supplies a reason and `accept_exceptions=true` only when explicitly accepting outstanding differences. All command requests use persistent idempotency keys.

**Export comparison** gathers all pages and rejects a revision or review change during the export. Independently check the saved JSON:

```text
python agentu/scripts/verify_reconciliation_export.py <comparison.json>
```

The verifier checks row counts/order, source-row hashes, one-to-one match links, signed amounts, statement and ledger arithmetic, reported differences, the comparison facts hash and independent final-review consistency. It does not authenticate the original file, reconstruct the full underlying journal from the comparison alone, prove privileged-store immutability or provide an external signature. Preserve original source files and independent ledger/evidence anchors for those assurances.

## Verification and deployment

Automated tests cover reversals of transfers/funding, independent review, current source checks, concurrent duplicate claims, expiry/retry, fixed ledger cut-offs, batched imports, ambiguous references, manual matching/unmatching, explicit exception acceptance, tenant isolation, worker lease replay, missing source journals and altered/truncated exports. The standalone JavaScript parser checks quoting, dates, exact money and malformed input.

Local browser verification imports two fictional GBP 75,000 outflows against journals 2–3, reopens/rematches a row, obtains an independent clean review, and appends an independently approved reversal of original journal 2. The comparison retains its original GBP 2.35m closing snapshot after that later reversal. A second statement retains an unmatched GBP 100 charge: clean review is rejected and a different reviewer explicitly accepts the exception without a ledger adjustment. A later agent run and independent approval restore the reserve target. The final local snapshot independently verifies 44 linked audit events, five balanced journals and both comparison exports; balances are GBP 2.35m Operating and GBP 150k Reserve, with zero reserved. The comparison and expanded evidence fit a 390-pixel viewport without page-level horizontal overflow; wide row tables scroll within their panels. Hosted AWS processing, authenticated provider statements and production assurance remain unverified.

The worker and API now use `AccountingService`; the existing platform table and work partition support the new records without a new AWS resource. Release the worker and API together using the existing release tool. Work remains in progress under `platform-scope.md`.
