from contextlib import closing
import json
import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from platform_core.agents import AgentService
from platform_core.api import AgentAPI, PlatformAPI
from platform_core.errors import PlatformError
from platform_core.jobs import QUEUE
from platform_core.model import Actor
from platform_core.providers import BedrockProvider, ProviderError, TreasuryRule
from platform_core.runner import Runner
from platform_core.store import DocumentStore, SQLiteBackend

key = lambda: str(uuid.uuid4())


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DocumentStore(SQLiteBackend(Path(self.temp.name) / "agents.sqlite3"))
        self.service = AgentService(self.store)
        self.owner, self.reviewer = Actor(key(), "owner@example.test", True), Actor(key(), "reviewer@example.test", True)
        self.tenant = self.service.create_institution(self.owner, {"name": "Agent institution"}, key())["institution"]["id"]
        self.pk = "TENANT#" + self.tenant
        invitation = self.cmd("invite_create", {"email": self.reviewer.email, "role": "owner"})
        self.service.accept_invitation(self.reviewer, invitation["invite_token"])
        self.source = self.cmd("account_create", {"name": "Operating"})["account"]["id"]
        self.target = self.cmd("account_create", {"name": "Reserve"})["account"]["id"]
        self.cmd("sandbox_fund", {"account_id": self.source, "amount": 250_000_000, "reason": "Agent test opening balance"})
        self.runner = Runner(self.service)

    def tearDown(self):
        self.temp.cleanup()

    def cmd(self, operation, body, actor=None, request_id=None):
        return self.service.command(self.tenant, actor or self.owner, operation, body, request_id or key())

    def get(self, prefix, record_id):
        return self.store.transact(lambda tx: tx.get(self.pk, prefix + "#" + record_id))

    def config(self, provider="treasury_rule", **overrides):
        return {"provider": provider, "objective": "Keep the reserve funded without breaching the operating floor.",
                "source_ids": [self.source], "destination_ids": [self.target], "currencies": ["GBP"],
                "transaction_limit": 10_000_000, "daily_limit": 20_000_000, "daily_runs": 20, "require_review": True,
                "source_floor": 125_000_000, "destination_target": 7_500_000, **overrides}

    def agent(self, provider="treasury_rule", **overrides):
        draft = self.cmd("agent_create", {"name": "Treasury agent", "config": self.config(provider, **overrides)})["agent"]
        return self.cmd("agent_publish", {"agent_id": draft["id"], "reason": "Independently reviewed agent scope and limits"}, self.reviewer)["agent"]

    def start_run(self, agent):
        return self.cmd("agent_run", {"agent_id": agent["id"]})["run"]

    def credentials(self, agent):
        return self.cmd("agent_key_create", {"agent_id": agent["id"], "label": "External test client", "days": 7})

    def proposal(self, value=7_500_000, **overrides):
        return {"source_id": self.source, "destination_id": self.target, "amount": value, "purpose": "External agent treasury allocation", **overrides}

    def test_agent_mandate_needs_independent_publication(self):
        draft = self.cmd("agent_create", {"name": "Draft agent", "config": self.config()})["agent"]
        with self.assertRaises(PlatformError) as error:
            self.cmd("agent_publish", {"agent_id": draft["id"], "reason": "Attempted self-publication"})
        self.assertEqual("independent_review_required", error.exception.code)
        self.assertEqual("draft", self.get("AGENT", draft["id"])["status"])

    def test_agent_review_setting_and_current_sponsor_apply_at_settlement(self):
        config = self.service.overview(self.tenant, self.owner)["policy"]["config"]
        draft = self.cmd("policy_create", {"name": "Automatic small transfers", "config": {**config, "auto_limit": 8_000_000}})["policy"]
        self.cmd("policy_publish", {"policy_id": draft["id"], "reason": "Independent small transfer mandate"}, self.reviewer)
        automatic = self.agent(require_review=False)
        run = self.start_run(automatic); self.runner.tick()
        self.assertEqual("settled", self.get("RUN", run["id"])["decision"])
        reviewed = self.agent(destination_target=10_000_000)
        run = self.start_run(reviewed); self.runner.tick()
        action = self.get("ACTION", self.get("RUN", run["id"])["action_id"])
        checks = {c["rule"]: c["result"] for c in action["checks"]}
        self.assertEqual(("pass", "review", "pending"), (checks["Automatic limit"], checks["Agent independent review"], action["status"]))
        self.cmd("member_update", {"sub": self.owner.sub, "role": "auditor", "status": "active", "reason": "Remove requester's run authority"}, self.reviewer)
        result = self.cmd("action_approve", {"action_id": action["id"], "reason": "Recheck after sponsor authority changed"}, self.reviewer)["action"]
        self.assertEqual("blocked", result["status"])
        self.assertEqual(0, self.get("ACCOUNT", self.source)["reserved"])

    def test_expiry_failure_retains_the_hold_and_work_for_retry(self):
        pending = self.cmd("action_propose", self.proposal())["action"]
        with patch("time.time", return_value=pending["expires_at"] + 1):
            with patch.object(self.service, "_finish_pending", side_effect=PlatformError("Reservation inconsistency", 409, "reservation_inconsistent")):
                self.assertEqual("retry", self.runner.process(pending["expiry_job_key"])["status"])
        self.assertEqual(pending["amount"], self.get("ACCOUNT", self.source)["reserved"])
        job = self.store.transact(lambda tx: tx.get(QUEUE, pending["expiry_job_key"]))
        self.assertEqual("reservation_inconsistent", job["last_error"]["code"])

    def test_real_model_connection_refuses_wrong_business_account_before_inference(self):
        model = BedrockProvider("arn:aws:bedrock:eu-west-2::foundation-model/amazon.nova-lite-v1:0")
        session = Mock()
        session.client.return_value.get_caller_identity.return_value = {"Account": "111111111111", "Arn": "arn:aws:iam::111111111111:role/OtherAccount"}
        with patch("boto3.Session", return_value=session):
            with self.assertRaises(ProviderError) as error:
                model.connect()
        self.assertEqual("provider_account_mismatch", error.exception.code)
        session.client.assert_called_once_with("sts")

    def test_rule_runs_real_balances_and_does_not_duplicate_pending_target(self):
        agent = self.agent()
        run = self.start_run(agent)
        self.runner.tick()
        completed = self.get("RUN", run["id"])
        self.assertEqual(("completed", "pending"), (completed["status"], completed["decision"]))
        action = self.get("ACTION", completed["action_id"])
        self.assertEqual(7_500_000, action["amount"])
        self.assertEqual(7_500_000, self.get("ACCOUNT", self.source)["reserved"])
        second = self.start_run(agent); self.runner.tick()
        self.assertEqual("no_action", self.get("RUN", second["id"])["status"])
        with self.assertRaises(PlatformError) as error:
            self.cmd("action_approve", {"action_id": action["id"], "reason": "Initiator cannot approve their agent"})
        self.assertEqual("self_approval", error.exception.code)
        self.cmd("action_approve", {"action_id": action["id"], "reason": "Independent review of the agent proposal"}, self.reviewer)
        self.assertEqual(242_500_000, self.get("ACCOUNT", self.source)["balance"])
        self.assertEqual(7_500_000, self.get("ACCOUNT", self.target)["balance"])

    def test_model_or_rule_output_is_rechecked_after_balance_changes(self):
        agent = self.agent(source_floor=245_000_000)
        run = self.start_run(agent)
        claimed = self.runner.claim(run["job_key"])
        result = TreasuryRule().propose(claimed["context"])
        # A separate human proposal reserves funds while the provider works.
        self.cmd("action_propose", self.proposal(4_000_000))
        self.runner.finish(claimed, result)
        completed = self.get("RUN", run["id"])
        self.assertEqual("blocked", completed["decision"])
        self.assertEqual(4_000_000, self.get("ACCOUNT", self.source)["reserved"])

    def test_external_key_is_hashed_once_and_agent_api_cannot_administer(self):
        agent = self.agent("external")
        request_id = key(); body = {"agent_id": agent["id"], "label": "One-time key"}
        first = self.cmd("agent_key_create", body, request_id=request_id)
        replay = self.cmd("agent_key_create", body, request_id=request_id)
        self.assertNotIn("agent_token", replay)
        public = self.service.collection(self.tenant, self.owner, "agent_keys")["items"]
        self.assertNotIn("secret_hash", public[0])
        with closing(self.store.backend.connect()) as connection:
            records = connection.execute("SELECT body FROM documents").fetchall()
        self.assertNotIn(first["agent_token"], str(records))
        event = {"rawPath": "/api/agent/proposals", "requestContext": {"http": {"method": "POST"}},
                 "headers": {"Authorization": "Bearer " + first["agent_token"], "Content-Type": "application/json", "Idempotency-Key": key()},
                 "body": json.dumps(self.proposal())}
        self.assertEqual(200, AgentAPI(self.service).handle(event)["statusCode"])
        machine = Actor("agent_" + agent["id"], "", True, "agent")
        self.assertEqual(401, PlatformAPI(self.service).handle({"rawPath": "/api/platform/me"}, machine)["statusCode"])
        with self.assertRaises(PlatformError):
            self.cmd("member_update", {"sub": machine.sub, "role": "owner"})

    def test_external_proposal_scope_replay_and_revocation(self):
        agent = self.agent("external", transaction_limit=5_000_000)
        credential = self.credentials(agent)
        token = credential["agent_token"]
        first = self.service.submit_agent(token, self.proposal(6_000_000), key())
        self.assertEqual("blocked", first["action"]["status"])
        request_id = key(); body = self.proposal(4_000_000)
        pending = self.service.submit_agent(token, body, request_id)
        self.assertEqual(pending, self.service.submit_agent(token, body, request_id))
        self.assertEqual(4_000_000, self.get("ACCOUNT", self.source)["reserved"])
        self.cmd("agent_key_revoke", {"agent_id": agent["id"], "key_id": credential["credential"]["id"], "reason": "Credential revocation test"})
        with self.assertRaises(PlatformError):
            self.service.submit_agent(token, body, request_id)
        reviewed = self.cmd("action_approve", {"action_id": pending["action"]["id"], "reason": "Recheck revoked machine credential"}, self.reviewer)
        self.assertEqual("blocked", reviewed["action"]["status"])
        self.assertEqual(0, self.get("ACCOUNT", self.source)["reserved"])

    def test_republished_mandate_does_not_expand_existing_credential(self):
        agent = self.agent("external")
        credential = self.credentials(agent)
        self.cmd("agent_revision", {"agent_id": agent["id"], "config": self.config("external", transaction_limit=9_000_000)})
        self.cmd("agent_publish", {"agent_id": agent["id"], "reason": "Independent revision review"}, self.reviewer)
        with self.assertRaises(PlatformError) as error:
            self.service.submit_agent(credential["agent_token"], self.proposal(), key())
        self.assertEqual("agent_unauthenticated", error.exception.code)

    def test_cancellation_and_suspension_during_provider_call_prevent_action(self):
        agent = self.agent()
        first = self.start_run(agent); claimed = self.runner.claim(first["job_key"])
        result = TreasuryRule().propose(claimed["context"])
        self.cmd("agent_run_cancel", {"run_id": first["id"], "reason": "Cancel while provider is running"})
        self.assertEqual("ignored", self.runner.finish(claimed, result)["status"])
        second = self.start_run(agent)
        class SuspendingProvider:
            def propose(provider, context):
                self.cmd("agent_suspend", {"agent_id": agent["id"], "reason": "Revoke during provider execution"})
                return TreasuryRule().propose(context)
        runner = Runner(self.service, {"treasury_rule": SuspendingProvider()})
        runner.process(second["job_key"])
        self.assertEqual("failed", self.get("RUN", second["id"])["status"])
        self.assertEqual(0, self.get("ACCOUNT", self.target)["balance"])
        self.assertEqual(0, self.get("ACCOUNT", self.source)["reserved"])

    def test_lease_recovery_ignores_late_completion_and_posts_once(self):
        agent = self.agent(); run = self.start_run(agent)
        first = self.runner.claim(run["job_key"])
        self.assertIsNone(self.runner.claim(run["job_key"]))
        advanced = time.time() + 151
        with patch("platform_core.runner.time.time", return_value=advanced):
            second = self.runner.claim(run["job_key"])
            result = TreasuryRule().propose(second["context"])
            self.assertEqual("ignored", self.runner.finish(first, result)["status"])
            self.assertEqual("completed", self.runner.finish(second, result)["status"])
            self.assertEqual("ignored", self.runner.finish(second, result)["status"])
        self.assertEqual(1, self.service.overview(self.tenant, self.owner)["institution"]["action_count"])

    def test_worker_releases_expired_reservations_without_user_access(self):
        agent = self.agent(); run = self.start_run(agent); self.runner.tick()
        action = self.get("ACTION", self.get("RUN", run["id"])["action_id"])
        self.cmd("agent_suspend", {"agent_id": agent["id"], "reason": "Stop before automatic expiry"})
        with patch("platform_core.runner.time.time", return_value=action["expires_at"] + 1):
            self.runner.tick()
        self.assertEqual("expired", self.get("ACTION", action["id"])["status"])
        self.assertEqual(0, self.get("ACCOUNT", self.source)["reserved"])
        self.assertEqual(0, self.service.overview(self.tenant, self.owner)["institution"]["pending_count"])
        self.assertEqual([], self.runner.due()[0])

    def test_run_budget_and_invalid_provider_decisions(self):
        agent = self.agent(daily_runs=1)
        run = self.start_run(agent)
        with self.assertRaises(PlatformError) as error:
            self.start_run(agent)
        self.assertEqual("agent_run_budget", error.exception.code)
        provider = Mock()
        provider.propose.return_value = {"decision": {"action": "approve_payment", "amount": 10}}
        Runner(self.service, {"treasury_rule": provider}).process(run["job_key"])
        self.assertEqual("failed", self.get("RUN", run["id"])["status"])
        self.assertEqual(0, self.get("ACCOUNT", self.source)["reserved"])

    def test_transient_provider_error_retries_then_completes(self):
        agent = self.agent(); run = self.start_run(agent)
        provider = Mock()
        provider.propose.side_effect = ProviderError("Temporarily unavailable", "provider_throttled", True)
        runner = Runner(self.service, {"treasury_rule": provider})
        self.assertEqual("queued", runner.process(run["job_key"])["status"])
        with patch("platform_core.runner.time.time", return_value=time.time() + 31):
            self.runner.process(run["job_key"])
        self.assertEqual("completed", self.get("RUN", run["id"])["status"])

    def test_bedrock_request_is_bounded_and_output_tools_are_validated(self):
        agent = self.agent(); run = self.start_run(agent); claimed = self.runner.claim(run["job_key"])
        client = Mock()
        client.converse.return_value = {"stopReason": "tool_use", "output": {"message": {"content": [{"toolUse": {
            "name": "propose_transfer", "toolUseId": "tool-1", "input": self.proposal()}}]}}, "usage": {"inputTokens": 12, "outputTokens": 9}}
        provider = BedrockProvider("arn:aws:bedrock:eu-west-2::foundation-model/test.fixture-v1:0", client)
        output = provider.propose(claimed["context"])
        self.assertEqual("propose_transfer", output["decision"]["action"])
        arguments = client.converse.call_args.kwargs
        self.assertEqual(600, arguments["inferenceConfig"]["maxTokens"])
        self.assertEqual(["propose_transfer", "no_action"], [tool["toolSpec"]["name"] for tool in arguments["toolConfig"]["tools"]])
        client.converse.return_value["output"]["message"]["content"][0]["toolUse"]["name"] = "change_policy"
        with self.assertRaises(ProviderError):
            provider.propose(claimed["context"])
        with self.assertRaises(ProviderError):
            BedrockProvider("arn:aws:bedrock:us-east-1::foundation-model/test.fixture", client).propose(claimed["context"])

    def test_deletion_is_limited_to_transient_jobs(self):
        with self.assertRaises(PlatformError):
            self.store.transact(lambda tx: tx.delete_work("JOURNAL#0001"))
