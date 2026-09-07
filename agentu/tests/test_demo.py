import copy
import json
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from domain import apply, fresh_state, verify, DomainError
from storage import FileStore
import handler


def command(scenario="sweep", **kwargs):
    return {"operation": "propose", "scenario": scenario, "request_id": str(uuid.uuid4()), **kwargs}


class PolicyTests(unittest.TestCase):
    def test_allowed_transfer_conserves_total(self):
        state, changed = apply(fresh_state(), command(), "presenter")
        self.assertTrue(changed)
        self.assertEqual(state["accounts"]["operating"]["balance"], 242500000)
        self.assertEqual(state["accounts"]["reserve"]["balance"], 97500000)
        self.assertEqual(sum(a["balance"] for a in state["accounts"].values()), 340000000)
        self.assertEqual(state["actions"][0]["status"], "executed")
        self.assertEqual(len(state["events"]), 3)
        self.assertTrue(verify(state["events"])["valid"])

    def test_unapproved_destination_is_blocked(self):
        initial = fresh_state()
        state, _ = apply(initial, command("blocked"), "presenter")
        self.assertEqual(state["accounts"], initial["accounts"])
        self.assertEqual(state["actions"][0]["status"], "blocked")
        self.assertEqual(len(state["events"]), 2)

    def test_approval_then_replay_moves_funds_once(self):
        state, _ = apply(fresh_state(), command("approval"), "presenter")
        self.assertEqual(state["accounts"]["operating"]["balance"], 250000000)
        self.assertEqual(state["actions"][0]["status"], "pending")
        approve = {"operation": "approve", "action_id": state["actions"][0]["id"], "note": "Reviewed for demo", "request_id": str(uuid.uuid4())}
        state, _ = apply(state, approve, "presenter")
        replay, changed = apply(state, approve, "presenter")
        self.assertFalse(changed)
        self.assertEqual(replay, state)
        self.assertEqual(state["accounts"]["operating"]["balance"], 232500000)
        with self.assertRaises(DomainError):
            apply(state, {**approve, "request_id": str(uuid.uuid4())}, "presenter")

    def test_decline_does_not_move_balances(self):
        state, _ = apply(fresh_state(), command("approval"), "presenter")
        state, _ = apply(state, {"operation": "decline", "action_id": state["actions"][0]["id"], "note": "Not required for demo", "request_id": str(uuid.uuid4())}, "presenter")
        self.assertEqual(state["accounts"], fresh_state()["accounts"])
        self.assertEqual(state["actions"][0]["status"], "declined")

    def test_approval_rechecks_changed_liquidity(self):
        state, _ = apply(fresh_state(), command("approval"), "presenter")
        pending_id = state["actions"][0]["id"]
        for _ in range(14):
            state, _ = apply(state, command(amount=10000000), "presenter")
        before = copy.deepcopy(state["accounts"])
        state, _ = apply(state, {"operation": "approve", "action_id": pending_id, "note": "Review current liquidity", "request_id": str(uuid.uuid4())}, "presenter")
        self.assertEqual(state["accounts"], before)
        self.assertEqual(state["actions"][0]["status"], "blocked")

    def test_hard_limit_wins_over_review(self):
        state, _ = apply(fresh_state(), command("approval", amount=50000001), "presenter")
        self.assertEqual(state["actions"][0]["status"], "blocked")

    def test_invalid_money_types_rejected(self):
        for amount in (0, -1, True, 1.2, "7500", None, 100000001):
            with self.subTest(amount=amount), self.assertRaises(DomainError):
                apply(fresh_state(), command(amount=amount), "presenter")

    def test_idempotency_key_reuse_rejected_for_different_input(self):
        payload = command()
        state, _ = apply(fresh_state(), payload, "presenter")
        with self.assertRaises(DomainError):
            apply(state, {**payload, "amount": 100}, "presenter")

    def test_tampered_reordered_or_missing_events_fail(self):
        state, _ = apply(fresh_state(), command(), "presenter")
        for transform in (lambda es: es[1]["data"].update(decision="blocked"), lambda es: es.reverse(), lambda es: es.pop(0)):
            events = copy.deepcopy(state["events"])
            transform(events)
            self.assertFalse(verify(events)["valid"])

    def test_approval_reason_required(self):
        state, _ = apply(fresh_state(), command("approval"), "presenter")
        with self.assertRaises(DomainError):
            apply(state, {"operation": "approve", "action_id": state["actions"][0]["id"], "request_id": str(uuid.uuid4()), "note": ""}, "presenter")

    def test_concurrent_retries_are_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            store = FileStore(folder)
            payload = command()
            with ThreadPoolExecutor(max_workers=10) as pool:
                list(pool.map(lambda _: store.transact("session", lambda s: apply(s, payload, "presenter")), range(20)))
            state = store.transact("session")
            self.assertEqual(len(state["actions"]), 1)
            self.assertEqual(state["accounts"]["operating"]["balance"], 242500000)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        handler.store = FileStore(self.temp.name)
        self.workspace = str(uuid.uuid4())

    def tearDown(self):
        self.temp.cleanup()

    def call(self, path="/api/state", actor="presenter", payload=None, **overrides):
        event = {"rawPath": path, "headers": {"X-Agentu-Workspace": self.workspace, "Content-Type": "application/json"},
                 "requestContext": {"http": {"method": "POST" if payload else "GET"}, "authorizer": {"jwt": {"claims": {"sub": actor}}}},
                 "body": json.dumps(payload) if payload else None}
        event.update(overrides)
        result = handler.handler(event, None)
        return result["statusCode"], json.loads(result["body"])

    def test_auth_is_required_except_health(self):
        self.assertEqual(self.call(actor=None)[0], 401)
        self.assertEqual(self.call("/api/health", actor=None)[0], 200)

    def test_rehearsals_are_isolated_by_identity(self):
        self.assertEqual(self.call("/api/actions", payload=command())[0], 200)
        _, other = self.call(actor="other-presenter")
        self.assertEqual(other["actions"], [])

    def test_rehearsals_are_isolated_by_workspace(self):
        self.call("/api/actions", payload=command())
        self.workspace = str(uuid.uuid4())
        self.assertEqual(self.call()[1]["actions"], [])

    def test_unknown_route_and_malformed_input(self):
        self.assertEqual(self.call("/api/nope")[0], 404)
        self.assertEqual(self.call("/api/actions", payload=command(), body="{")[0], 400)
        self.assertEqual(self.call(headers={})[0], 400)

    def test_export_matches_persisted_state(self):
        _, state = self.call("/api/actions", payload=command())
        _, exported = self.call("/api/export")
        self.assertEqual(state, exported)
        self.assertNotIn("requests", exported)


if __name__ == "__main__":
    unittest.main()
