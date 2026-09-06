import concurrent.futures
import copy
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from platform_core.accounting import AccountingService, facts
from platform_core.errors import PlatformError
from platform_core.jobs import QUEUE
from platform_core.model import Actor, digest
from platform_core.runner import Runner
from platform_core.store import DocumentStore, SQLiteBackend
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_reconciliation_export import verify

key = lambda: str(uuid.uuid4())


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DocumentStore(SQLiteBackend(Path(self.temp.name) / "accounting.sqlite3"))
        self.service = AccountingService(self.store)
        self.owner, self.reviewer = Actor(key(), "owner@example.test", True), Actor(key(), "reviewer@example.test", True)
        self.tenant = self.service.create_institution(self.owner, {"name": "Accounting verification"}, key())["institution"]["id"]
        self.pk = "TENANT#" + self.tenant
        invite = self.cmd("invite_create", {"email": self.reviewer.email, "role": "owner"})
        self.service.accept_invitation(self.reviewer, invite["invite_token"])
        self.source = self.cmd("account_create", {"name": "Operating"})["account"]["id"]
        self.target = self.cmd("account_create", {"name": "Reserve"})["account"]["id"]
        self.cmd("sandbox_fund", {"account_id": self.source, "amount": 10_000_000, "reason": "Opening verification capital"})
        self.runner = Runner(self.service)

    def tearDown(self):
        self.temp.cleanup()

    def cmd(self, name, body, actor=None, request_id=None):
        return self.service.command(self.tenant, actor or self.owner, name, body, request_id or key())

    def get(self, sk):
        return self.store.transact(lambda tx: tx.get(self.pk, sk))

    def approve(self, action):
        return self.cmd("action_approve", {"action_id": action["id"], "reason": "Independent accounting review"}, self.reviewer)["action"]

    def transfer(self):
        return self.approve(self.cmd("action_propose", {"source_id": self.source, "destination_id": self.target, "amount": 500_000, "purpose": "Treasury allocation for comparison"})["action"])

    def row(self, external_id="bank-1", value=-500_000, reference=""):
        return {"external_id": external_id, "booked_date": "2026-09-06", "amount": value, "reference": reference, "description": "Statement verification"}

    def start(self, rows, **overrides):
        body = {"account_id": self.source, "currency": "GBP", "source_sha256": digest(rows), "statement_reference": "Statement " + key(),
                "period_start": "2026-09-01", "period_end": "2026-09-30", "total_rows": len(rows),
                "opening_balance": 10_000_000, "closing_balance": 10_000_000 + sum(r["amount"] for r in rows),
                "ledger_start": 1, "ledger_end": self.get("META")["ledger_sequence"], **overrides}
        return self.cmd("reconciliation_create", body)["reconciliation"]

    def upload(self, record, rows):
        for offset in range(0, len(rows), 20):
            record = self.cmd("reconciliation_append", {"reconciliation_id": record["id"], "offset": offset, "rows": rows[offset:offset + 20]})["reconciliation"]
        return self.cmd("reconciliation_finish", {"reconciliation_id": record["id"]})["reconciliation"]

    def compare(self, record):
        for _ in range(20):
            self.runner.process(record["job_key"])
            current = self.get("RECON#" + record["id"])
            if current["status"] not in ("queued", "reconciling"):
                return current
        self.fail("Comparison did not finish within its expected bounded pages")

    def test_reversal_appends_inverse_entry_and_preserves_original(self):
        action = self.transfer()
        original = self.get(f"JOURNAL#{action['journal_sequence']:020d}")
        reversal = self.cmd("reversal_propose", {"journal_sequence": original["sequence"], "reason": "Correct an erroneous treasury allocation"})["action"]
        with self.assertRaises(PlatformError):
            self.cmd("action_approve", {"action_id": reversal["id"], "reason": "Attempted correction self-approval"})
        settled = self.approve(reversal)
        corrected = self.get(f"JOURNAL#{settled['journal_sequence']:020d}")
        self.assertEqual(original, self.get(f"JOURNAL#{original['sequence']:020d}"))
        self.assertEqual(original["id"], corrected["reversal_of"]["id"])
        before = {p["account_id"]: (p["debit"], p["credit"]) for p in original["postings"]}
        self.assertEqual(before, {p["account_id"]: (p["credit"], p["debit"]) for p in corrected["postings"]})
        self.assertEqual(10_000_000, self.get("ACCOUNT#" + self.source)["balance"])
        self.assertEqual(0, self.get("ACCOUNT#" + self.target)["balance"])
        with self.assertRaises(PlatformError) as error:
            self.cmd("reversal_propose", {"journal_sequence": original["sequence"], "reason": "Another request key cannot reverse twice"})
        self.assertEqual("reversal_exists", error.exception.code)

    def test_funding_reversal_requires_review_even_under_auto_limit(self):
        config = self.service.overview(self.tenant, self.owner)["policy"]["config"]
        draft = self.cmd("policy_create", {"name": "Automatic transfers", "config": {**config, "auto_limit": 10_000_000}})["policy"]
        self.cmd("policy_publish", {"policy_id": draft["id"], "reason": "Independent policy review"}, self.reviewer)
        reversal = self.cmd("reversal_propose", {"journal_sequence": 1, "reason": "Correct duplicated sandbox opening funding"})["action"]
        self.assertEqual("pending", reversal["status"])
        self.approve(reversal)
        self.assertEqual(0, self.get("ACCOUNT#" + self.source)["balance"])
        self.assertEqual(0, self.get("ACCOUNT#sandbox-equity-GBP")["balance"])

    def test_concurrent_reversals_share_one_claim_and_expiry_allows_retry(self):
        original = self.transfer()
        body = {"journal_sequence": original["journal_sequence"], "reason": "Concurrent correction request"}
        def attempt():
            try:
                return self.cmd("reversal_propose", body)["action"]
            except PlatformError as error:
                return error.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: attempt(), range(2)))
        self.assertEqual(1, results.count("reversal_exists"))
        pending = next(result for result in results if isinstance(result, dict))
        with patch("time.time", return_value=pending["expires_at"] + 1):
            self.runner.process(pending["expiry_job_key"])
        self.assertEqual(0, self.get("ACCOUNT#" + self.target)["reserved"])
        self.assertEqual("pending", self.cmd("reversal_propose", body)["action"]["status"])

    def test_reversal_rechecks_source_journal_and_liquidity(self):
        original = self.transfer()
        pending = self.cmd("reversal_propose", {"journal_sequence": original["journal_sequence"], "reason": "Review against immutable source"})["action"]
        sk = f"JOURNAL#{original['journal_sequence']:020d}"
        changed = self.get(sk); changed["reason"] = "Privileged tamper simulation"
        self.store.transact(lambda tx: tx.put(self.pk, sk, changed))
        self.assertEqual("blocked", self.approve(pending)["status"])
        self.assertEqual(0, self.get("ACCOUNT#" + self.target)["reserved"])

    def test_reconciliation_uses_fixed_cutoff_and_independent_review(self):
        action = self.transfer()
        rows = [self.row(reference=action["id"])]
        record = self.upload(self.start(rows), rows)
        reversal = self.cmd("reversal_propose", {"journal_sequence": 2, "reason": "Later correction must not alter an earlier snapshot"})["action"]
        self.approve(reversal)
        result = self.compare(record)
        self.assertEqual((1, 0, 0, 9_500_000), (result["matched_rows"], result["unmatched_statement"], result["closing_variance"], result["ledger_closing"]))
        self.assertEqual(digest(facts(result)), result["report_hash"])
        body = {"reconciliation_id": result["id"], "revision": result["revision"], "reason": "Independent statement and ledger comparison"}
        with self.assertRaises(PlatformError):
            self.cmd("reconciliation_approve", body)
        reviewed = self.cmd("reconciliation_approve", body, self.reviewer)["reconciliation"]
        self.assertEqual("reconciled", reviewed["status"])
        self.assertEqual(10_000_000, self.get("ACCOUNT#" + self.source)["balance"])

    def test_unmatched_rows_require_equal_amount_matching_and_stale_review_is_rejected(self):
        self.transfer()
        rows = [self.row(reference="unmapped-bank-reference")]
        record = self.compare(self.upload(self.start(rows), rows))
        old_revision = record["revision"]
        record = self.cmd("reconciliation_match", {"reconciliation_id": record["id"], "revision": record["revision"], "statement_index": 1, "journal_sequence": 2, "reason": "Matched using the reviewed statement reference"})["reconciliation"]
        with self.assertRaises(PlatformError) as error:
            self.cmd("reconciliation_approve", {"reconciliation_id": record["id"], "revision": old_revision, "reason": "Must inspect the current comparison revision"}, self.reviewer)
        self.assertEqual("stale_comparison", error.exception.code)
        record = self.cmd("reconciliation_unmatch", {"reconciliation_id": record["id"], "revision": record["revision"], "statement_index": 1, "reason": "Reopen this match for further investigation"})["reconciliation"]
        self.assertEqual((0, 1, 1), (record["matched_rows"], record["unmatched_statement"], record["unmatched_ledger"]))

    def test_difference_acceptance_remains_explicit_and_does_not_post_money(self):
        self.transfer()
        rows = [self.row(value=-510_000, reference="bank-fee-difference")]
        record = self.compare(self.upload(self.start(rows), rows))
        body = {"reconciliation_id": record["id"], "revision": record["revision"], "reason": "Unresolved fee difference recorded for provider investigation"}
        with self.assertRaises(PlatformError) as error:
            self.cmd("reconciliation_approve", body, self.reviewer)
        self.assertEqual("reconciliation_exceptions", error.exception.code)
        reviewed = self.cmd("reconciliation_approve", {**body, "accept_exceptions": True}, self.reviewer)["reconciliation"]
        self.assertEqual(("accepted_with_exceptions", -10_000, 1), (reviewed["status"], reviewed["closing_variance"], reviewed["unmatched_statement"]))
        self.assertEqual(2, self.get("META")["ledger_sequence"])

    def test_batched_import_duplicate_offset_currency_and_balance_validation(self):
        rows = [self.row()]
        with self.assertRaises(PlatformError):
            self.start(rows, currency="EUR")
        record = self.start(rows, total_rows=2)
        request_id = key(); body = {"reconciliation_id": record["id"], "offset": 0, "rows": rows}
        first = self.cmd("reconciliation_append", body, request_id=request_id)
        self.assertEqual(first, self.cmd("reconciliation_append", body, request_id=request_id))
        with self.assertRaises(PlatformError):
            self.cmd("reconciliation_append", {**body, "offset": 1})
        self.assertEqual(1, self.get("RECON#" + record["id"])["uploaded_rows"])
        with self.assertRaises(PlatformError):
            self.cmd("reconciliation_finish", {"reconciliation_id": record["id"]})
        invalid = self.start(rows, closing_balance=9_500_001)
        self.cmd("reconciliation_append", {"reconciliation_id": invalid["id"], "offset": 0, "rows": rows})
        with self.assertRaises(PlatformError):
            self.cmd("reconciliation_finish", {"reconciliation_id": invalid["id"]})
        self.assertNotIn("job_key", self.get("RECON#" + invalid["id"]))

    def test_large_comparison_stays_within_transaction_pages_and_replayed_lease_is_ignored(self):
        rows = []
        for n in range(45):
            result = self.cmd("sandbox_fund", {"account_id": self.source, "amount": 100, "reason": "Paged reconciliation fixture " + str(n)})
            journal = self.get(f"JOURNAL#{n + 2:020d}")
            rows.append(self.row("bank-" + str(n), 100, journal["id"]))
        record = self.upload(self.start(rows), rows)
        claimed = self.runner.claim(record["job_key"])
        self.runner.finish(claimed)
        # A subsequent claim owns the next page. The old lease cannot repeat it.
        second = self.runner.claim(record["job_key"])
        self.assertEqual("ignored", self.runner.finish(claimed)["status"])
        self.runner.finish(second)
        result = self.compare(record)
        self.assertEqual((45, 45, 0), (result["ledger_rows"], result["matched_rows"], result["closing_variance"]))

    def test_report_access_is_tenant_scoped_and_cancellation_ignores_late_worker(self):
        rows = []
        record = self.upload(self.start(rows, ledger_start=1), rows)
        other = Actor(key(), "other@example.test", True)
        with self.assertRaises(PlatformError):
            self.service.reconciliation(self.tenant, other, record["id"], "statement")
        claimed = self.runner.claim(record["job_key"])
        self.cmd("reconciliation_cancel", {"reconciliation_id": record["id"], "reason": "Cancel an obsolete comparison request"})
        self.assertEqual("ignored", self.runner.finish(claimed)["status"])
        self.assertEqual("cancelled", self.get("RECON#" + record["id"])["status"])

    def test_ambiguous_reference_is_never_automatically_guessed(self):
        self.cmd("sandbox_fund", {"account_id": self.source, "amount": 100, "reason": "Second reference occurrence"})
        self.cmd("sandbox_fund", {"account_id": self.source, "amount": 100, "reason": "Third reference occurrence"})
        rows = [self.row("duplicate-reference", 100, "sandbox-funding")]
        record = self.compare(self.upload(self.start(rows), rows))
        self.assertEqual(0, record["matched_rows"])
        bank = self.service.reconciliation(self.tenant, self.owner, record["id"], "statement")["items"]
        self.assertEqual("ambiguous_reference", bank[0]["exception"])

    def test_export_verifier_rejects_tampering_truncation_and_false_clean_status(self):
        action = self.transfer(); rows = [self.row(reference=action["id"])]
        r = self.compare(self.upload(self.start(rows), rows))
        document = {"schema": "agentu.reconciliation.export.v1", "institution_id": self.tenant, "mode": "sandbox", "report": r,
                    **{kind: self.service.reconciliation(self.tenant, self.owner, r["id"], kind)["items"] for kind in ("statement", "ledger")}}
        self.assertEqual(1, verify(document)["matched"])
        for kind in ("statement", "ledger"):
            changed = copy.deepcopy(document); changed[kind] = []
            with self.assertRaises(ValueError): verify(changed)
        changed = copy.deepcopy(document); changed["statement"][0]["amount"] -= 1
        with self.assertRaises(ValueError): verify(changed)
        changed = copy.deepcopy(document); changed["ledger"][0]["statement_index"] = 99
        with self.assertRaises(ValueError): verify(changed)
        changed = copy.deepcopy(document); changed["report"].update(status="reconciled", reviewed_by=self.owner.sub, review_reason="Invalid self review")
        with self.assertRaises(ValueError): verify(changed)

    def test_missing_source_journal_fails_without_partial_counts_and_can_retry_after_repair(self):
        action = self.transfer(); rows = [self.row(reference=action["id"])]
        record = self.upload(self.start(rows), rows)
        query = self.store.backend.query
        def missing(pk, prefix, **kwargs):
            items, cursor = query(pk, prefix, **kwargs)
            return ([item for item in items if item[2].get("sequence") != 1] if prefix == "JOURNAL#" else items), cursor
        with patch.object(self.store.backend, "query", side_effect=missing):
            result = self.runner.process(record["job_key"])
        self.assertEqual("failed", result["status"])
        failed = self.get("RECON#" + record["id"])
        self.assertEqual((0, 0, "ledger_sequence_gap"), (failed["ledger_scanned"], failed["ledger_rows"], failed["error"]["code"]))
        retry = self.cmd("reconciliation_retry", {"reconciliation_id": record["id"]})["reconciliation"]
        self.assertEqual(1, self.compare(retry)["matched_rows"])
