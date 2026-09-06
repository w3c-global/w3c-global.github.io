# Agentu

Agentu is a **pre-incorporation venture** exploring financial controls for AI. This project contains the website, a working demonstration, and infrastructure for separate AWS sandbox and founder-demo environments. It is not a production banking platform.

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
```

The build allowlists public assets and packages only three backend files into Lambda. It never synchronises the repository, credentials or local demo records to a web bucket.

## Readiness and deployment

- [Founder walkthrough](docs/founder-walkthrough.md)
- [Readiness record](docs/readiness.md)
- [AWS operations and release guide](docs/aws-operations.md)
- [Architecture and limits](docs/architecture.md)

Hosted sessions require invited Cognito users and use DynamoDB with atomic conditional writes. Browser sign-in uses the OAuth authorisation-code flow with PKCE. GitHub releases use short-lived OIDC credentials and an environment-specific release role; infrastructure creation remains a separate operation.

The enquiry form prepares an email to `frankie@w3c.com`. The visitor reviews and sends it in their email application; the website does not claim to have submitted it.

## Evidence boundary

The audit records form a SHA-256 hash chain. Verification detects inconsistent contents, ordering or links. It is **not** independently anchored, externally signed or immutable against a privileged administrator. Money movements and agent requests are simulated. Production segregation of duties, banking adapters, model governance, reconciliation and external assurance remain future work.
