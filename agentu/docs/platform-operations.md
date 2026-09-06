# Institution operations

The operations application is `/agentu/app/`. It is separate from the guided three-scenario walkthrough at `/agentu/demo/`. Institutions start with no funds or accounts. Creating a workspace does not open a bank account or establish a legal company.

## First institution

1. Start the loopback server described in the README. Create a local development identity using a fictional email and a development-only password of at least 12 characters.
2. Create an institution, choose its reporting currency and add two asset accounts of the same currency.
3. As owner, post sandbox funding. This creates an asset debit and a sandbox-equity credit.
4. Create a named invitation for an approver. Copy the link when shown; it is not sent automatically and cannot be retrieved later. Lost links can be revoked and replaced.
5. Propose a transfer. The initial policy requires independent approval for every positive amount, limits each transfer to 100,000 major currency units and total daily commitments to 1,000,000. The initial currency mandate is the institution's reporting currency.
6. Sign out. Open the invitation link, register/sign in as the named recipient and accept. Only the matching verified email is accepted in the hosted service. Local development assumes email ownership.
7. Review and approve the transfer. The server rechecks the proposer, reviewers, account status, institution status, policy, daily commitments and available funds. A completed transfer posts a balanced journal and releases its reservation atomically.

The proposer cannot approve their own request, including when the proposer is an owner. Draft policies need a different active owner or administrator to publish them. A newly published mandate invalidates earlier approvals when a pending request is reviewed. Suspended or demoted reviewers do not count toward the approval threshold.

## Roles

| Role | Authority |
| --- | --- |
| Owner | Institution and account administration, sandbox funding, policy drafting/publication, invitations and membership changes, proposal and independent review |
| Administrator | Accounts, institutional pause/resume, policy drafting/publication, invitations for non-administrative roles, invitation revocation and cancellation |
| Operator | Propose actions and cancel their own pending actions |
| Approver | Independently approve or decline pending actions |
| Auditor | Read institution records and export evidence |

The worker automatically releases expired requests that have expiry jobs. Every active member can also release an expired request explicitly. An owner cannot change their own membership, and at least one active owner must remain. The application does not provide journal or audit edit/delete commands.

## HTTP interface

All platform routes require an authenticated identity. Hosted requests require a Cognito access token accepted by API Gateway and Cognito GetUser. The GetUser subject must match the authoriser's subject, and the returned email must be verified. No body or custom header can supply an authoritative user or role.

| Method and path | Purpose |
| --- | --- |
| `GET /api/platform/me` | Current authenticated identity |
| `GET /api/platform/institutions` | Current user's memberships; optional `after` cursor |
| `POST /api/platform/institutions` | Create a sandbox institution from `name` and `currency` |
| `POST /api/platform/invitations/accept` | Accept an email-bound invitation from a body `token` |
| `GET /api/platform/institutions/{id}/overview` | Institution, membership, permissions, policy and initial account page |
| `GET /api/platform/institutions/{id}/{collection}` | Accounts, actions, members, invitations, policies, agents, agent_keys, runs, journal or audit; `limit` 1–60 and optional `after` |
| `POST /api/platform/institutions/{id}/commands/{operation}` | Execute a permitted command |

Operations: `account_create`, `account_status`, `sandbox_fund`, `invite_create`, `invite_revoke`, `member_update`, `policy_create`, `policy_publish`, `institution_pause`, `action_propose`, `action_approve`, `action_decline`, `action_cancel`, `action_expire`.

Money in JSON uses integer minor units. Transfer input is `source_id`, `destination_id`, `amount`, `purpose`. Decisions use `action_id` and `reason`. Policy configuration contains `auto_limit`, `transaction_limit`, `daily_limit`, `liquidity_floor`, `required_approvals` (1–3), `approval_minutes` (5–1440) and `currencies` (GBP, EUR and/or USD). Limits apply separately to each currency; no FX conversion is implied.

Every create/command request needs an `Idempotency-Key` of 16–80 letters, digits, underscores or hyphens. Reuse the same key and payload after an interrupted response. Changing the payload with that key returns a conflict. Access is checked again before replaying a cached result. Invitation acceptance is intrinsically idempotent for the accepting identity. Invitation tokens are returned once and are excluded from persistent response caches and audit records.

Responses use `error` and `code` fields for failures. Pagination is scoped to the authenticated institution. Local sign-in uses `/api/platform-auth/register`, `/login` and `/logout`; these routes and the development identity adapter are not exposed in AWS.

## Evidence

Export the journal or audit trail from its application page. Exports reject a changed institution head while paging so a single download is consistent. Validate a saved file independently:

```text
python agentu/scripts/verify_platform_export.py <export.json>
```

The verifier checks sequence, hash links and the export head for audit records, and sequence, count and balanced postings for journal records. It rejects tampering and truncation. The included head is not an external signature; independently anchored retention remains outstanding.

## Deployment and remaining work

The CloudFormation template adds a separate `PlatformRecords` table, authenticated platform routes and a Cognito callback for the application. Apply the infrastructure update before releasing code that depends on those outputs and environment variables. `publish` writes stage-specific public configuration for both applications. Neither application contains a client secret.

Hosted users remain administrator-provisioned in Cognito. An institution invitation assigns a role after sign-in; it does not itself create the Cognito identity. The local identity database is development-only, with hashed passwords, hashed sessions, eight-hour HttpOnly/SameSite cookies and per-identity login throttling. Keep the local server on loopback.

The current service posts internal sandbox transfers and includes governed agent identities, scoped credentials, a treasury rule, a Bedrock adapter and an automatic expiry worker. See [Governed agents](agents.md) for authority, credential lifecycle, run processing and configuration. Real model invocation, external financial providers, beneficiary lifecycle, reconciliation/reversals and evidence signing remain unfinished or unverified in `platform-scope.md`.

References: [Cognito GetUser](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_GetUser.html), [DynamoDB transaction permissions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis-iam.html), [DynamoDB transactions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis.html).
