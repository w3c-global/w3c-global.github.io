import concurrent.futures
import copy
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from platform_core.errors import PlatformError, Conflict
from platform_core.model import Actor, digest
from platform_core.service import PlatformService
from platform_core.store import DocumentStore, SQLiteBackend, UnitOfWork
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_platform_export import verify


def key():
    return str(uuid.uuid4())


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DocumentStore(SQLiteBackend(Path(self.temp.name) / "platform.sqlite3"))
        self.service = PlatformService(self.store)
        self.owner = Actor(key(), "owner@example.test", True)
        self.tenant = self.service.create_institution(self.owner, {"name": "Test institution"}, key())["institution"]["id"]
        self.pk = "TENANT#" + self.tenant
        self.source = self.cmd("account_create", {"name": "Operating"})["account"]["id"]
        self.target = self.cmd("account_create", {"name": "Reserve"})["account"]["id"]
        self.cmd("sandbox_fund", {"account_id": self.source, "amount": 1_000_00, "reason": "Test opening capital"})

    def tearDown(self):
        self.temp.cleanup()

    def cmd(self, operation, body, actor=None, request_id=None):
        return self.service.command(self.tenant, actor or self.owner, operation, body, request_id or key())

    def member(self, role, address=None):
        actor = Actor(key(), address or key() + "@example.test", True)
        invite = self.cmd("invite_create", {"email": actor.email, "role": role})
        self.service.accept_invitation(actor, invite["invite_token"])
        return actor

    def propose(self, value=100_00, actor=None):
        return self.cmd("action_propose", {"source_id": self.source, "destination_id": self.target,
                        "amount": value, "purpose": "Treasury reserve allocation"}, actor)["action"]

    def account(self, account_id):
        return self.store.transact(lambda tx: tx.get(self.pk, "ACCOUNT#" + account_id))

    def publish(self, **overrides):
        admin = self.member("administrator")
        config = {"auto_limit": 0, "transaction_limit": 100_000_00, "daily_limit": 1_000_000_00,
                  "liquidity_floor": 0, "required_approvals": 1, "approval_minutes": 60, "currencies": ["GBP"]}
        config.update(overrides)
        draft = self.cmd("policy_create", {"name": "Updated mandate", "config": config})["policy"]
        self.cmd("policy_publish", {"policy_id": draft["id"], "reason": "Independent mandate review"}, admin)
        return draft

    def assert_error(self, code, fn):
        with self.assertRaises(PlatformError) as caught:
            fn()
        self.assertEqual(code, caught.exception.code)

    def test_pending_then_independent_approval_posts_balanced_journal(self):
        reviewer = self.member("approver")
        action = self.propose()
        self.assertEqual("pending", action["status"])
        self.assertEqual(100_00, self.account(self.source)["reserved"])
        self.assert_error("self_approval", lambda: self.cmd("action_approve", {"action_id": action["id"], "reason": "My own approval"}))
        settled = self.cmd("action_approve", {"action_id": action["id"], "reason": "Verified mandate and purpose"}, reviewer)["action"]
        self.assertEqual("settled", settled["status"])
        self.assertEqual((900_00, 0), (self.account(self.source)["balance"], self.account(self.source)["reserved"]))
        self.assertEqual(100_00, self.account(self.target)["balance"])
        for entry in self.service.collection(self.tenant, self.owner, "journal")["items"]:
            self.assertEqual(0, sum(p["debit"] - p["credit"] for p in entry["postings"]))

    def test_idempotency_survives_restarts_and_rejects_changed_payload(self):
        request_id = key()
        body = {"account_id": self.source, "amount": 150_00, "reason": "Persistent idempotency test"}
        first = self.cmd("sandbox_fund", body, request_id=request_id)
        reopened = PlatformService(DocumentStore(SQLiteBackend(Path(self.temp.name) / "platform.sqlite3")))
        self.assertEqual(first, reopened.command(self.tenant, self.owner, "sandbox_fund", body, request_id))
        self.assertEqual(1_150_00, self.account(self.source)["balance"])
        self.assert_error("idempotency_conflict", lambda: self.cmd("sandbox_fund", {**body, "amount": 1}, request_id=request_id))

    def test_concurrent_proposals_cannot_over_reserve(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            actions = list(executor.map(lambda _: self.propose(600_00), range(2)))
        self.assertEqual(["blocked", "pending"], sorted(a["status"] for a in actions))
        self.assertEqual(600_00, self.account(self.source)["reserved"])
        self.assertEqual(1_000_00, self.account(self.source)["balance"])

    def test_cross_institution_and_suspended_access_are_rejected(self):
        outsider = Actor(key(), "outsider@example.test", True)
        self.assert_error("forbidden", lambda: self.service.overview(self.tenant, outsider))
        operator = self.member("operator")
        self.assert_error("forbidden", lambda: self.cmd("sandbox_fund", {}, operator))
        self.cmd("member_update", {"sub": operator.sub, "status": "suspended"})
        self.assert_error("forbidden", lambda: self.propose(actor=operator))

    def test_revocation_blocks_replaying_previously_authorised_request(self):
        operator = self.member("operator")
        request_id = key()
        body = {"source_id": self.source, "destination_id": self.target, "amount": 1, "purpose": "Replay after revocation"}
        self.cmd("action_propose", body, operator, request_id)
        self.cmd("member_update", {"sub": operator.sub, "status": "suspended"})
        self.assert_error("forbidden", lambda: self.cmd("action_propose", body, operator, request_id))

    def test_policy_needs_independent_publisher_and_can_allow_automatic_transfer(self):
        draft = self.publish(auto_limit=200_00)
        self.assertEqual("settled", self.propose()["status"])
        draft = self.cmd("policy_create", {"name": "Same author", "config": draft["config"]})["policy"]
        self.assert_error("independent_review_required", lambda: self.cmd("policy_publish", {"policy_id": draft["id"], "reason": "Cannot approve own mandate"}))

    def test_new_policy_is_enforced_when_pending_action_is_approved(self):
        reviewer = self.member("approver")
        action = self.propose(300_00)
        self.publish(transaction_limit=200_00)
        result = self.cmd("action_approve", {"action_id": action["id"], "reason": "Review against new mandate"}, reviewer)
        self.assertEqual("blocked", result["action"]["status"])
        self.assertEqual(0, self.account(self.source)["reserved"])
        self.assertEqual(0, self.account(self.target)["balance"])

    def test_two_approvals_and_revoked_reviewer_does_not_count(self):
        self.publish(required_approvals=2)
        first, second, third = [self.member("approver") for _ in range(3)]
        action = self.propose()
        body = {"action_id": action["id"], "reason": "Independent financial control review"}
        self.assertEqual("pending", self.cmd("action_approve", body, first)["action"]["status"])
        self.assert_error("duplicate_approval", lambda: self.cmd("action_approve", body, first))
        self.cmd("member_update", {"sub": first.sub, "status": "suspended"})
        self.assertEqual("pending", self.cmd("action_approve", body, second)["action"]["status"])
        self.assertEqual("settled", self.cmd("action_approve", body, third)["action"]["status"])

    def test_revoked_originator_cannot_leave_an_executable_request(self):
        operator, reviewer = self.member("operator"), self.member("approver")
        action = self.propose(actor=operator)
        self.cmd("member_update", {"sub": operator.sub, "status": "suspended"})
        result = self.cmd("action_approve", {"action_id": action["id"], "reason": "Recheck originator authority"}, reviewer)
        self.assertEqual("blocked", result["action"]["status"])
        self.assertEqual(0, self.account(self.source)["reserved"])

    def test_daily_limit_counts_reservations_and_settlements(self):
        self.publish(transaction_limit=300_00, daily_limit=500_00)
        reviewer = self.member("approver")
        first = self.propose(300_00)
        self.assertEqual("blocked", self.propose(300_00)["status"])
        self.cmd("action_approve", {"action_id": first["id"], "reason": "Daily limit accounting"}, reviewer)
        self.assertEqual("blocked", self.propose(300_00)["status"])
        self.assertEqual("pending", self.propose(200_00)["status"])

    def test_pause_and_currency_mismatch_are_hard_blocks(self):
        self.cmd("institution_pause", {"status": "paused", "reason": "Operational incident control"})
        self.assertEqual("blocked", self.propose()["status"])
        self.cmd("institution_pause", {"status": "active", "reason": "Incident review completed"})
        euro = self.cmd("account_create", {"name": "EUR reserve", "currency": "EUR"})["account"]
        result = self.cmd("action_propose", {"source_id": self.source, "destination_id": euro["id"], "amount": 100, "purpose": "Currency mismatch test"})
        self.assertEqual("blocked", result["action"]["status"])
        self.assertEqual(0, self.account(euro["id"])["balance"])

    def test_cancel_decline_and_expiry_release_holds(self):
        reviewer = self.member("approver")
        for operation, actor, expected in [("action_cancel", self.owner, "cancelled"), ("action_decline", reviewer, "declined"), ("action_expire", reviewer, "expired")]:
            action = self.propose()
            body = {"action_id": action["id"], "reason": "Request is no longer needed"}
            if operation == "action_expire":
                with patch("platform_core.service.time.time", return_value=action["expires_at"] + 1):
                    result = self.cmd(operation, body, actor)
            else:
                result = self.cmd(operation, body, actor)
            self.assertEqual(expected, result["action"]["status"])
            self.assertEqual(0, self.account(self.source)["reserved"])
        self.assertEqual(0, self.service.overview(self.tenant, self.owner)["institution"]["pending_count"])

    def test_invitation_is_email_bound_revocable_and_token_is_not_listed(self):
        actor = Actor(key(), "invited@example.test", True)
        invite = self.cmd("invite_create", {"email": actor.email, "role": "auditor"})
        self.assertNotIn("token_hash", self.service.collection(self.tenant, self.owner, "invitations")["items"][0])
        self.assert_error("invitation_mismatch", lambda: self.service.accept_invitation(self.owner, invite["invite_token"]))
        self.cmd("invite_revoke", {"invitation_id": invite["invitation"]["id"]})
        self.assert_error("invitation_unavailable", lambda: self.service.accept_invitation(actor, invite["invite_token"]))

    def test_failed_posting_rolls_back_every_account_and_journal(self):
        before = self.account(self.source)
        def malformed(tx):
            return self.service._journal(tx, self.pk, self.owner, [
                {"account_id": self.source, "currency": "GBP", "debit": 100, "credit": 0},
                {"account_id": self.target, "currency": "GBP", "debit": 0, "credit": 99}], "invalid", "Imbalanced journal")
        with self.assertRaises(PlatformError):
            self.store.transact(malformed)
        self.assertEqual(before, self.account(self.source))
        self.assertEqual(1, len(self.service.collection(self.tenant, self.owner, "journal")["items"]))

    def test_audit_chain_and_pagination_are_contiguous(self):
        for _ in range(4):
            action = self.propose()
            self.cmd("action_cancel", {"action_id": action["id"], "reason": "Pagination evidence test"})
        events, after = [], None
        while True:
            page = self.service.collection(self.tenant, self.owner, "audit", after, 3)
            events.extend(page["items"])
            after = page["next_cursor"]
            if not after:
                break
        previous = "0" * 64
        for sequence, event in enumerate(events, 1):
            record = copy.deepcopy(event)
            value = record.pop("hash")
            self.assertEqual(sequence, record["sequence"])
            self.assertEqual(previous, record["previous_hash"])
            self.assertEqual(value, digest(record))
            previous = value
        self.assertEqual(previous, self.service.overview(self.tenant, self.owner)["institution"]["event_head"])

    def test_read_version_check_detects_permission_race(self):
        tx = UnitOfWork(self.store.backend)
        tx.get(self.pk, "MEMBER#" + self.owner.sub)
        self.store.transact(lambda other: other.put(self.pk, "MEMBER#" + self.owner.sub,
                           {**other.get(self.pk, "MEMBER#" + self.owner.sub), "status": "suspended"}))
        with self.assertRaises(Conflict):
            self.store.backend.commit(tx)

    def test_malformed_inputs_are_domain_errors(self):
        for body in [{"name": "Malformed", "currency": []}, {"name": None}]:
            with self.assertRaises(PlatformError):
                self.service.create_institution(self.owner, body, key())
        for value in [True, 1.5, -10, "500"]:
            with self.assertRaises(PlatformError):
                self.propose(value)
        with self.assertRaises(PlatformError):
            self.cmd("policy_create", {"name": "Malformed", "config": []})

    def test_independent_export_verifier_rejects_tamper_and_truncation(self):
        with self.assertRaises(ValueError):
            verify({"schema": "agentu.platform.export.v1", "items": [], "collection": "unsupported"})
        overview = self.service.overview(self.tenant, self.owner)["institution"]
        for collection in ("audit", "journal"):
            export = {"schema": "agentu.platform.export.v1", "institution_id": self.tenant, "mode": "sandbox",
                      "collection": collection, "as_of_sequence": overview["event_sequence"], "as_of_head": overview["event_head"],
                      "journal_sequence": overview["ledger_sequence"], "items": self.service.collection(self.tenant, self.owner, collection)["items"]}
            self.assertTrue(verify(export)["verified"])
            shortened = copy.deepcopy(export); shortened["items"].pop()
            with self.assertRaises(ValueError):
                verify(shortened)
            altered = copy.deepcopy(export)
            if collection == "audit":
                altered["items"][0]["actor"] = "another-actor"
            else:
                altered["items"][0]["postings"][0]["debit"] += 1
            with self.assertRaises(ValueError):
                verify(altered)


if __name__ == "__main__":
    unittest.main()
