# Agentu readiness — 6 September 2026

The requested end state is the full platform. The work below is verified progress; it does not close the full completion record in `platform-scope.md`.

## Implemented and verified locally

- Reworked website retaining the established Agentu identity, with clear pre-incorporation wording and prominent demo access.
- Working control room with three user-selected scenarios, editable amounts, action inspection, approval/decline, balances, audit export and chain verification.
- Server-side policy engine with integer money values, fixed mandates, hard-limit precedence and rechecks at approval.
- Idempotency, atomic state updates and user/workspace isolation.
- Local rehearsal server and Windows launcher.
- 68 automated tests covering the demo, institution service, version-checked storage, HTTP authentication and export verification.
- Browser checks: allowed sweep, blocked destination and approval produce the expected balances and 10 linked audit events.
- JavaScript syntax checks and CloudFormation schema/lint validation pass.
- Allowlisted static build and Lambda package.
- Infrastructure templates for separate AWS sandbox and demo stacks in the designated account.
- CI checks, OIDC release workflow, narrow release-role setup, and deployment/rollback instructions.
- Work is saved on the business branch for W3C draft pull request #1. The CI workflow runs all 68 tests, JavaScript checks and CloudFormation validation; check its result against the current commit before deployment.
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

## Not yet verified / blocked by account access

- AWS sign-in and an authenticated deployment session for account `032312375271`.
- Actual creation of sandbox/demo stacks and public AWS URLs.
- Cognito user onboarding and hosted authentication journey.
- DynamoDB concurrency and isolation tests against the deployed service.
- Private S3 / CloudFront checks and an actual GitHub OIDC release.
- Budget notifications, monitoring recipient and restore rehearsal.
- Updated public website cutover to the verified demo environment.

The latest AWS inspection showed the IAM sign-in page with account `032312375271` populated and username/password empty. No command-line AWS profiles were available. The infrastructure is prepared and has not been deployed.

## Intentional boundaries

The company is not yet incorporated. The treasury rule is deterministic automation; no real AI model invocation, financial integration or live-money execution has been verified. The Bedrock adapter is implemented and mock-tested. The audit chain is hash-linked but not externally signed or immutable against administrators. Real model verification, beneficiary/provider execution, reversals/reconciliation, evidence anchoring, staging/production infrastructure and tested recovery remain unfinished. Local verification of the platform controls does not establish production readiness.
