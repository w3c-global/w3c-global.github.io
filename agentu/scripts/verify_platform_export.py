"""Verify an exported audit chain or balanced journal without a running service.

This detects missing, reordered and altered records relative to the included head.
It is not a signature and cannot establish that a privileged operator has not
rewritten the entire dataset. Compare the head with a separately retained anchor.
"""
import argparse
import hashlib
import json
from pathlib import Path


def verify(document):
    if document.get("schema") != "agentu.platform.export.v1" or not isinstance(document.get("items"), list):
        raise ValueError("Unsupported export schema.")
    if document.get("collection") not in ("audit", "journal"):
        raise ValueError("Choose an audit or journal export.")
    items = document["items"]
    previous = "0" * 64
    journal_ids = set()
    for sequence, item in enumerate(items, 1):
        if item.get("sequence") != sequence:
            raise ValueError(f"Missing, duplicate or reordered record at sequence {sequence}.")
        if document["collection"] == "audit":
            value = dict(item)
            recorded = value.pop("hash", None)
            actual = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
            if recorded != actual or item.get("previous_hash") != previous:
                raise ValueError(f"Audit chain mismatch at sequence {sequence}.")
            previous = recorded
        elif document["collection"] == "journal":
            if item.get("id") in journal_ids:
                raise ValueError("Duplicate journal ID.")
            journal_ids.add(item["id"])
            totals = {}
            postings = item.get("postings", [])
            if len(postings) < 2:
                raise ValueError("Journal needs at least two postings.")
            accounts = set()
            for posting in postings:
                debit, credit = posting.get("debit"), posting.get("credit")
                if type(debit) is not int or type(credit) is not int or min(debit, credit) < 0 or (debit == 0) == (credit == 0):
                    raise ValueError("Invalid debit or credit.")
                if posting["account_id"] in accounts:
                    raise ValueError("Duplicate account in journal entry.")
                accounts.add(posting["account_id"])
                code = posting["currency"]
                totals[code] = totals.get(code, 0) + debit - credit
            if any(totals.values()):
                raise ValueError(f"Unbalanced journal at sequence {sequence}.")
        else:
            raise ValueError("Choose an audit or journal export.")
    if document["collection"] == "audit" and (document.get("as_of_sequence") != len(items) or document.get("as_of_head") != previous):
        raise ValueError("Export count or head does not match its audit records.")
    if document["collection"] == "journal" and document.get("journal_sequence") != len(items):
        raise ValueError("Export journal count does not match its records.")
    return {"verified": True, "collection": document["collection"], "records": len(items), "institution_id": document["institution_id"], "mode": document["mode"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(json.loads(args.export.read_text(encoding="utf-8")))))
    except (ValueError, KeyError, TypeError) as error:
        raise SystemExit("Verification failed: " + str(error))
