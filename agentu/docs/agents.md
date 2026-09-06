# Governed agents

Agentu now runs bounded treasury automation against the institution service. Three providers share the same policy, reservation, approval, ledger and evidence controls:

| Provider | Implemented behavior | Verification boundary |
| --- | --- | --- |
| Treasury rule | Reads scoped accounts, preserves an operating floor and proposes up to the approved reserve target | Exercised against the local application and durable worker |
| External agent | Authenticates with a scoped, expiring credential and submits one internal-transfer proposal | API and credential lifecycle tested locally |
| Amazon Bedrock | Sends scoped account data to one configured London foundation model through Converse; accepts one supported tool decision | Adapter tested with mock responses; real account/model invocation remains unverified |

The treasury rule is deterministic automation. It does not use a language model. All transfers still use simulated funds in the internal ledger. No agent has bank-execution, policy-administration or human-approval authority.

## Create and operate a mandate

1. In **Agents**, an owner or administrator creates a draft with a name, objective, provider, source/destination account scope, permitted currencies, transaction/daily limits and daily run budget.
2. A different active owner or administrator reviews and publishes it. Agent limits must fit inside the institution's current policy. Treasury rules require one source and a different destination in the same currency.
3. An owner or operator starts a published, configured agent from **Agents** or **Agent runs**. The local worker polls every two seconds; the AWS worker is scheduled every minute when deployed.
4. Open the run evidence to inspect the decision, snapshot reference, provider evidence, attempt count and resulting action. Bedrock evidence includes model ARN and token usage, without private reasoning.
5. Open **Operations** to review the proposal. The person requesting a run, or issuing the external credential, cannot approve its resulting transfer. The agent cannot approve it either. The platform rechecks the human sponsor's current authority before settlement.
6. Run again after the reserve is filled. The treasury rule records `no_action`. Pending incoming allocations from the same agent count toward its target, preventing repeated runs from proposing the same allocation twice.

`require_review=true` always requires independent approval, even below the institution's automatic threshold. Setting it false delegates the approval threshold to the institution policy; hard limits still apply. Limits apply separately by currency. The daily run budget counts new requests, including blocked and no-action decisions; an idempotent replay does not consume another run.

Suspension stops new proposals and blocks settlement of pending proposals at review. A new draft suspends the previous mandate immediately. Republishing requires independent review and invalidates old credentials. Pending reservations remain until review, cancellation, decline or automatic expiry; suspending an agent does not erase accounting evidence.

## External proposal API

For an active external agent, an owner can issue a labelled credential lasting 1–90 days (default seven). Copy it into the external client's secret store. The credential is shown once, never stored in plaintext by the service, and omitted from response replay caches, audit records and collection responses. It is bound to one institution, agent and mandate revision. It also requires its issuing owner to remain active with owner authority.

Send only to the intended Agentu environment:

```http
POST /api/agent/proposals
Authorization: Bearer <agent-credential>
Content-Type: application/json
Idempotency-Key: <unique-request-id-at-least-16-characters>
```

```json
{
  "source_id": "<operating-account-id>",
  "destination_id": "<reserve-account-id>",
  "amount": 7500000,
  "purpose": "Allocate treasury reserve within the approved mandate."
}
```

Amounts are integer minor units: 7,500,000 means GBP 75,000 for GBP accounts. The request must contain exactly those four fields. Copy account IDs from account/mandate records in the institution application. Credentials grant proposal access only; they do not grant an account-reading endpoint or access to human `/api/platform/` administration.

The response contains an `action` and a durable `run`. `action.status` can be `blocked`, `pending` or `settled`. A completed run means the proposal was processed; it does not mean the resulting operation settled. Reuse the same request ID and identical payload after an interrupted response. Changed input returns `409`; invalid/revoked/expired credentials return `401`; daily run-budget exhaustion returns `429`. Never automatically replace an uncertain request's key.

Human commands: `agent_create`, `agent_revision`, `agent_publish`, `agent_suspend`, `agent_key_create`, `agent_key_revoke`, `agent_run`, `agent_run_cancel`. Collections: `agents`, `agent_keys`, `runs`. `GET /api/platform/capabilities` reports provider configuration, not proof that a real model invocation has succeeded.

## Worker and AWS configuration

Jobs are written atomically with the request to a durable work partition. The worker claims a 150-second lease, calls the provider outside database transactions, then rechecks current scope, policy, balances, mandate revision and sponsor authority before committing a result. A cancelled, replaced or expired lease cannot post a late result. Transient provider failures are retried with backoff, at most three processing attempts and within a 15-minute run deadline. A retry after an interrupted inference can incur another inference charge; it cannot duplicate the resulting ledger action.

Pending actions create an expiry job in the same transaction as their reservation. The worker releases expired reservations even if the agent or institution is suspended. Failures preserve both the hold and work item for retry. The explicit expiry command remains available. Existing pending actions created before the worker release need explicit expiry; they do not retroactively acquire jobs.

The AWS template adds a separate worker Lambda, EventBridge schedule and bounded concurrency. Both API and worker may delete work-partition items only; no tenant-record deletion permission is granted. Worker metrics are `Heartbeats`, `FailedRuns` and `ExpiryRetries` in `Agentu/Worker`, dimensioned by stage. CloudWatch alarms also cover invocation errors and a missing five-minute heartbeat. They have no notification destination until an operations recipient is configured. The single work partition, one worker and bounded per-tick batch are an initial operating limit, not an established throughput/service commitment.

Bedrock is disabled by default. Configure an approved `arn:aws:bedrock:eu-west-2::foundation-model/...` using `deploy.py plan --bedrock-model-arn <arn>`. The worker IAM policy grants `bedrock:InvokeModel` to that ARN only. The API role has no model-invocation permission. The provider verifies account `032312375271` and a non-root identity before constructing the inference client. The model must support Converse tool use in London and be available to the business account; selection and live verification are still pending.

Omitting the model argument on an infrastructure update preserves its current value. An explicit empty value disables it. Apply infrastructure before releasing this version. Release tooling updates the worker first, then the API, and checks both code hashes before publishing static assets. The deployment identity remains guarded to account `032312375271`, region `eu-west-2`.

## Verified local walkthrough

The fictional **Agentu Platform Verification** institution started this phase with GBP 2,425,000 in Operating and GBP 75,000 in Reserve. Two separately signed-in development identities drafted/published **Reserve steward**, with an operating floor of GBP 1,250,000 and reserve target GBP 150,000. The worker proposed GBP 75,000, the requester could not approve, and the independent owner approved. Balances became GBP 2,350,000 and GBP 150,000, with no outstanding reservation. A second worker run correctly recorded no action. The browser also exercised a fictional external credential: it was masked, removed from the DOM after closing its one-time dialog, and revoked. Independent server-side exports verify 25 audit events and three balanced journals. Hosted execution remains unverified.

References: [Bedrock Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html), [DynamoDB transaction permissions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis-iam.html), [CloudWatch embedded metric format](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html).
