# Full platform completion record

The active objective is to build Agentu as an operational product. The guided demonstration is retained as a walkthrough and does not define completion.

| Requirement | Evidence needed for completion | Current status |
| --- | --- | --- |
| Institution onboarding and isolation | Persisted institutions, membership lifecycle and adversarial cross-tenant API tests | Built and tested locally; deployed verification pending |
| Authentication and independent permissions | Verified sign-in, invitations, suspension and server-enforced roles; distinct users proposing/approving | Local identity flow browser-tested; Cognito binding and required TOTP configuration built; hosted onboarding, enrollment and recovery verification pending |
| Configurable, governed policy | Versioned limits and mandates, independent publication, full decision evidence and concurrent-change tests | Built and tested locally; hosted checks pending |
| Accounts and ledger | Multiple currencies, balanced append-only journal entries, reservations, reversals and reconciliation | Accounts, journal, reservations, governed full reversals and supplied-statement reconciliation built and tested; authenticated provider and hosted verification pending |
| Payment/treasury operations | Durable action lifecycle, independent approvals, cancellation, replay safety and provider outcomes | Internal sandbox transfers built; external execution pending |
| Agent execution | Authenticated agent identities, bounded tool permissions, model/provider integration and traceability | Mandates, scoped credentials, durable worker and treasury rule built and tested; Bedrock adapter mock-tested; real model/deployed verification pending |
| Financial integrations | Named provider, verified credentials, test connection, idempotent execution, authenticated callbacks and reconciliation | Provider input outstanding |
| Evidence and monitoring | Exports, independent verification, signing/retention, alerts, incident controls and tested recovery | Signing/archive tooling, eleven-alarm monitoring and an operations inspector built with mocked AWS; real cryptography, database integrity and local restore verified; hosted signing/retention, automatic checkpoints, live metrics/delivery, cost controls and hosted restore pending |
| Operational web application | Institution/team/account/policy/action/approval/ledger/settings journeys against the real services | Core, treasury-agent, reversal and reconciliation journeys browser-tested locally; hosted model/provider verification pending |
| AWS environments | Verified sandbox, staging and production configuration in the designated business account, access controls and smoke tests | Four isolated targets and configuration inspector built/tested; GitHub environments created; AWS access and provisioning outstanding |
| Releases and operations | W3C source, CI, controlled releases, migration/rollback, backups and a completed restore test | CI, release foundation, resumable migration and local backup/restore built and tested; deployed upgrade/restore pending |
| Public site and handover | Accurate product claims, working contact path, hosted product entry, documentation and founder walkthrough | Site revision and walkthrough built; public cutover outstanding |

The user states Agentu is pre-incorporation. Formation, regulated counterparties, commercial agreements and real financial transaction authorisation cannot be inferred from a software build. Software work continues while these external dependencies are identified; the goal remains active until the requested end state is verified.
