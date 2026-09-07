# Operations monitoring and response

Each environment definition includes eleven CloudWatch alarms, a dashboard and a dedicated SNS operations topic. The alarms cover API invocation errors, HTTP server errors and latency, worker invocation/errors/expiry/heartbeat, and read/write throttling on both data tables. These resources are implemented and schema-checked; none has been deployed or verified in AWS yet.

## Signals and initial thresholds

| Signal | Alarm condition | First response |
| --- | --- | --- |
| API Lambda errors | At least 3 in 5 minutes | Inspect invocation logs and the deployed revision; establish the affected operations before retrying |
| HTTP API `5xx` | At least 1 in 5 minutes | Inspect API status/latency and application logs; retain original request IDs for uncertain operations |
| HTTP API latency | p95 over 2,000 ms in 2 of 3 five-minute periods | Check Lambda duration, database throttling and concurrent activity before changing capacity |
| Worker invocation errors | At least 1 in 5 minutes | Inspect the worker invocation and retained work items; avoid submitting replacement financial requests |
| Failed agent runs / expiry retries | At least 1 in 5 minutes | Inspect the failed run or held reservation and its evidence; use the existing retry/cancellation/expiry controls |
| Worker heartbeat | Fewer than 1 in 5 minutes; missing data breaches | Check the scheduled rule, exact target, Lambda status and logs; an enabled schedule alone does not establish execution |
| Table read/write throttle events | At least 1 in 5 minutes for either table/direction | Inspect request patterns and limits; keep idempotency keys and review capacity changes in the business account |

These are initial investigation thresholds, not service commitments or spending caps. Review them against observed business traffic before a pilot. The HTTP API metrics use the `ApiId` and `$default` stage dimensions; they do not require paid route-level metrics. See [API Gateway metric definitions](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-metrics.html). Table alarms use separate read/write throttle-event metrics so an operation-specific request metric cannot hide throttled events; see [DynamoDB metric definitions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/metrics-dimensions.html).

## Dashboard and access

The stack outputs `OperationsDashboard`, `OperationsUrl`, `OperationsTopicArn` and `OperationsReadPolicyArn`. Open the dashboard through the authenticated business AWS console. It displays one environment's metrics with the corresponding thresholds; missing lines do not mean zero errors.

The unattached `OperationsReadPolicy` permits describing its stack, alarms, dashboard and scheduled worker, reading metric data in London, and inspecting its topic and subscription metadata. It grants no financial-record reads/writes, alert publication, subscriptions, alarm changes or deployment permission. `GetMetricData` requires a wildcard resource; the policy restricts its region, and the inspector binds every query to account `032312375271` and the selected environment. This does not make the IAM metric-read permission exclusive to one stage. Assign the policy to a designated business operations identity after reviewing its other inherited permissions.

## Read-only inspection

After deployment, with a non-root business AWS profile:

```text
python agentu/scripts/operations.py --stage sandbox --profile agentu --out .build/operations-sandbox-01.json
python agentu/scripts/operations.py --stage staging --profile agentu --require-healthy
```

The output file must be new. The command changes no AWS resource and publishes no message. It checks the complete named alarm inventory, metric/statistic/threshold and missing-data settings, enabled alarm actions, account/stage bindings, topic policy, subscription confirmation counts, dashboard contents and the worker's schedule/target. Recipient addresses and notification endpoints are omitted from the report.

The inspector reads a fifteen-minute metric window ending at the last complete minute. It requires a positive worker heartbeat and API request sample within ten minutes, recent explicit API error data, complete results, all alarms `OK` and a confirmed business subscription for `monitoring_observed_ok=true`. Missing/error samples remain unknown; old zeroes cannot establish current health. Invalid metrics, service warnings, conflicting pagination or drift stop inspection. `--require-healthy` exits nonzero when the observed monitoring conditions are not met.

Even an observed OK result leaves `alert_delivery_verified`, `hosted_journeys_verified` and `cost_controls_verified` false. It is a point-in-time observation of these signals, not a financial integrity check, backlog proof, uptime guarantee, model/bank test or delivery receipt. Run the application, export-integrity and recovery checks separately.

## Configure and verify delivery

Alarms route both `ALARM` and `OK` transitions to the environment topic. Its service policy allows CloudWatch publication only from that account, region and stage's alarm prefix. Runtime and release identities have no SNS publication permission. The topic contains operational metric metadata, not financial exports. It has no custom KMS key configured; adding one requires reviewing and testing CloudWatch's key permissions. The inspector stops on unreviewed key configuration.

The template creates **no subscription**, supplies no recipient and sends no invitation or test message. Delivery remains unfinished until an authorised business recipient/channel is chosen and configured. A confirmed subscription only establishes its configuration, not receipt. See [CloudWatch notification setup and source restrictions](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Notify_Users_Alarm_Changes.html).

With explicit authorisation for the selected recipient and a scheduled test, verify receipt of a controlled test, a real alarm transition and recovery notification. Record the environment, exact alarm, transition times and recipient acknowledgement. Keep financial records and credentials out of test messages. Do not infer delivery from an SNS topic or confirmation count.

## Costs, incidents and recovery

The dashboard and alarms can incur AWS charges after deployment. Before cloud readiness, agree the budget threshold, budget scope and business recipient, configure them in the business account, and verify notification delivery. No budget, recipient or paid-capacity reservation has been created by this work. API throttling is not a spending cap.

For an incident, record the affected stage and deployed revision; inspect account and operation state before replaying requests. Retain evidence and use existing idempotency keys for uncertain outcomes. Pause affected institution operations when investigation requires it, through the existing authorised application control. A monitoring alert does not itself suspend operations or reverse journals. Follow [AWS release/rollback operations](aws-operations.md) and [backup and recovery](recovery.md); hosted restoration remains to be exercised.

Automated tests use mocked AWS responses and validate their request/response shapes against the installed SDK. They cover all four stages, incorrect routing/ownership, alarm drift, missing/stale/partial metrics, pending recipients, disabled/redirected workers, dashboard drift, pagination and limited read permissions. The build preserves a readable template while sending an equivalent compact body under CloudFormation's inline limit; oversize or invalid plans stop before artifact mutations. Live metrics, rendered AWS dashboards, recipient delivery, cost controls and incident/restore drills remain unverified.
