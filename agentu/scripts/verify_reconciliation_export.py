"""Check a comparison export without the application or a database connection.

Verifies arithmetic, row completeness, one-to-one matching and the included hashes.
It cannot authenticate a supplied bank statement or a privileged ledger snapshot.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

ZERO = "0" * 64
FACT_FIELDS = ("id", "account_id", "currency", "source_sha256", "statement_reference", "period_start", "period_end",
               "opening_balance", "closing_balance", "ledger_start", "ledger_end", "total_rows", "row_chain", "ledger_chain",
               "ledger_opening", "ledger_closing", "ledger_rows", "matched_rows", "match_chain", "opening_variance", "closing_variance")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value):
    return type(value) is int


def verify(document):
    check(document.get("schema") == "agentu.reconciliation.export.v1", "Unsupported comparison schema.")
    r, bank, ledger = document["report"], document["statement"], document["ledger"]
    check(r["status"] in ("ready", "reconciled", "accepted_with_exceptions"), "Export requires a completed comparison.")
    check(r["currency"] in ("GBP", "EUR", "USD"), "Unsupported currency.")
    check(isinstance(bank, list) and isinstance(ledger, list), "Export rows must be arrays.")
    check(all(integer(r[field]) for field in ("opening_balance", "closing_balance", "ledger_opening", "ledger_closing", "ledger_start", "ledger_end", "total_rows", "ledger_rows", "matched_rows")), "Balances, cutoffs and counts must be integers.")
    check(0 <= r["ledger_start"] <= r["ledger_end"], "Invalid journal cutoffs.")
    check(len(bank) == r["total_rows"] and len(ledger) == r["ledger_rows"], "Export contains missing or extra rows.")
    chain, ids = ZERO, set()
    for number, row in enumerate(bank, 1):
        check(row["index"] == number and row["external_id"] not in ids, "Statement sequence or external ID is duplicated/missing.")
        ids.add(row["external_id"])
        check(integer(row["amount"]) and row["amount"] != 0, "Statement amounts must be signed, nonzero integers.")
        check(r["period_start"] <= row["booked_date"] <= r["period_end"], "Booked date is outside the statement period.")
        value = {field: row[field] for field in ("external_id", "booked_date", "amount", "reference", "description")}
        chain = digest({"previous": chain, "row": value})
    check(chain == r["row_chain"], "Statement row chain does not match.")
    by_sequence, previous = {}, r["ledger_start"]
    for row in ledger:
        sequence = row["sequence"]
        check(integer(sequence) and previous < sequence <= r["ledger_end"], "Ledger sequence is duplicated, reordered or outside its cutoffs.")
        previous = sequence
        check(integer(row["amount"]) and row["amount"] != 0, "Ledger amounts must be signed, nonzero integers.")
        by_sequence[sequence] = row
    matched, claimed = 0, set()
    for row in bank:
        check(row["match_status"] in ("matched", "unmatched"), "Statement row has an unfinished match state.")
        if row["match_status"] == "matched":
            sequence = row["journal_sequence"]
            other = by_sequence.get(sequence)
            check(other is not None and sequence not in claimed, "A ledger match is missing or reused.")
            check(other["match_status"] == "matched" and other["statement_index"] == row["index"] and other["amount"] == row["amount"], "Match links or signed amounts disagree.")
            matched += 1; claimed.add(sequence)
        else:
            check("journal_sequence" not in row, "Unmatched statement row retains a match link.")
    for row in ledger:
        check(row["match_status"] in ("matched", "unmatched"), "Invalid ledger match state.")
        check((row["sequence"] in claimed) == (row["match_status"] == "matched"), "Ledger match has no corresponding statement row.")
        if row["match_status"] == "unmatched":
            check("statement_index" not in row, "Unmatched ledger row retains a match link.")
    check(r["opening_balance"] + sum(row["amount"] for row in bank) == r["closing_balance"], "Statement arithmetic does not balance.")
    check(r["ledger_opening"] + sum(row["amount"] for row in ledger) == r["ledger_closing"], "Ledger snapshot arithmetic does not balance.")
    check(matched == r["matched_rows"] and len(bank) - matched == r["unmatched_statement"] and len(ledger) - matched == r["unmatched_ledger"], "Match counts disagree with the rows.")
    check(r["opening_variance"] == r["opening_balance"] - r["ledger_opening"] and r["closing_variance"] == r["closing_balance"] - r["ledger_closing"], "Reported balance differences are incorrect.")
    check(all(isinstance(r[field], str) and re.fullmatch(r"[a-f0-9]{64}", r[field]) for field in ("source_sha256", "ledger_chain", "match_chain")), "Malformed evidence hash.")
    check(digest({field: r[field] for field in FACT_FIELDS}) == r["report_hash"], "Comparison facts do not match their recorded hash.")
    exceptions = any(r[field] for field in ("unmatched_statement", "unmatched_ledger", "opening_variance", "closing_variance"))
    if r["status"] != "ready":
        check(r.get("reviewed_by") and r["reviewed_by"] not in r["contributors"] and len(r.get("review_reason", "")) >= 10, "Final comparison lacks an independent review.")
        check((r["status"] == "accepted_with_exceptions") == bool(exceptions), "Final status misrepresents remaining exceptions.")
    return {"verified": True, "statement_rows": len(bank), "ledger_rows": len(ledger), "matched": matched,
            "status": r["status"], "closing_variance": r["closing_variance"], "source_authenticated": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(json.loads(args.export.read_text(encoding="utf-8")))))
    except (ValueError, KeyError, TypeError) as error:
        raise SystemExit("Verification failed: " + str(error))
