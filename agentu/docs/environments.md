# Business deployment environments

Agentu has four deployment targets in account `032312375271`, region `eu-west-2`. Each uses its own stack, data tables, user pool, browser client, API, worker, website bucket, CloudFront distribution and release role. A stage name does not activate financial integrations: the current application still uses simulated funds until provider execution is implemented and verified.

| Target | Purpose | GitHub release environment | Allowed release branch | Application log retention |
| --- | --- | --- | --- | --- |
| `sandbox` | Development and integration verification | `agentu-sandbox` | `main`, `codex/agentu-*` | 30 days |
| `demo` | Stable founder walkthrough | `agentu-demo` | `main` | 30 days |
| `staging` | Hosted acceptance and release rehearsal | `agentu-staging` | `main` | 90 days |
| `production` | Operational deployment target | `agentu-production` | `main` | 90 days |

All four GitHub environments exist. The staging and production branch policies were read back from GitHub on 7 September 2026. Their AWS role variables remain unset; no AWS stack has been provisioned. Branch restrictions are not independent deployment approval or proof of production readiness.

## Hosted identity baseline

Every new stack requires authenticator-app MFA through Cognito. Users enroll and enter their own codes on the hosted sign-in page; Agentu does not collect authenticator secrets. The template enables software-token MFA with `MfaConfiguration=ON`, limits browser access and identity tokens to 15 minutes, enables revocation and requires ownership verification before an email change becomes active. Identity provisioning remains invitation-only.

[AWS documents the hosted TOTP enrollment flow](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-mfa-totp.html). Actual enrollment, sign-in, sign-out, expiry, lost-device recovery and disabled-user rejection must be exercised after deployment with separate business identities. Local password sessions do not verify these hosted paths. Existing installations must apply the new infrastructure before using the updated release role or release script.

Both data tables and the user pool have deletion protection; CloudFormation also retains them on deletion or replacement. The institution table has point-in-time recovery and no TTL. Deleting a stack is not a recovery or data-erasure procedure. These controls are configuration requirements; a successful restore remains a separate check.

## Verify the deployed configuration

Run this with an authenticated business session after the stack finishes deploying:

```text
python agentu/scripts/deploy.py inspect --stage staging --profile agentu
```

The inspection only reads AWS resources. It writes a local report at `.build/staging-environment.json` after successful verification. It checks:

- Stack account, region, stage, ownership tags and deployment contract; exact resource names and website/sign-in URLs.
- Both Lambda roles, runtimes, table bindings, model setting, active state, code hashes and revision IDs.
- Required software-token MFA, invitation-only identities, verified email changes, public OAuth client, callback URLs, scopes, token settings and revocation.
- The complete API route inventory, JWT issuer and client, access-token scope requirements, and the exact application integration. Inventory reads follow pagination.
- Data encryption, deletion protection, point-in-time recovery, institution TTL disabled and rehearsal expiry enabled.
- Private, encrypted, versioned website storage and the distribution's actual website/API origins.

Inspection failures stop release or publication before those operations mutate AWS. No automatic repair or fallback to another environment is attempted. The code release uses the Lambda revision IDs observed by inspection, so a concurrent configuration change makes its conditional update fail. Both API and worker must match the current package before website publication. If a later update fails after one function has changed, retain that deployment evidence and repair the package mismatch before publishing.

The release role gains narrowly scoped configuration-read permissions for these checks. It cannot read or alter institution records directly, administer identities, pass roles or execute infrastructure changes. Its OIDC trust is bound to the exact W3C repository and environment. API configuration reads use the [documented management resource ARNs](https://docs.aws.amazon.com/service-authorization/latest/reference/list_apigatewayv2.html).

## Provision and validate each target

Use the plan, status, apply and publish sequence in [AWS operations](aws-operations.md), choosing the required `--stage`. Rehearse in sandbox, maintain the stable demo, then complete hosted acceptance in staging before operational cutover. Keep source revisions and the corresponding environment reports with the release evidence. Provision separate users in each environment; never copy a development database, password or session into production.

The automated tests validate all four target configurations, cross-environment refusal, weakened MFA, public registration, changed callback URLs and token units, unprotected or expiring institution data, extra/unauthenticated API routes, mixed Lambda packages, failed revision updates and role boundaries. Selected mocked AWS responses are checked against the installed SDK schemas. These tests have not contacted AWS.

Configuration verification does not prove hosted user journeys, MFA recovery, tenant isolation in the deployed service, provider execution, model availability, audit anchoring, alert delivery, budget notifications or restoration. Complete and retain the actual smoke tests in `aws-operations.md` and the full requirements in `platform-scope.md` before declaring the product ready.
