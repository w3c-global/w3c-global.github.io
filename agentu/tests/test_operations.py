"""Monitoring must distinguish observed health from missing operational proof."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from botocore.exceptions import ClientError
from botocore.session import get_session
from botocore.validate import validate_parameters

ROOT = Path(__file__).resolve().parents[1]
for folder in ("infra", "scripts"):
    sys.path.insert(0, str(ROOT / folder))
from test_environments import fixture as environment_fixture
from environments import EXPECTED_ACCOUNT, REGION, STAGES
from monitoring import alarm_specs, dashboard, topic_policy
from operations import inspect
from template import template
from build import inline_template
import deploy

AT = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def fixture(stage="sandbox"):
    f = environment_fixture(stage)
    topic = f"arn:aws:sns:{REGION}:{EXPECTED_ACCOUNT}:agentu-{stage}-operations"
    name = f"agentu-{stage}-operations"
    f["values"].update(OperationsTopicArn=topic, OperationsDashboard=name)
    f["stack"]["Outputs"] = [{"OutputKey": k, "OutputValue": v} for k, v in f["values"].items()]
    alarms = [{"AlarmName": f"agentu-{stage}-{suffix}", "AlarmArn": f"arn:aws:cloudwatch:{REGION}:{EXPECTED_ACCOUNT}:alarm:agentu-{stage}-{suffix}",
               **props, "ActionsEnabled": True, "AlarmActions": [topic], "OKActions": [topic], "InsufficientDataActions": [], "StateValue": "OK"}
              for suffix, props in alarm_specs(stage, f["values"]).values()]
    attrs = {"TopicArn": topic, "Owner": EXPECTED_ACCOUNT, "Policy": json.dumps(topic_policy(stage, topic))}
    subscription = {"TopicArn": topic, "Owner": EXPECTED_ACCOUNT, "Protocol": "email", "Endpoint": "private-recipient@operations.example.test",
                    "SubscriptionArn": topic + ":11111111-2222-4333-8444-555555555555"}
    board = {"DashboardName": name, "DashboardArn": f"arn:aws:cloudwatch::{EXPECTED_ACCOUNT}:dashboard/{name}", "DashboardBody": json.dumps(dashboard(stage, f["values"]))}
    rule_name = f"agentu-{stage}-worker"
    rule = {"Name": rule_name, "Arn": f"arn:aws:events:{REGION}:{EXPECTED_ACCOUNT}:rule/{rule_name}", "State": "ENABLED", "ScheduleExpression": "rate(1 minute)"}
    target = {"Id": "AgentuWorker", "Arn": f"arn:aws:lambda:{REGION}:{EXPECTED_ACCOUNT}:function:{f['values']['WorkerFunctionName']}",
              "Input": '{"source":"agentu.worker"}', "RetryPolicy": {"MaximumEventAgeInSeconds": 300, "MaximumRetryAttempts": 2}}
    metrics = [{"Id": key, "Timestamps": [AT - timedelta(minutes=2)], "Values": [value], "StatusCode": "Complete"}
               for key, value in (("heartbeats", 1.0), ("api_requests", 10.0), ("api_server_errors", 0.0))]
    for service in ("cloudwatch", "sns", "events"):
        f["clients"][service] = Mock()
    cw, sns, events = (f["clients"][s] for s in ("cloudwatch", "sns", "events"))
    cw.describe_alarms.return_value = {"MetricAlarms": alarms}
    cw.get_dashboard.return_value = board
    cw.get_metric_data.return_value = {"MetricDataResults": metrics}
    sns.get_topic_attributes.return_value = {"Attributes": attrs}
    sns.list_subscriptions_by_topic.return_value = {"Subscriptions": [subscription]}
    events.describe_rule.return_value = rule
    events.list_targets_by_rule.return_value = {"Targets": [target]}
    f.update(topic=topic, alarms=alarms, attrs=attrs, subscription=subscription, board=board, rule=rule, target=target, metrics=metrics, cw=cw, sns=sns, events=events)
    return f


class OperationsTests(unittest.TestCase):
    def test_compact_deployment_preserves_the_full_template_within_the_api_limit(self):
        document = template()
        formatted = json.dumps(document, indent=2)
        self.assertGreater(len(formatted.encode("utf-8")), 51200)
        compact = inline_template(document)
        self.assertEqual(document, json.loads(compact))
        self.assertLessEqual(len(compact.encode("utf-8")), 51200)
        with self.assertRaisesRegex(ValueError, "inline limit"):
            inline_template({"Description": "\u00a3" * 9000})

    def test_planning_validates_the_exact_body_before_artifact_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            document = template()
            (out / "template.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
            f = fixture()
            cf = f["clients"]["cloudformation"]
            with patch.object(deploy, "OUT", out), patch.object(deploy, "build", return_value={"lambda_sha256": "a" * 64}), \
                 patch.object(deploy, "artifact_bucket", return_value="fictional-artifact-bucket") as bucket, patch("sys.stdout", new_callable=io.StringIO):
                cf.validate_template.side_effect = ClientError({"Error": {"Code": "ValidationError", "Message": "Rejected fixture"}}, "ValidateTemplate")
                with self.assertRaises(ClientError):
                    deploy.plan(f["session"], "sandbox")
                bucket.assert_not_called()
                f["clients"]["s3"].upload_file.assert_not_called()
                cf.create_change_set.assert_not_called()
                cf.validate_template.side_effect = None
                deploy.plan(f["session"], "sandbox")
                body = cf.validate_template.call_args.kwargs["TemplateBody"]
                self.assertEqual(body, cf.create_change_set.call_args.kwargs["TemplateBody"])
                self.assertEqual(document, json.loads(body))
                self.assertLessEqual(len(body.encode("utf-8")), 51200)

    def test_all_stages_report_observations_without_claiming_delivery_or_readiness(self):
        for stage in STAGES:
            f = fixture(stage)
            report = inspect(f["session"], stage, AT)
            self.assertTrue(report["configuration_verified"])
            self.assertTrue(report["monitoring_observed_ok"])
            self.assertEqual(11, len(report["alarms"]))
            self.assertEqual(0, report["observations"]["api_server_errors"]["sum"])
            for field in ("alert_delivery_verified", "hosted_journeys_verified", "cost_controls_verified"):
                self.assertFalse(report[field])
            self.assertNotIn(f["subscription"]["Endpoint"], json.dumps(report))
            used = {call[0] for c in (f["cw"], f["sns"], f["events"]) for call in c.method_calls}
            self.assertEqual({"describe_alarms", "get_dashboard", "get_metric_data", "get_topic_attributes", "list_subscriptions_by_topic", "describe_rule", "list_targets_by_rule"}, used)

    def test_alarm_drift_and_incomplete_inventory_are_rejected(self):
        for change in ({"MetricName": "Errors"}, {"Statistic": "Average"}, {"Threshold": 100}, {"ActionsEnabled": False},
                       {"AlarmActions": []}, {"OKActions": ["arn:aws:sns:eu-west-2:999999999999:personal"]}, {"Unit": "Bytes"},
                       {"Dimensions": [{"Name": "ApiId", "Value": "other-api"}]}, {"Metrics": [{"Id": "replacement"}]}):
            f = fixture()
            alarm = next(a for a in f["alarms"] if a["MetricName"] == "5xx")
            alarm.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                inspect(f["session"], "sandbox", AT)
        for duplicate in (False, True):
            f = fixture()
            f["alarms"].pop()
            if duplicate:
                f["alarms"].append(deepcopy(f["alarms"][0]))
            with self.assertRaisesRegex(ValueError, "inventory"):
                inspect(f["session"], "sandbox", AT)

    def test_account_topic_and_subscription_boundaries_are_verified(self):
        for field in ("stack", "topic", "policy", "subscription", "encryption"):
            f = fixture()
            if field == "stack":
                f["stack"]["StackId"] = f["stack"]["StackId"].replace(EXPECTED_ACCOUNT, "999999999999")
            elif field == "topic":
                f["attrs"]["Owner"] = "999999999999"
            elif field == "policy":
                policy = json.loads(f["attrs"]["Policy"])
                policy["Statement"][0]["Condition"]["ArnLike"]["aws:SourceArn"] = "*"
                f["attrs"]["Policy"] = json.dumps(policy)
            elif field == "subscription":
                f["subscription"]["Owner"] = "999999999999"
            else:
                f["attrs"]["KmsMasterKeyId"] = "alias/unreviewed-key"
            with self.subTest(field=field), self.assertRaises(ValueError):
                inspect(f["session"], "sandbox", AT)

    def test_pending_subscriptions_and_alarm_states_require_attention(self):
        f = fixture()
        f["subscription"]["SubscriptionArn"] = "PendingConfirmation"
        f["alarms"][0]["StateValue"] = "ALARM"
        f["alarms"][1]["StateValue"] = "INSUFFICIENT_DATA"
        report = inspect(f["session"], "sandbox", AT)
        self.assertFalse(report["monitoring_observed_ok"])
        self.assertEqual({"confirmed": 0, "pending_or_deleted": 1}, report["subscriptions"])
        self.assertEqual(3, len(report["attention"]))

    def test_missing_stale_and_partial_metrics_never_become_healthy_zeroes(self):
        for variant in ("missing", "stale", "partial", "zero-heartbeat", "recent-error"):
            f = fixture()
            if variant == "missing":
                for m in f["metrics"]:
                    m.update(Timestamps=[], Values=[])
            elif variant == "stale":
                for m in f["metrics"]:
                    m["Timestamps"] = [AT - timedelta(minutes=14)]
            elif variant == "partial":
                f["metrics"][0]["StatusCode"] = "PartialData"
            elif variant == "zero-heartbeat":
                f["metrics"][0]["Values"] = [0.0]
            else:
                f["metrics"][2]["Values"] = [1.0]
            report = inspect(f["session"], "sandbox", AT)
            self.assertFalse(report["monitoring_observed_ok"], variant)
            if variant == "missing":
                self.assertIsNone(report["observations"]["api_server_errors"]["sum"])

    def test_service_warnings_and_invalid_metric_values_are_rejected(self):
        for change in ({"Values": [float("nan")]}, {"Values": [-1]}, {"Values": []}, {"Timestamps": [AT + timedelta(minutes=1)]},
                       {"Id": "unexpected"}, {"Messages": [{"Code": "InternalError", "Value": "Retry later"}]}):
            f = fixture()
            f["metrics"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                inspect(f["session"], "sandbox", AT)
        f = fixture()
        f["cw"].get_metric_data.return_value["Messages"] = [{"Code": "DataLimitExceeded", "Value": "Results omitted"}]
        with self.assertRaisesRegex(ValueError, "warning"):
            inspect(f["session"], "sandbox", AT)

    def test_complete_paginated_lists_and_metrics_are_verified(self):
        f = fixture()
        f["cw"].describe_alarms.side_effect = [{"MetricAlarms": f["alarms"][:5], "NextToken": "alarm-page-2"}, {"MetricAlarms": f["alarms"][5:]}]
        f["sns"].list_subscriptions_by_topic.side_effect = [{"Subscriptions": [], "NextToken": "sub-page-2"}, {"Subscriptions": [f["subscription"]]}]
        earlier = deepcopy(f["metrics"])
        for item in earlier:
            item["Timestamps"] = [AT - timedelta(minutes=3)]
        f["cw"].get_metric_data.side_effect = [{"MetricDataResults": earlier, "NextToken": "metric-page-2"}, {"MetricDataResults": f["metrics"]}]
        report = inspect(f["session"], "sandbox", AT + timedelta(seconds=49))
        self.assertTrue(report["monitoring_observed_ok"])
        self.assertEqual(20, report["observations"]["api_requests"]["sum"])
        args = f["cw"].get_metric_data.call_args.kwargs
        self.assertEqual(AT, args["EndTime"])
        self.assertEqual("metric-page-2", args["NextToken"])

    def test_repeated_tokens_and_duplicate_metric_samples_stop_inspection(self):
        f = fixture()
        f["cw"].describe_alarms.return_value["NextToken"] = "repeated"
        with self.assertRaisesRegex(ValueError, "pagination"):
            inspect(f["session"], "sandbox", AT)
        f = fixture()
        f["cw"].get_metric_data.side_effect = [{"MetricDataResults": f["metrics"], "NextToken": "next"}, {"MetricDataResults": f["metrics"]}]
        with self.assertRaisesRegex(ValueError, "timestamp"):
            inspect(f["session"], "sandbox", AT)

    def test_disabled_or_redirected_worker_and_dashboard_drift_are_rejected(self):
        for variant in ("disabled", "target", "input", "extra-routing", "dashboard"):
            f = fixture()
            if variant == "disabled":
                f["rule"]["State"] = "DISABLED"
            elif variant == "target":
                f["target"]["Arn"] += "-other"
            elif variant == "input":
                f["target"]["Input"] = '{"source":"other"}'
            elif variant == "extra-routing":
                f["target"]["DeadLetterConfig"] = {"Arn": "arn:aws:sqs:eu-west-2:999999999999:unreviewed"}
            else:
                f["board"]["DashboardBody"] = '{"widgets":[]}'
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                inspect(f["session"], "sandbox", AT)

    def test_captured_requests_and_mock_responses_match_installed_sdk_models(self):
        f = fixture()
        inspect(f["session"], "sandbox", AT)
        cases = {"cloudwatch": {"describe_alarms": "DescribeAlarms", "get_dashboard": "GetDashboard", "get_metric_data": "GetMetricData"},
                 "sns": {"get_topic_attributes": "GetTopicAttributes", "list_subscriptions_by_topic": "ListSubscriptionsByTopic"},
                 "events": {"describe_rule": "DescribeRule", "list_targets_by_rule": "ListTargetsByRule"}}
        for service, methods in cases.items():
            model = get_session().get_service_model(service)
            for method, operation in methods.items():
                call = getattr(f["clients"][service], method)
                shape = model.operation_model(operation)
                validate_parameters(call.call_args.kwargs, shape.input_shape)
                validate_parameters(call.return_value, shape.output_shape)

    def test_template_covers_http_errors_and_both_tables_without_subscribing_people(self):
        r = template()["Resources"]
        alarm = r["ApiServerErrors"]["Properties"]
        self.assertEqual(("AWS/ApiGateway", "5xx", "Sum", 1), (alarm["Namespace"], alarm["MetricName"], alarm["Statistic"], alarm["Threshold"]))
        self.assertEqual("p95", r["ApiLatency"]["Properties"]["ExtendedStatistic"])
        for key, table in (("RecordsTable", "Records"), ("PlatformTable", "PlatformRecords")):
            for direction in ("Read", "Write"):
                p = r[key + direction + "Throttles"]["Properties"]
                self.assertEqual(direction + "ThrottleEvents", p["MetricName"])
                self.assertEqual([{"Name": "TableName", "Value": {"Ref": table}}], p["Dimensions"])
        self.assertFalse(any(v["Type"] == "AWS::SNS::Subscription" for v in r.values()))
        self.assertNotIn("Subscription", r["OperationsTopic"]["Properties"])
        policy = json.dumps(r["OperationsReadPolicy"])
        for forbidden in ("sns:Publish", "sns:Subscribe", "cloudwatch:Put", "cloudwatch:SetAlarmState", "dynamodb:", "lambda:Update", "events:Put"):
            self.assertNotIn(forbidden, policy)
        for role in ("ApiRole", "WorkerRole"):
            self.assertNotIn("sns:", json.dumps(r[role]))


if __name__ == "__main__":
    unittest.main()
