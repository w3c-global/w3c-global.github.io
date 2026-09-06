"""Deterministic demonstration controls. No model calls or financial integrations."""
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone

POLICY = {"version": "demo-policy-1", "auto_limit": 100_000_00, "hard_limit": 500_000_00,
          "liquidity_floor": 1_000_000_00, "currency": "GBP", "approved_destinations": ["reserve"]}
SCENARIOS = {
    "sweep": {"name": "Treasury sweep", "amount": 75_000_00, "destination": "reserve",
              "purpose": "Move surplus operating cash into the treasury reserve."},
    "blocked": {"name": "Unapproved beneficiary", "amount": 25_000_00, "destination": "external-unknown",
                "purpose": "Pay a newly supplied beneficiary that is not on the approved list."},
    "approval": {"name": "Large treasury transfer", "amount": 175_000_00, "destination": "reserve",
                 "purpose": "Increase the treasury reserve above the automatic execution limit."},
}


class DomainError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fresh_state():
    return {"version": 0, "created_at": now(), "accounts": {
        "operating": {"name": "Operating account", "balance": 2_500_000_00, "currency": "GBP"},
        "reserve": {"name": "Treasury reserve", "balance": 900_000_00, "currency": "GBP"}},
        "actions": [], "events": [], "requests": {}}


def append_event(state, kind, action_id, data):
    previous = state["events"][-1]["hash"] if state["events"] else "0" * 64
    event = {"sequence": len(state["events"]) + 1, "timestamp": now(), "kind": kind,
             "action_id": action_id, "data": copy.deepcopy(data), "previous_hash": previous}
    event["hash"] = hashlib.sha256(canonical(event).encode()).hexdigest()
    state["events"].append(event)


def verify(events):
    previous = "0" * 64
    for seq, event in enumerate(events, 1):
        unsigned = {k: v for k, v in event.items() if k != "hash"}
        if (event.get("sequence") != seq or event.get("previous_hash") != previous
                or hashlib.sha256(canonical(unsigned).encode()).hexdigest() != event.get("hash")):
            return {"valid": False, "count": len(events), "failed_sequence": seq, "head": previous}
        previous = event["hash"]
    return {"valid": True, "count": len(events), "head": previous,
            "scope": "Internal hash-chain consistency. Not an external signature or proof against an administrator rewriting the full record."}


def evaluate(amount, destination, balance):
    checks = [
        {"rule": "Approved destination", "result": "pass" if destination in POLICY["approved_destinations"] else "fail",
         "detail": "Treasury reserve is approved." if destination == "reserve" else "This beneficiary is not on the approved list."},
        {"rule": "Maximum transaction", "result": "pass" if amount <= POLICY["hard_limit"] else "fail",
         "detail": "No action may exceed £500,000."},
        {"rule": "Liquidity floor", "result": "pass" if balance - amount >= POLICY["liquidity_floor"] else "fail",
         "detail": "At least £1,000,000 must remain in the operating account."},
        {"rule": "Automatic execution limit", "result": "pass" if amount <= POLICY["auto_limit"] else "review",
         "detail": "Amounts above £100,000 require a demo operator’s approval."},
    ]
    decision = "blocked" if any(c["result"] == "fail" for c in checks) else (
        "pending" if any(c["result"] == "review" for c in checks) else "allowed")
    return checks, decision


def transfer(state, action):
    state["accounts"]["operating"]["balance"] -= action["amount"]
    state["accounts"]["reserve"]["balance"] += action["amount"]
    action["status"] = "executed"
    append_event(state, "simulated_transfer", action["id"], {"amount": action["amount"], "currency": "GBP",
                 "from": "operating", "to": "reserve", "balances": state["accounts"]})


def apply(state, payload, actor):
    if not isinstance(payload, dict):
        raise DomainError("A JSON object is required.")
    request_id = payload.get("request_id", "")
    if not isinstance(request_id, str) or not 16 <= len(request_id) <= 80:
        raise DomainError("A request ID of 16–80 characters is required.")
    fingerprint = hashlib.sha256(canonical({k: v for k, v in payload.items() if k != "request_id"}).encode()).hexdigest()
    if request_id in state["requests"]:
        if state["requests"][request_id] != fingerprint:
            raise DomainError("This request ID was already used for different input.", 409)
        return state, False
    if len(state["events"]) >= 240:
        raise DomainError("This rehearsal is full. Start a new rehearsal to continue.", 409)
    state = copy.deepcopy(state)
    operation = payload.get("operation")
    if operation == "propose":
        scenario = payload.get("scenario")
        if not isinstance(scenario, str) or scenario not in SCENARIOS:
            raise DomainError("Choose one of the three demo scenarios.")
        preset = SCENARIOS[scenario]
        amount = payload.get("amount", preset["amount"])
        if type(amount) is not int or amount <= 0 or amount > 1_000_000_00:
            raise DomainError("Amount must be a positive whole number of pence, up to £1,000,000.")
        action = {**preset, "id": str(uuid.uuid4()), "scenario": scenario, "amount": amount,
                  "created_at": now(), "agent": "Treasury agent (simulated)", "requested_by": actor,
                  "currency": "GBP", "source": "operating", "policy_version": POLICY["version"]}
        append_event(state, "request_received", action["id"], action)
        action["checks"], decision = evaluate(amount, action["destination"], state["accounts"]["operating"]["balance"])
        action["decision"] = decision
        action["status"] = decision
        append_event(state, "policy_evaluated", action["id"], {"policy": POLICY, "checks": action["checks"], "decision": decision})
        if decision == "allowed":
            transfer(state, action)
        state["actions"].append(action)
    elif operation in ("approve", "decline"):
        action = next((x for x in state["actions"] if x["id"] == payload.get("action_id")), None)
        if not action:
            raise DomainError("Action not found in this rehearsal.", 404)
        if action["status"] != "pending":
            raise DomainError("This action is no longer awaiting approval.", 409)
        note = payload.get("note", "")
        if not isinstance(note, str) or not 5 <= len(note.strip()) <= 500:
            raise DomainError("Record an approval or decline reason between 5 and 500 characters.")
        append_event(state, "operator_" + operation, action["id"], {"actor": actor, "note": note.strip(), "demo_role": "presenter"})
        action["review"] = {"actor": actor, "note": note.strip(), "outcome": operation, "timestamp": now()}
        if operation == "decline":
            action["status"] = "declined"
        else:
            checks, decision = evaluate(action["amount"], action["destination"], state["accounts"]["operating"]["balance"])
            action["approval_checks"] = checks
            append_event(state, "approval_rechecked", action["id"], {"checks": checks, "decision": decision})
            if decision == "blocked":
                action["status"] = "blocked"
                append_event(state, "execution_blocked", action["id"], {"reason": "A hard control failed when approval was processed."})
            else:
                transfer(state, action)
    else:
        raise DomainError("Unknown operation.")
    state["requests"][request_id] = fingerprint
    state["version"] += 1
    return state, True


def public_state(state):
    return {k: copy.deepcopy(v) for k, v in state.items() if k != "requests"} | {
        "policy": POLICY, "scenarios": SCENARIOS, "integrity": verify(state["events"]), "simulated": True}
