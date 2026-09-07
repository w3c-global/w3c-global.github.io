# Agentu readiness — 7 September 2026

The requested end state is the full platform. The work below is verified progress; it does not close the full completion record in `platform-scope.md`.

## Implemented and verified locally

- Reworked website retaining the established Agentu identity, with clear pre-incorporation wording and prominent demo access.
- Working control room with three user-selected scenarios, editable amounts, action inspection, approval/decline, balances, audit export and chain verification.
- Server-side policy engine with integer money values, fixed mandates, hard-limit precedence and rechecks at approval.
- Idempotency, atomic state updates and user/workspace isolation.
- Local rehearsal server and Windows launcher.
- 100 automated tests covering the demo, institution service, version-checked storage, HTTP authentication and export verification.
- Browser checks: allowed sweep, blocked destination and approval produce the expected balances and 10 linked audit events.
- JavaScript syntax checks and CloudFormation schema/lint validation pass.
- Allowlisted static build and Lambda package.
- Infrastructure templates for separate AWS sandbox and demo stacks in the designated account.
- CI checks, OIDC release workflow, narrow release-role setup, and deployment/rollback instructions.
- Work is saved on the business branch for W3C draft pull request #1. The CI workflow runs all 101 tests, JavaScript checks and CloudFormation validation; check its result against the current commit before deployment.
- GitHub release environments `agentu-sandbox` and `agentu-demo` created with branch restrictions. AWS role attachment remains pending.
- Founder walkthrough for Tuesday 8 September.
- Operational application at `/agentu/app/`: onboarding, institutions, accounts, funding, operations, policies, team access, ledger and audit.
- Durable SQLite development store and transactional DynamoDB adapter, with separate records and optimistic checks for the full read set.
- Independent proposal/approval and policy publication, current-role checks, reservations, daily limits, institution pause, cancellation, decline and automatic reservation expiry.
- Real local sign-in with hashed passwords, cookie sessions and login throttling; Cognito identity binding implemented for hosted use.
- Browser verification with two separately signed-in development identities: £2.5m opening sandbox capital, a £75k pending transfer and independent approval. Result: £2.425m operating, £75k reserve, zero reservation, two balanced journals and nine linked audit events.
- Mobile breakpoint inspected with no page-level horizontal overflow.
- Consistent paginated journal/audit exports and a standalone verifier rejecting altered or truncated data.
- AWS template extended with the platform table, authenticated API route and application callback/configuration; no local authentication adapter is included in Lambda.

- Governed Agents and Agent runs pages: independent mandate publication, scoped expiring credentials, suspension, cancellation, retry-safe runs and provider evidence.
- Local worker browser walkthrough: an independently published reserve mandate proposed GBP 75,000; the requesting identity could not approve; a different identity approved, producing GBP 2.35m Operating, GBP 150k Reserve and no outstanding reservation.
- Repeated treasury run verified as no-action; external credential issuance, masked one-time display, removal after dialog close and revocation checked in the browser.
- Independent server-side snapshot exports verify 25 linked audit events and three balanced journals. This is separate from the browser-download check, which remains unverified.
- Worker deployment, restricted work-item deletion, optional one-model Bedrock permission, heartbeat/failure/expiry alarms and coordinated API/worker release tooling pass template validation.

- Ledger reversals append an independently approved inverse journal, preserve the original, prevent duplicate reversal claims and reuse current policy/liquidity/expiry controls.
- Supplied-statement reconciliation supports bounded CSV imports, fixed journal cut-offs, worker comparisons, exact reference/amount matching, manual matching/unmatching, independent review and explicit exception acceptance.
- Browser verification matched two GBP 75k outflows with zero difference, reopened/rematched one row, obtained independent review and verified that a later reversal leaves that snapshot unchanged. A second statement with an unmatched GBP 100 charge rejected clean review and required explicit exception acceptance.
- Comparison exports have a standalone arithmetic, completeness, link and hash verifier; CSV parser tests cover quoted fields, dates, duplicate IDs and exact signed amounts.
- The latest accounting snapshot verifies 44 linked audit events, five balanced journals and both clean/exception comparison exports. After correction and a further independently approved agent transfer, balances are GBP 2.35m Operating and GBP 150k Reserve, with zero reserved.
- The final comparison and expanded evidence were inspected at 390 pixels: no page-level horizontal overflow; wide row tables scroll inside their panels.

## History and upgrade verification

- Operations, agent runs and reconciliations now paginate in creation order across the full history. Agent-run links open the exact current operation.
- Tests verify more than 40 records, timestamp ties, inserts between pages, resumable upgrades and rollback rebuilds. A mocked DynamoDB page fits the full read-set check.
- The local upgrade preserved 129 existing source records, added nine pointers and one audit event, and resumed after a deliberately bounded first run. The latest institution has 45 linked audit events and five balanced journals. Direct links and return to the complete list were verified in the browser.
- Existing institutions must follow `history-upgrade.md`; deployed migration verification remains outstanding.

## Local recovery verification

- Consistent platform backups, independent whole-database integrity checks and quarantined restore copies are implemented. Recovery HTTP rejects domain changes, copied sessions expire, and the worker remains stopped.
- The live rehearsal database was restored separately and browser-inspected with matching balances, 45 audit events, five journals, four operations, three runs and two comparisons. Its original session and operating controls remained available.
- Tests cover live WAL backups, nonzero reservations, lost work/history, altered accounting evidence and mutation attempts against recovery mode. See `recovery.md`; hosted recovery and cutover remain unverified.

## Not yet verified / blocked by account access

- AWS sign-in and an authenticated deployment session for account `032312375271`.
- Actual creation of sandbox/demo stacks and public AWS URLs.
- Cognito user onboarding and hosted authentication journey.
- DynamoDB concurrency and isolation tests against the deployed service.
- Private S3 / CloudFront checks and an actual GitHub OIDC release.
- Budget notifications, monitoring recipient and hosted restore rehearsal.
- Updated public website cutover to the verified demo environment.

The latest AWS inspection showed the IAM sign-in page with account `032312375271` populated and username/password empty. No command-line AWS profiles were available. The infrastructure is prepared and has not been deployed.

## Intentional boundaries

The company is not yet incorporated. The treasury rule is deterministic automation; no real AI model invocation, financial integration or live-money execution has been verified. The Bedrock adapter is implemented and mock-tested. The audit chain is hash-linked but not externally signed or immutable against administrators. Real model verification, beneficiary/provider execution, authenticated provider reconciliation, evidence anchoring, staging/production infrastructure and tested hosted recovery remain unfinished. Local verification of the platform controls does not establish production readiness.
