# Agentu

Agentu is a **pre-incorporation venture** building financial controls for AI. This project contains the website, a guided demonstration and an institution operations application with a durable backend. The full product is under construction; deployed production readiness has not been established.

## Ownership and scope

- Canonical source: `w3c-global/w3c-global.github.io`, under `agentu/`.
- Deployment account authorised by the user: `032312375271`.
- AWS region: London (`eu-west-2`). Separate `agentu-sandbox` and `agentu-demo` stacks.
- No personal GitHub repository, AWS account or email destination is used by this build.
- No real bank accounts, funds, customer data, LLM calls or payment integrations.

## Rehearse locally

Requires Python 3.13 or later. The rehearsal has no third-party Python dependencies.

```powershell
python agentu/backend/local.py --port 4322
```

Open `http://127.0.0.1:4322/agentu/` or `http://127.0.0.1:4322/agentu/demo/`. On Windows, `agentu/scripts/launch-demo.ps1` starts a hidden local server and opens the demo. Local state lives in the ignored `.local-demo/` directory. **New rehearsal** creates a new workspace; it does not erase old records.

The three scenarios cover an allowed £75,000 treasury sweep, a blocked £25,000 payment to an unknown destination and a £175,000 transfer requiring operator approval. Amounts are editable. Every approval rechecks the current liquidity and hard limits.

## Validate

```powershell
python -m unittest discover -s agentu/tests -v
python -m pip install -r agentu/requirements-tools.txt
python agentu/scripts/build.py
cfn-lint .build/template.json
node --check agentu/site.js
node --check agentu/demo/app.js
node --check agentu/demo/auth.js
node --check agentu/app/app.js
node --check agentu/app/agents.js
node --check agentu/app/accounting.js
node agentu/tests/test_statement.mjs
```

The build allowlists public assets and the Lambda modules. The local development authentication adapter and local databases are excluded from the Lambda package. Credentials and development records are never synchronised to a web bucket.

## Operations application

Open `/agentu/app/` on the same local server, or run `agentu/scripts/launch-demo.ps1 -View app` on Windows. Register a development identity, create an institution, add accounts and fund the sandbox. Invite a second identity using its development email and copy the invitation link. Sign in as that second identity to accept the invitation and review a transfer. No email is sent automatically.

The application includes governed agent mandates, durable runs, automatic reservation expiry, institution-scoped roles, versioned policies with independent publication, money reservations, approvals, cancellation, expiry, a balanced journal and hash-linked audit events. Every command uses a persistent idempotency record and checks record versions at commit, including role, policy and balance dependencies. Role selection in request JSON has no authority.

Local accounts and institution records use SQLite in ignored `.local-platform/`. Local email ownership is assumed strictly for development. Hosted identities use Cognito with verified email; each invited person must also be provisioned in the stage's Cognito pool before using an institution invitation. The hosted database is a separate DynamoDB table with point-in-time recovery and no automatic TTL for platform records.

See [Platform operation and API guide](docs/platform-operations.md) and [full completion record](docs/platform-scope.md).

## Readiness and deployment

- [Founder walkthrough](docs/founder-walkthrough.md)
- [Readiness record](docs/readiness.md)
- [AWS operations and release guide](docs/aws-operations.md)
- [Architecture and limits](docs/architecture.md)

Hosted sessions require invited Cognito users and use DynamoDB with atomic conditional writes. Browser sign-in uses the OAuth authorisation-code flow with PKCE. GitHub releases use short-lived OIDC credentials and an environment-specific release role; infrastructure creation remains a separate operation.

The enquiry form prepares an email to `frankie@w3c.com`. The visitor reviews and sends it in their email application; the website does not claim to have submitted it.

## Evidence boundary

The audit records form a SHA-256 hash chain. Verification detects inconsistent contents, ordering or links. It is **not** independently anchored, externally signed or immutable against a privileged administrator. Financial operations use simulated funds and internal ledger postings. Real model invocation, bank execution, authenticated provider statements, evidence signing, production infrastructure and operational assurance remain outstanding in the full completion record.

Agent operation, credential integration, worker behavior and model configuration are documented in [Governed agents](docs/agents.md). The treasury rule runs locally; the Bedrock adapter requires a verified business AWS account and model before it can be exercised live.

[Corrections and reconciliation](docs/accounting.md) covers independently approved full journal reversals, batched statement imports, fixed ledger snapshots, exception investigation and comparison export verification. Supplied statements remain explicitly unauthenticated until a provider integration is verified.

Existing institutions: follow [the ordered-history upgrade](docs/history-upgrade.md) before using this version. New institutions initialize it automatically.
