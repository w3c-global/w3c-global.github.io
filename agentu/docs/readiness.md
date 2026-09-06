# Agentu readiness — 6 September 2026

## Implemented and verified locally

- Reworked website retaining the established Agentu identity, with clear pre-incorporation wording and prominent demo access.
- Working control room with three user-selected scenarios, editable amounts, action inspection, approval/decline, balances, audit export and chain verification.
- Server-side policy engine with integer money values, fixed mandates, hard-limit precedence and rechecks at approval.
- Idempotency, atomic state updates and user/workspace isolation.
- Local rehearsal server and Windows launcher.
- 20 passing automated tests, including a real HTTP journey through all three scenarios and approval.
- Browser checks: allowed sweep, blocked destination and approval produce the expected balances and 10 linked audit events.
- JavaScript syntax checks and CloudFormation schema/lint validation pass.
- Allowlisted static build and Lambda package.
- Infrastructure templates for separate AWS sandbox and demo stacks in the designated account.
- CI checks, OIDC release workflow, narrow release-role setup, and deployment/rollback instructions.
- Founder walkthrough for Tuesday 8 September.

## Not yet verified / blocked by account access

- AWS sign-in and an authenticated deployment session for account `032312375271`.
- Actual creation of sandbox/demo stacks and public AWS URLs.
- Cognito user onboarding and hosted authentication journey.
- DynamoDB concurrency and isolation tests against the deployed service.
- Private S3 / CloudFront checks and an actual GitHub OIDC release.
- Budget notifications, monitoring recipient and restore rehearsal.
- Updated public website cutover to the verified demo environment.

As of this record, the AWS console showed a sign-in error and no command-line AWS profiles were available. The infrastructure is prepared; it has not been represented as deployed.

## Intentional boundaries

The company is not yet incorporated. The prototype has no real AI model, financial integrations or live-money execution. The audit chain is hash-linked but not externally signed or immutable against administrators. The founder demo is a demonstrable starting point for product and pilot decisions, not a production-ready regulated service.
