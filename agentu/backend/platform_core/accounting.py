"""Statement reconciliation against an immutable-at-API journal cutoff.

Imports and comparisons are paged. No reconciliation operation changes balances.
Statement provenance is user supplied until a financial provider is integrated.
"""
import re
from datetime import date
from .agents import AgentService
from .errors import PlatformError
from .jobs import enqueue
from .model import amount, digest, identifier, new_id, now, text
from .service import MANAGERS, ZERO, access, audit, required

EDITORS = {"owner", "administrator", "operator"}
ROW_FIELDS = ("external_id", "booked_date", "amount", "reference", "description")


def signed(value, name):
    return amount(value, name, -100_000_000_000)


def day(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise PlatformError(name + " must be YYYY-MM-DD.")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise PlatformError(name + " is not a valid calendar date.") from exc
    return value


def row_value(value):
    if not isinstance(value, dict) or set(value) - set(ROW_FIELDS):
        raise PlatformError("Statement rows contain unsupported fields.")
    result = {"external_id": text(value.get("external_id"), "Statement transaction ID", 1, 100),
              "booked_date": day(value.get("booked_date"), "Booked date"), "amount": signed(value.get("amount"), "Statement amount"),
              "reference": text(value.get("reference", ""), "Ledger reference", 0, 100),
              "description": text(value.get("description", ""), "Description", 0, 250)}
    if result["amount"] == 0:
        raise PlatformError("Statement transactions must have a nonzero amount.")
    return result


def report_key(value):
    return "RECON#" + identifier(value, "Reconciliation")


def row_key(record_id, index, kind="statement"):
    prefix = "RECONROW" if kind == "statement" else "RECONLEDGER"
    return f"{prefix}#{record_id}#{index:020d}"


def facts(record):
    fields = ("id", "account_id", "currency", "source_sha256", "statement_reference", "period_start", "period_end",
              "opening_balance", "closing_balance", "ledger_start", "ledger_end", "total_rows", "row_chain", "ledger_chain",
              "ledger_opening", "ledger_closing", "ledger_rows", "matched_rows", "match_chain", "opening_variance", "closing_variance")
    return {key: record[key] for key in fields}


def revise(record):
    record.update(unmatched_statement=record["total_rows"] - record["matched_rows"],
                  unmatched_ledger=record["ledger_rows"] - record["matched_rows"],
                  opening_variance=record["opening_balance"] - record["ledger_opening"],
                  closing_variance=record["closing_balance"] - record["ledger_closing"], revision=new_id())
    record["report_hash"] = digest(facts(record))


class AccountingService(AgentService):
    permissions = {**AgentService.permissions, **{name: EDITORS for name in (
        "reconciliation_create", "reconciliation_append", "reconciliation_finish", "reconciliation_match", "reconciliation_unmatch", "reconciliation_retry", "reconciliation_cancel")},
        "reconciliation_approve": {"owner", "approver"}}
    collections = {**AgentService.collections, "reconciliations": "RECON#"}

    def reconciliation(self, tenant_id, actor, record_id, kind=None, after=None, limit=40):
        def operation(tx):
            pk, _, _ = access(tx, tenant_id, actor)
            record = required(tx, pk, report_key(record_id))
            if kind is None:
                return {"reconciliation": record}
            if kind not in ("statement", "ledger"):
                raise PlatformError("Choose statement or ledger rows.")
            prefix = ("RECONROW#" if kind == "statement" else "RECONLEDGER#") + record["id"] + "#"
            rows, cursor = tx.query(pk, prefix, after=after, limit=limit)
            return {"items": rows, "next_cursor": cursor, "revision": record.get("revision"), "status": record["status"]}
        return self.store.transact(operation)

    @staticmethod
    def editable(tx, pk, actor, body, statuses):
        record = required(tx, pk, report_key(body.get("reconciliation_id")))
        if record["status"] not in statuses:
            raise PlatformError("This reconciliation is not in the required state.", 409, "invalid_state")
        if actor.sub != record["created_by"] and required(tx, pk, "MEMBER#" + actor.sub)["role"] not in MANAGERS:
            raise PlatformError("Only the importer or an administrator can change this reconciliation.", 403, "forbidden")
        return record

    def _reconciliation_create(self, tx, pk, tenant, actor, member, body):
        account = required(tx, pk, "ACCOUNT#" + identifier(body.get("account_id")))
        if account["kind"] != "asset":
            raise PlatformError("Reconcile an asset account.")
        if body.get("currency") != account["currency"]:
            raise PlatformError("Statement currency must match the selected account; no FX conversion is performed.")
        start = amount(body.get("ledger_start", 0), "Opening ledger sequence", 0)
        end = amount(body.get("ledger_end", tenant["ledger_sequence"]), "Closing ledger sequence", 0)
        if not start <= end <= tenant["ledger_sequence"]:
            raise PlatformError("Choose an ordered ledger range ending at an existing journal sequence.")
        source_hash = body.get("source_sha256")
        if not isinstance(source_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", source_hash):
            raise PlatformError("Supply the source file SHA-256 digest.")
        count = amount(body.get("total_rows"), "Statement row count", 0)
        if count > 10000:
            raise PlatformError("Each statement supports up to 10,000 transactions.")
        header = {"account_id": account["id"], "account_name": account["name"], "currency": account["currency"],
                  "statement_reference": text(body.get("statement_reference"), "Statement reference", 2, 120),
                  "file_name": text(body.get("file_name", "pasted-statement.csv"), "Source filename", 1, 160), "source_sha256": source_hash,
                  "period_start": day(body.get("period_start"), "Statement start date"), "period_end": day(body.get("period_end"), "Statement end date"),
                  "opening_balance": signed(body.get("opening_balance"), "Opening statement balance"), "closing_balance": signed(body.get("closing_balance"), "Closing statement balance"),
                  "ledger_start": start, "ledger_end": end, "total_rows": count}
        if header["period_start"] > header["period_end"]:
            raise PlatformError("Statement dates must be in order.")
        key = "RECONIMPORT#" + digest({key: header[key] for key in ("account_id", "source_sha256", "statement_reference", "ledger_start", "ledger_end")})
        existing = tx.get(pk, key)
        if existing:
            if existing["fingerprint"] != digest(header):
                raise PlatformError("This import reference already describes different statement metadata.", 409, "import_conflict")
            return {"reconciliation": required(tx, pk, "RECON#" + existing["id"]), "existing": True}
        record = {**header, "id": new_id(), "status": "uploading", "created_by": actor.sub, "created_at": now(),
                  "provenance": "user_supplied_statement", "as_of_audit_head": tenant["event_head"], "contributors": [actor.sub],
                  "uploaded_rows": 0, "statement_delta": 0, "row_chain": ZERO, "ledger_chain": ZERO, "match_chain": ZERO,
                  "ledger_opening": 0, "ledger_closing": 0, "ledger_rows": 0, "matched_rows": 0, "processed_rows": 0,
                  "phase": "ledger", "ledger_cursor": None, "ledger_scanned": 0}
        tx.put(pk, "RECON#" + record["id"], record, insert_only=True)
        tx.put(pk, key, {"id": record["id"], "fingerprint": digest(header)}, insert_only=True)
        audit(tx, pk, actor, "statement_import_started", record)
        return {"reconciliation": record}

    def _reconciliation_append(self, tx, pk, tenant, actor, member, body):
        record = self.editable(tx, pk, actor, body, {"uploading"})
        rows = body.get("rows")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
            raise PlatformError("Upload batches of one to twenty statement rows.")
        if type(body.get("offset")) is not int or body["offset"] != record["uploaded_rows"]:
            raise PlatformError("Resume from the current uploaded row count.", 409, "upload_offset")
        if record["uploaded_rows"] + len(rows) > record["total_rows"]:
            raise PlatformError("The batch exceeds the declared row count.")
        for raw in rows:
            row = row_value(raw)
            if not record["period_start"] <= row["booked_date"] <= record["period_end"]:
                raise PlatformError("A booked date is outside the statement period.")
            unique = "RECONBANKREF#" + record["id"] + "#" + digest(row["external_id"])
            if tx.get(pk, unique):
                raise PlatformError("A statement contains duplicate external transaction IDs.", 409, "duplicate_statement_row")
            record["uploaded_rows"] += 1
            record["statement_delta"] += row["amount"]
            record["row_chain"] = digest({"previous": record["row_chain"], "row": row})
            row.update(index=record["uploaded_rows"], match_status="unprocessed")
            tx.put(pk, row_key(record["id"], row["index"]), row, insert_only=True)
            tx.put(pk, unique, {"index": row["index"]}, insert_only=True)
        if actor.sub not in record["contributors"]:
            record["contributors"].append(actor.sub)
        tx.put(pk, "RECON#" + record["id"], record)
        return {"reconciliation": record}

    def _reconciliation_finish(self, tx, pk, tenant, actor, member, body):
        record = self.editable(tx, pk, actor, body, {"uploading"})
        if record["uploaded_rows"] != record["total_rows"] or record["opening_balance"] + record["statement_delta"] != record["closing_balance"]:
            raise PlatformError("Complete all rows and reconcile the statement's own opening, movements and closing balance before comparison.")
        record.update(status="queued", job_key=enqueue(tx, "reconciliation", tenant["id"], record["id"]))
        tx.put(pk, "RECON#" + record["id"], record)
        audit(tx, pk, actor, "statement_import_completed", {"id": record["id"], "rows": record["uploaded_rows"], "row_chain": record["row_chain"], "source_sha256": record["source_sha256"]})
        return {"reconciliation": record}

    @staticmethod
    def link_match(tx, pk, record, bank, ledger, actor, reason):
        bank.update(match_status="matched", journal_sequence=ledger["sequence"], match_method=actor, match_reason=reason)
        ledger.update(match_status="matched", statement_index=bank["index"], match_method=actor, match_reason=reason)
        record["matched_rows"] += 1
        record["match_chain"] = digest({"previous": record["match_chain"], "row": bank["index"], "journal": ledger["sequence"], "by": actor, "reason": reason})
        tx.put(pk, row_key(record["id"], bank["index"]), bank)
        tx.put(pk, row_key(record["id"], ledger["sequence"], "ledger"), ledger)

    def reconciliation_step(self, tx, pk, job, actor):
        record = required(tx, pk, "RECON#" + job["reference"])
        if record["status"] not in ("queued", "reconciling"):
            return True
        record["status"] = "reconciling"
        if record["phase"] == "ledger":
            journals, cursor = tx.query(pk, "JOURNAL#", after=record["ledger_cursor"], limit=20)
            finished = not cursor
            for journal in journals:
                if journal["sequence"] > record["ledger_end"]:
                    finished = True
                    break
                if journal["sequence"] != record["ledger_scanned"] + 1:
                    raise PlatformError("The source journal has a missing or out-of-order sequence. Restore its integrity before comparison.", 409, "ledger_sequence_gap")
                record["ledger_scanned"] = journal["sequence"]
                record["ledger_chain"] = digest({"previous": record["ledger_chain"], "journal_hash": digest(journal)})
                delta = sum(p["debit"] - p["credit"] for p in journal["postings"] if p["account_id"] == record["account_id"])
                record["ledger_closing"] += delta
                if journal["sequence"] <= record["ledger_start"]:
                    record["ledger_opening"] += delta
                elif delta:
                    line = {"sequence": journal["sequence"], "journal_id": journal["id"], "journal_hash": digest(journal),
                            "reference": journal["reference"], "amount": delta, "timestamp": journal["timestamp"], "description": journal["reason"], "match_status": "unmatched"}
                    tx.put(pk, row_key(record["id"], journal["sequence"], "ledger"), line, insert_only=True)
                    record["ledger_rows"] += 1
                    for reference in set((journal["reference"], journal["id"])):
                        key = "RECONREF#" + record["id"] + "#" + digest(reference)
                        index = tx.get(pk, key) or {"count": 0, "sequence": journal["sequence"]}
                        index["count"] += 1
                        tx.put(pk, key, index)
                if journal["sequence"] == record["ledger_end"]:
                    finished = True
            record["ledger_cursor"] = cursor
            if finished:
                if record["ledger_scanned"] != record["ledger_end"]:
                    raise PlatformError("The source journal does not reach the selected closing sequence.", 409, "ledger_sequence_gap")
                record["phase"] = "statement"
        else:
            for number in range(record["processed_rows"] + 1, min(record["processed_rows"] + 20, record["total_rows"]) + 1):
                bank = required(tx, pk, row_key(record["id"], number))
                index = tx.get(pk, "RECONREF#" + record["id"] + "#" + digest(bank["reference"])) if bank["reference"] else None
                ledger = required(tx, pk, row_key(record["id"], index["sequence"], "ledger")) if index and index["count"] == 1 else None
                if ledger and ledger["amount"] == bank["amount"] and ledger["match_status"] == "unmatched":
                    self.link_match(tx, pk, record, bank, ledger, "automatic", "Exact unique reference and amount")
                else:
                    bank.update(match_status="unmatched", exception="missing_reference" if not bank["reference"] else "reference_not_found" if not index else "ambiguous_reference" if index["count"] != 1 else "amount_mismatch" if ledger["amount"] != bank["amount"] else "ledger_already_matched")
                    tx.put(pk, row_key(record["id"], number), bank)
                record["processed_rows"] = number
            if record["processed_rows"] == record["total_rows"]:
                record.update(status="ready", completed_at=now())
                revise(record)
                audit(tx, pk, actor, "reconciliation_comparison_completed", {"id": record["id"], "report_hash": record["report_hash"], "facts": facts(record)})
        tx.put(pk, "RECON#" + record["id"], record)
        return record["status"] == "ready"

    def reconciliation_failed(self, tx, pk, job, error, actor):
        record = required(tx, pk, "RECON#" + job["reference"])
        record.update(status="failed", error={"code": getattr(error, "code", "comparison_failed"), "message": str(error)[:500]})
        tx.put(pk, "RECON#" + record["id"], record)
        audit(tx, pk, actor, "reconciliation_failed", {"id": record["id"], "error": record["error"]})

    def _reconciliation_match(self, tx, pk, tenant, actor, member, body):
        record = self.editable(tx, pk, actor, body, {"ready"})
        if body.get("revision") != record["revision"]:
            raise PlatformError("The comparison changed. Reload before matching.", 409, "stale_comparison")
        bank = required(tx, pk, row_key(record["id"], amount(body.get("statement_index"), "Statement row")))
        ledger = required(tx, pk, row_key(record["id"], amount(body.get("journal_sequence"), "Journal sequence"), "ledger"))
        if bank["match_status"] != "unmatched" or ledger["match_status"] != "unmatched" or bank["amount"] != ledger["amount"]:
            raise PlatformError("Match two unmatched rows with the same signed amount.", 409, "invalid_match")
        reason = text(body.get("reason"), "Matching evidence", 10, 500)
        self.link_match(tx, pk, record, bank, ledger, actor.sub, reason)
        if actor.sub not in record["contributors"]:
            record["contributors"].append(actor.sub)
        revise(record)
        tx.put(pk, "RECON#" + record["id"], record)
        audit(tx, pk, actor, "reconciliation_manual_match", {"id": record["id"], "statement_index": bank["index"], "journal_sequence": ledger["sequence"], "reason": reason, "report_hash": record["report_hash"]})
        return {"reconciliation": record}

    def _reconciliation_approve(self, tx, pk, tenant, actor, member, body):
        record = required(tx, pk, report_key(body.get("reconciliation_id")))
        if record["status"] != "ready" or body.get("revision") != record["revision"]:
            raise PlatformError("Reload the completed comparison before review.", 409, "stale_comparison")
        if actor.sub in record["contributors"]:
            raise PlatformError("An independent person must review this reconciliation.", 403, "independent_review_required")
        exceptions = any(record[key] for key in ("unmatched_statement", "unmatched_ledger", "opening_variance", "closing_variance"))
        if exceptions and body.get("accept_exceptions") is not True:
            raise PlatformError("Unmatched rows or balance differences remain. Resolve them or explicitly accept the exceptions.", 409, "reconciliation_exceptions")
        record.update(status="accepted_with_exceptions" if exceptions else "reconciled", reviewed_by=actor.sub,
                      reviewed_at=now(), review_reason=text(body.get("reason"), "Review evidence", 10, 1000))
        tx.put(pk, "RECON#" + record["id"], record)
        audit(tx, pk, actor, "reconciliation_reviewed", {"id": record["id"], "status": record["status"], "report_hash": record["report_hash"], "reason": record["review_reason"], "facts": facts(record)})
        return {"reconciliation": record}

    def _reconciliation_unmatch(self, tx, pk, tenant, actor, member, body):
        record = self.editable(tx, pk, actor, body, {"ready"})
        if body.get("revision") != record["revision"]:
            raise PlatformError("The comparison changed. Reload before removing a match.", 409, "stale_comparison")
        bank = required(tx, pk, row_key(record["id"], amount(body.get("statement_index"), "Statement row")))
        if bank["match_status"] != "matched":
            raise PlatformError("This row is not matched.", 409, "invalid_match")
        ledger = required(tx, pk, row_key(record["id"], bank["journal_sequence"], "ledger"))
        if ledger.get("statement_index") != bank["index"] or ledger["match_status"] != "matched":
            raise PlatformError("The match links are inconsistent.", 409, "invalid_match")
        reason = text(body.get("reason"), "Unmatching evidence", 10, 500)
        for row, fields in ((bank, ("journal_sequence", "match_method", "match_reason")), (ledger, ("statement_index", "match_method", "match_reason"))):
            row["match_status"] = "unmatched"
            for field in fields:
                row.pop(field, None)
        bank["exception"] = "match_removed"
        record["matched_rows"] -= 1
        record["match_chain"] = digest({"previous": record["match_chain"], "removed_row": bank["index"], "journal": ledger["sequence"], "by": actor.sub, "reason": reason})
        if actor.sub not in record["contributors"]:
            record["contributors"].append(actor.sub)
        revise(record)
        tx.put(pk, row_key(record["id"], bank["index"]), bank)
        tx.put(pk, row_key(record["id"], ledger["sequence"], "ledger"), ledger)
        tx.put(pk, "RECON#" + record["id"], record)
        audit(tx, pk, actor, "reconciliation_match_removed", {"id": record["id"], "statement_index": bank["index"], "journal_sequence": ledger["sequence"], "reason": reason, "report_hash": record["report_hash"]})
        return {"reconciliation": record}

    def _reconciliation_cancel(self, tx, pk, tenant, actor, member, body):
        record = self.editable(tx, pk, actor, body, {"uploading", "queued", "reconciling", "ready", "failed"})
        if record.get("job_key"):
            tx.delete_work(record["job_key"])
        record.update(status="cancelled", cancelled_at=now(), cancellation_reason=text(body.get("reason"), "Cancellation reason", 5, 500))
        tx.put(pk, "RECON#" + record["id"], record)
        audit(tx, pk, actor, "reconciliation_cancelled", {"id": record["id"], "reason": record["cancellation_reason"]})
        return {"reconciliation": record}

    def _reconciliation_retry(self, tx, pk, tenant, actor, member, body):
        record = self.editable(tx, pk, actor, body, {"failed"})
        record.update(status="queued", job_key=enqueue(tx, "reconciliation", tenant["id"], record["id"]))
        record.pop("error", None)
        tx.put(pk, "RECON#" + record["id"], record)
        audit(tx, pk, actor, "reconciliation_retry_requested", {"id": record["id"]})
        return {"reconciliation": record}
