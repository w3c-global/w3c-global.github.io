"""Deployment boundaries that protect credentials and accounting evidence."""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
for directory in ("infra", "scripts", "backend"):
    sys.path.insert(0, str(ROOT / directory))
from template import template
from deploy import model_parameter
from platform_core.store import DynamoBackend, UnitOfWork
import worker


class AgentDeploymentTests(unittest.TestCase):
    def test_machine_route_is_exact_and_administration_keeps_jwt(self):
        r = template()["Resources"]
        self.assertEqual("POST /api/agent/proposals", r["AgentProposalRoute"]["Properties"]["RouteKey"])
        self.assertNotIn("AuthorizerId", r["AgentProposalRoute"]["Properties"])
        self.assertEqual("JWT", r["PlatformRoute"]["Properties"]["AuthorizationType"])
        self.assertEqual("rate(1 minute)", r["WorkerSchedule"]["Properties"]["ScheduleExpression"])
        self.assertEqual(1, r["WorkerFunction"]["Properties"]["ReservedConcurrentExecutions"])

    def test_delete_permission_is_only_work_partition_and_model_scope_is_optional(self):
        r = template()["Resources"]
        for name in ("ApiRole", "WorkerRole"):
            statements = r[name]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
            deletes = [s for s in statements if s.get("Action") == "dynamodb:DeleteItem"]
            self.assertEqual(1, len(deletes))
            self.assertEqual(["WORK#platform"], deletes[0]["Condition"]["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"])
            self.assertEqual({"Fn::GetAtt": ["PlatformRecords", "Arn"]}, deletes[0]["Resource"])
        api_policy = json.dumps(r["ApiRole"])
        self.assertNotIn("bedrock:InvokeModel", api_policy)
        conditional = r["WorkerRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"][-1]["Fn::If"]
        self.assertEqual(("EnableBedrock", {"Ref": "BedrockModelArn"}), (conditional[0], conditional[1]["Resource"]))
        self.assertNotIn("TimeToLiveSpecification", r["PlatformRecords"]["Properties"])

    def test_model_parameter_preserves_choice_unless_explicitly_changed(self):
        existing = [{"ParameterKey": "BedrockModelArn", "ParameterValue": "selected-model"}]
        self.assertTrue(model_parameter(None, existing, True)["UsePreviousValue"])
        self.assertEqual("", model_parameter(None, [], True)["ParameterValue"])
        self.assertEqual("", model_parameter("", existing, True)["ParameterValue"])

    def test_dynamo_queue_delete_checks_claimed_version(self):
        client = Mock()
        client.get_item.return_value = {"Item": {"version": {"N": "4"}, "body": {"S": '{"kind":"agent_run"}'}}}
        backend = DynamoBackend("Platform", client)
        tx = UnitOfWork(backend)
        tx.delete_work("DUE#0000000000001#job")
        backend.commit(tx)
        item = client.transact_write_items.call_args.kwargs["TransactItems"][0]["Delete"]
        self.assertEqual("#version = :version", item["ConditionExpression"])
        self.assertEqual({":version": {"N": "4"}}, item["ExpressionAttributeValues"])
        self.assertEqual({"S": "WORK#platform"}, item["Key"]["pk"])

    def test_handled_failures_emit_metrics_and_heartbeat(self):
        context = Mock(aws_request_id="test-request")
        context.get_remaining_time_in_millis.return_value = 120000
        runner = Mock()
        runner.tick.return_value = [{"status": "failed"}, {"status": "retry"}, {"status": "completed"}]
        with patch.object(worker, "runner", runner), patch("sys.stdout", new_callable=io.StringIO) as output:
            worker.handler({}, context)
        result = json.loads(output.getvalue())
        self.assertEqual((1, 1, 1), (result["Heartbeats"], result["FailedRuns"], result["ExpiryRetries"]))
        self.assertEqual("Agentu/Worker", result["_aws"]["CloudWatchMetrics"][0]["Namespace"])
