"""Independent verification of a consistent SQLite platform snapshot.

Uses stored evidence and arithmetic, never calls the application's write service.
This is an integrity check, not authentication of a bank or an external signature.
"""
import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from verify_platform_export import verify as verify_export
from verify_reconciliation_export import verify as verify_comparison

ZERO = "0" * 64
HISTORIES = {"actions": "ACTION#", "runs": "RUN#", "reconciliations": "RECON#"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def collect(records, prefix):
    return [v for k, v in sorted(records.items()) if k.startswith(prefix)]


def meter_check(records, prefix, expected, fields):
    keys = set(expected) | {k for k in records if k.startswith(prefix)}
    for key in keys:
        record = records.get(key, {})
        for field in fields:
            actual = record.get(field, 0)
            check(integer(actual) and actual == expected.get(key, {}).get(field, 0), f"Meter disagrees with operations: {key}/{field}.")


def verify_institution(tenant_id, records, work):
    meta = records.get("META")
    check(isinstance(meta, dict) and meta.get("id") == tenant_id, "Institution metadata is missing or inconsistent.")
    for collection, prefix in (("audit", "EVENT#"), ("journal", "JOURNAL#")):
        rows = collect(records, prefix)
        check(all(records.get(prefix + f"{i:020d}") == row for i, row in enumerate(rows, 1)), "Journal or audit storage key disagrees with its sequence.")
        verify_export({"schema": "agentu.platform.export.v1", "collection": collection, "items": rows,
                       "institution_id": tenant_id, "mode": meta["mode"], "as_of_sequence": meta["event_sequence"],
                       "as_of_head": meta["event_head"], "journal_sequence": meta["ledger_sequence"]})
    for prefix, field in (("ACCOUNT#", "id"), ("ACTION#", "id"), ("POLICY#", "id"), ("AGENT#", "id"), ("RUN#", "id"), ("RECON#", "id"), ("MEMBER#", "sub")):
        check(all(key == prefix + value[field] for key, value in records.items() if key.startswith(prefix)), "Domain storage key disagrees with record identity.")
    accounts = {a["id"]: a for a in collect(records, "ACCOUNT#")}
    actions = {a["id"]: a for a in collect(records, "ACTION#")}
    journals = {j["sequence"]: j for j in collect(records, "JOURNAL#")}
    posting_events = [event["data"] for event in collect(records, "EVENT#")
                      if event["kind"] in ("sandbox_funding_posted", "internal_transfer_posted", "journal_reversal_posted")]
    check(len(posting_events) == len(journals) and {j["sequence"]: j for j in posting_events} == journals,
          "Stored journals disagree with their hash-linked posting audit evidence.")
    members = collect(records, "MEMBER#")
    check(meta["account_count"] == sum(a["kind"] == "asset" for a in accounts.values()), "Account count disagrees with records.")
    check(meta["action_count"] == len(actions), "Action count disagrees with records.")
    check(meta["pending_count"] == sum(a["status"] == "pending" for a in actions.values()), "Pending count disagrees with records.")
    check(all(integer(meta[field]) for field in ("account_count", "action_count", "pending_count", "owner_count", "event_sequence", "ledger_sequence")), "Institution counters must be nonnegative integers.")
    owners = sum(m["role"] == "owner" and m["status"] == "active" for m in members)
    check(meta["owner_count"] == owners and owners > 0, "Independent access recovery needs the recorded active owners.")
    policy = records.get("POLICY#" + meta["policy_id"])
    check(policy and policy["status"] == "published", "Active policy is missing or not published.")
    balances, reserved = defaultdict(int), defaultdict(int)
    usage = defaultdict(lambda: defaultdict(int))
    agent_usage = defaultdict(lambda: defaultdict(int))
    destinations = defaultdict(lambda: defaultdict(int))
    for sequence, journal in journals.items():
        for posting in journal["postings"]:
            account = accounts.get(posting["account_id"])
            check(account and account["currency"] == posting["currency"], "Journal account or currency is inconsistent.")
            delta = posting["debit"] - posting["credit"]
            balances[account["id"]] += delta if account["normal_side"] == "debit" else -delta
        if journal["reference"] == "sandbox-funding":
            check(meta["mode"] == "sandbox", "Manual funding appears outside sandbox mode.")
        else:
            action = actions.get(journal["reference"])
            check(action and action["status"] == "settled" and action.get("journal_sequence") == sequence, "Journal is not linked to its settled action.")
        if journal.get("reversal_of"):
            original = journals.get(journal["reversal_of"]["sequence"])
            check(original and original["sequence"] < sequence and original["id"] == journal["reversal_of"]["id"] and digest(original) == journal["reversal_of"]["hash"], "Reversal source hash or identity is inconsistent.")
            inverse = [{**p, "debit": p["credit"], "credit": p["debit"]} for p in original["postings"]]
            check(sorted(inverse, key=lambda p: p["account_id"]) == sorted(journal["postings"], key=lambda p: p["account_id"]), "Reversal postings do not invert the original.")
    legacy_expiries = 0
    for action in actions.values():
        check(action["status"] in ("pending", "settled", "blocked", "cancelled", "declined", "expired"), "Unknown action state.")
        check(integer(action["amount"], 1) and action["source_id"] != action["destination_id"], "Invalid action amount or accounts.")
        for field in ("source_id", "destination_id"):
            check(action[field] in accounts and accounts[action[field]]["currency"] == action["currency"], "Action account or currency is inconsistent.")
        check(type(action["reserved"]) is bool and action["reserved"] == (action["status"] == "pending"), "Action hold disagrees with its lifecycle.")
        check(records.get("MEMBER#" + action["proposed_by"]) and records.get("POLICY#" + action["policy_id"]), "Action authority or policy record is missing.")
        check(action["policy_snapshot"] == records["POLICY#" + action["policy_id"]]["config"], "The action's policy snapshot disagrees with its retained policy version.")
        approvals = action["approvals"]
        reviewers = [a["sub"] for a in approvals]
        check(len(set(reviewers)) == len(reviewers) and not set(reviewers) & {action["proposed_by"], action.get("initiated_by")}, "Approval identities are duplicated or not independent.")
        check(all(records.get("MEMBER#" + reviewer) for reviewer in reviewers), "An approval member record is missing.")
        if action["status"] == "settled":
            journal = journals.get(action.get("journal_sequence"))
            expected = [{"account_id": action["source_id"], "debit": 0, "credit": action["amount"], "currency": action["currency"]},
                        {"account_id": action["destination_id"], "debit": action["amount"], "credit": 0, "currency": action["currency"]}]
            check(journal and journal["reference"] == action["id"] and journal["postings"] == expected, "Settled action and journal postings disagree.")
            check(journal.get("reversal_of") == action.get("reversal_of"), "Action and journal reversal evidence disagree.")
            check(all(c["result"] in ("pass", "review") for c in action["checks"]), "A settled action contains a failed control.")
            if action.get("reversal_of") or any(c["result"] == "review" for c in action["checks"]):
                check(len(reviewers) >= action["policy_snapshot"]["required_approvals"], "A posted reversal lacks independent approvals.")
            day, field = action["settled_at"][:10], "executed"
        elif action["status"] == "pending":
            reserved[action["source_id"]] += action["amount"]
            day, field = action["usage_date"], "reserved"
            job = action.get("expiry_job_key")
            if job:
                check(job in work and work[job]["kind"] == "action_expire" and work[job]["reference"] == action["id"] and work[job]["due"] == action["expires_at"], "A financial hold is missing its expiry job.")
            else:
                legacy_expiries += 1
        else:
            continue
        usage[f"USAGE#{day}#{action['currency']}"][field] += action["amount"]
        if action.get("agent_id"):
            check(records.get("AGENT#" + action["agent_id"]), "The operation's agent record is missing.")
            agent_usage[f"AGENTUSAGE#{action['agent_id']}#{day}#{action['currency']}"][field] += action["amount"]
            if field == "reserved":
                destinations[f"AGENTDEST#{action['agent_id']}#{action['destination_id']}"]["reserved"] += action["amount"]
    for account_id, account in accounts.items():
        check(account["kind"] in ("asset", "equity") and account["normal_side"] == ("debit" if account["kind"] == "asset" else "credit"), "Unsupported account type or normal side.")
        check(type(account["balance"]) is int and account["balance"] == balances[account_id], "Account balance disagrees with reconstructed journals.")
        check(integer(account["reserved"]) and account["reserved"] == reserved[account_id], "Account reservation disagrees with pending actions.")
        check(account["kind"] != "asset" or account["balance"] >= account["reserved"], "An asset account is overdrawn after reservations.")
    meter_check(records, "USAGE#", usage, ("executed", "reserved"))
    meter_check(records, "AGENTUSAGE#", agent_usage, ("executed", "reserved"))
    meter_check(records, "AGENTDEST#", destinations, ("reserved",))
    for claim in collect(records, "REVERSAL#"):
        action = actions.get(claim["action_id"])
        check(action and action.get("reversal_of") and action["reversal_of"]["sequence"] == claim["original_sequence"], "A reversal claim is orphaned.")
    runs = {r["id"]: r for r in collect(records, "RUN#")}
    for run in runs.values():
        check(records.get("AGENT#" + run["agent_id"]), "A run's agent record is missing.")
        if run.get("action_id"):
            check(run["action_id"] in actions and actions[run["action_id"]].get("agent_id") == run["agent_id"], "Run and resulting operation are inconsistent.")
        if run["status"] in ("queued", "running"):
            check(run.get("job_key") in work and work[run["job_key"]]["kind"] == "agent_run" and work[run["job_key"]]["reference"] == run["id"], "An unfinished run is missing its job.")
    comparisons = {r["id"]: r for r in collect(records, "RECON#")}
    for record in comparisons.values():
        bank = collect(records, "RECONROW#" + record["id"] + "#")
        check(len(bank) == record["uploaded_rows"] <= record["total_rows"], "Statement upload has missing or extra rows.")
        chain = ZERO
        for number, row in enumerate(bank, 1):
            check(row["index"] == number, "Statement upload sequence is inconsistent.")
            chain = digest({"previous": chain, "row": {k: row[k] for k in ("external_id", "booked_date", "amount", "reference", "description")}})
        check(chain == record["row_chain"] and sum(r["amount"] for r in bank) == record["statement_delta"], "Uploaded statement evidence disagrees with its import.")
        if record["status"] in ("queued", "reconciling"):
            check(record.get("job_key") in work and work[record["job_key"]]["kind"] == "reconciliation" and work[record["job_key"]]["reference"] == record["id"], "An unfinished comparison is missing its job.")
        if record["status"] in ("ready", "reconciled", "accepted_with_exceptions"):
            ledger = collect(records, "RECONLEDGER#" + record["id"] + "#")
            verify_comparison({"schema": "agentu.reconciliation.export.v1", "report": record, "statement": bank, "ledger": ledger})
            chain, opening, closing, movements = ZERO, 0, 0, {}
            for sequence in range(1, record["ledger_end"] + 1):
                journal = journals.get(sequence)
                check(journal is not None, "A comparison's source journal is missing.")
                chain = digest({"previous": chain, "journal_hash": digest(journal)})
                delta = sum(p["debit"] - p["credit"] for p in journal["postings"] if p["account_id"] == record["account_id"])
                closing += delta
                if sequence <= record["ledger_start"]:
                    opening += delta
                elif delta:
                    movements[sequence] = (delta, journal["id"], digest(journal), journal["reference"])
            check(chain == record["ledger_chain"] and (opening, closing) == (record["ledger_opening"], record["ledger_closing"]), "Comparison snapshot disagrees with original journals.")
            check(movements == {r["sequence"]: (r["amount"], r["journal_id"], r["journal_hash"], r["reference"]) for r in ledger}, "Comparison ledger rows disagree with original journals.")
    for job_key, job in work.items():
        collection, field, states = {"action_expire": (actions, "expiry_job_key", {"pending"}),
                                     "agent_run": (runs, "job_key", {"queued", "running"}),
                                     "reconciliation": (comparisons, "job_key", {"queued", "reconciling"})}[job["kind"]]
        record = collection.get(job["reference"])
        check(record and record["status"] in states and record.get(field) == job_key, "The work queue contains an orphaned or resolved job.")
    if meta.get("history_version") == 1:
        for name, record_prefix in HISTORIES.items():
            expected = {}
            for record in collect(records, record_prefix):
                reversed_time = "".join(str(9 - int(c)) for c in record["created_at"] if c.isdigit())
                expected[f"HISTORY#{name}#{reversed_time}#{record['id']}"] = {"record_key": record_prefix + record["id"], "created_at": record["created_at"]}
            check(expected == {k: v for k, v in records.items() if k.startswith("HISTORY#" + name + "#")}, "History index has missing, extra or inconsistent pointers.")
    else:
        check(meta.get("history_version", 0) == 0, "Unsupported history version.")
    return {"institution_id": tenant_id, "mode": meta["mode"], "audit_events": meta["event_sequence"],
            "audit_head": meta["event_head"], "journals": len(journals), "accounts": len(accounts), "actions": len(actions),
            "pending": meta["pending_count"], "runs": len(runs), "comparisons": len(comparisons), "queued_jobs": len(work),
            "legacy_manual_expiries": legacy_expiries, "history_upgrade_required": meta.get("history_version") != 1}


def verify_database(path):
    path = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("BEGIN")
        check(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "SQLite integrity check failed.")
        check([r[1] for r in connection.execute("PRAGMA table_info(documents)")] == ["pk", "sk", "version", "body"], "Unsupported platform database schema.")
        chain, count, tenant_keys, work = ZERO, 0, set(), defaultdict(dict)
        for pk, sk, version, body in connection.execute("SELECT pk,sk,version,body FROM documents ORDER BY pk,sk"):
            check(isinstance(pk, str) and isinstance(sk, str) and integer(version, 1), "Invalid document storage key or version.")
            value = json.loads(body)
            check(isinstance(value, dict), "A stored document must be a JSON object.")
            chain = digest({"previous": chain, "pk": pk, "sk": sk, "version": version, "body": value})
            count += 1
            if pk.startswith("TENANT#"):
                tenant_keys.add(pk)
            if pk == "WORK#platform":
                check(sk == value.get("key") and sk.startswith("DUE#") and value.get("kind") in ("agent_run", "action_expire", "reconciliation"), "Unsupported work item.")
                work[value["tenant_id"]][sk] = value
        check(all("TENANT#" + t in tenant_keys for t in work), "A work item refers to a missing institution.")
        reports = []
        for pk in sorted(tenant_keys):
            records = {sk: json.loads(body) for sk, body in connection.execute("SELECT sk,body FROM documents WHERE pk=? ORDER BY sk", (pk,))}
            reports.append(verify_institution(pk[7:], records, work[pk[7:]]))
    return {"verified": True, "schema": "agentu.recovery.integrity.v1", "documents": count, "logical_sha256": chain,
            "institutions": reports, "externally_authenticated": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_database(args.database)))
    except (ValueError, KeyError, TypeError, sqlite3.Error) as error:
        raise SystemExit("Database verification failed: " + str(error))
