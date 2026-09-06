# Architecture and demonstration limits

## Hosted environment

```mermaid
flowchart LR
  Browser --> CloudFront
  Browser -->|Authorisation code + PKCE| Cognito
  CloudFront -->|Signed origin request| PrivateS3[Private S3 website]
  CloudFront -->|Uncached API request| Gateway[HTTP API + JWT authoriser]
  Gateway --> Lambda[Python policy engine]
  Lambda -->|Conditional atomic write| DynamoDB[Per-user rehearsal state]
  Gateway --> Logs[CloudWatch]
  Lambda --> Logs
```

Separate sandbox and demo stacks use separate S3 buckets, user pools, Lambda functions, API gateways, data tables and logs in account `032312375271`, London. There is no VPC or always-on application server in this design.

All state routes require a Cognito access token with the `openid` scope. The API Gateway JWT authoriser validates issuer and client; the function also rejects a missing authenticated subject. Records are keyed by that subject plus the rehearsal identifier, never by an identity supplied in the request body.

The browser stores a short-lived access token and its workspace identifier in session storage. No client secret is shipped. Hosted user registration is invite-only. No user invitation has been sent by the source code.

## Decisions and persistence

Amounts are positive integer pence. Destination and policy values come from the backend. Hard controls override approval thresholds. Approvals re-evaluate hard controls against the current balance.

Each mutation uses an idempotency key. Replays with the same payload return existing state. Reusing a key with different input is rejected. Decisions, balance changes and audit events are saved in one conditionally written DynamoDB document. Concurrent conflicts reload and retry; a request never partially updates balances independently of the record.

The deliberately small demo bounds each session to approximately 240 audit records and caps the document below DynamoDB’s item-size limit. Use a new rehearsal for additional runs. DynamoDB TTL is seven days after mutation; expired sessions are inaccessible immediately even when physical TTL deletion is pending. Point-in-time recovery may retain earlier versions longer. Website object versions are retained for 30 days after replacement. Operational logs are retained for 14 days.

## Evidence

Each event stores a sequence, UTC timestamp, previous hash and SHA-256 hash of its canonical JSON content. The server and standalone Python verifier check the chain. An administrator who can rewrite every record can generate a new consistent chain; there is no external anchor. Do not describe this as an immutable or cryptographically attested financial ledger.

## Local rehearsal

The local HTTP server binds only to loopback, rejects cross-origin API calls and Host-header rebinding, and excludes backend source and private local paths from static serving. It substitutes a local presenter identity and a locked, atomically replaced JSON-file store. It must not be exposed publicly or used as the cloud server.

## Before live financial use

Not implemented: real AI/model execution, bank/payment integrations, production role separation, beneficiary onboarding, regulated custody/payment operations, independent evidence signing, financial reconciliation, multi-tenant administration, external penetration testing, incident-response ownership and production service commitments. The demo is a basis for defining those requirements with the founder, not evidence that they have been delivered.
