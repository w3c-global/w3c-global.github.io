"""Backup correctness, corruption detection and quarantined HTTP inspection."""
from contextlib import closing
from http.cookiejar import CookieJar
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import local
from local_auth import LocalAuth, COOKIE
from platform_core.accounting import AccountingService
from platform_core.model import Actor
from platform_core.runner import Runner
from platform_core.store import DocumentStore, SQLiteBackend
from recovery import backup, restore, verify_backup, DATABASE
from verify_database import verify_database

key = lambda: str(uuid.uuid4())
PASSWORD = "Restore-Verification-Only-2026!"


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / DATABASE
        self.store = DocumentStore(SQLiteBackend(self.database))
        self.service = AccountingService(self.store)
        self.auth = LocalAuth(self.store)
        people = []
        self.cookies = []
        for name in ("owner", "reviewer"):
            value, cookie = self.auth.authenticate({"email": name + "@example.test", "password": PASSWORD}, True)
            people.append(Actor(value["user"]["sub"], value["user"]["email"], True))
            self.cookies.append(cookie.split(";", 1)[0])
        self.owner, self.reviewer = people
        self.tenant = self.service.create_institution(self.owner, {"name": "Recovery verification"}, key())["institution"]["id"]
        self.pk = "TENANT#" + self.tenant
        invitation = self.cmd("invite_create", {"email": self.reviewer.email, "role": "owner"})
        self.service.accept_invitation(self.reviewer, invitation["invite_token"])
        self.source = self.cmd("account_create", {"name": "Operating"})["account"]["id"]
        self.target = self.cmd("account_create", {"name": "Reserve"})["account"]["id"]
        self.cmd("sandbox_fund", {"account_id": self.source, "amount": 250_000_000, "reason": "Recovery test opening funds"})

    def tearDown(self):
        self.temp.cleanup()

    def cmd(self, operation, body, actor=None):
        return self.service.command(self.tenant, actor or self.owner, operation, body, key())

    def action(self):
        return self.cmd("action_propose", {"source_id": self.source, "destination_id": self.target, "amount": 10_000, "purpose": "Recovery reservation verification"})["action"]

    def get(self, sk):
        return self.store.transact(lambda tx: tx.get(self.pk, sk))

    def put(self, sk, body):
        self.store.transact(lambda tx: tx.put(self.pk, sk, body))

    def test_live_wal_backup_and_restore_preserve_holds_without_resurrecting_sessions(self):
        with closing(sqlite3.connect(self.database)) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM documents").fetchone()
            action = self.action()
            self.assertGreater(Path(str(self.database) + "-wal").stat().st_size, 0)
            source = verify_database(self.database)
            saved = backup(self.database, self.root / "backup with # spaces")
            self.assertEqual(source, saved["integrity"])
        result = restore(self.root / "backup with # spaces", self.root / "restored")
        self.assertEqual(2, result["sessions_invalidated"])
        self.assertEqual(source["institutions"], result["integrity"]["institutions"])
        restored = DocumentStore(SQLiteBackend(self.root / "restored" / DATABASE))
        self.assertEqual(action, restored.transact(lambda tx: tx.get(self.pk, "ACTION#" + action["id"])))
        self.assertIsNone(LocalAuth(restored).actor({"Cookie": self.cookies[0]}))
        self.assertEqual(self.owner.sub, self.auth.actor({"Cookie": self.cookies[0]}).sub)
        self.assertEqual(source, verify_database(self.database))

    def test_file_changes_and_existing_destinations_are_rejected(self):
        directory = self.root / "backup"
        backup(self.database, directory)
        with self.assertRaises(FileExistsError):
            backup(self.database, directory)
        (directory / DATABASE).write_bytes((directory / DATABASE).read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "digest"):
            restore(directory, self.root / "failed-restore")
        self.assertFalse((self.root / "failed-restore").exists())

    def test_balance_reservation_usage_and_policy_corruption_stop_verification(self):
        action = self.action()
        usage_key = "USAGE#" + action["usage_date"] + "#GBP"
        policy_key = "POLICY#" + self.get("META")["policy_id"]
        for sk, change, message in [
            ("ACCOUNT#" + self.source, {"balance": 249_999_999}, "balance"),
            ("ACCOUNT#" + self.source, {"reserved": 0}, "reservation"),
            (usage_key, {"reserved": 0}, "Meter"),
            (policy_key, {"status": "superseded"}, "policy"),
            ("ACTION#" + action["id"], {"reserved": False}, "hold"),
        ]:
            original = self.get(sk)
            with self.subTest(sk=sk, change=change):
                self.put(sk, {**original, **change})
                with self.assertRaisesRegex(ValueError, message):
                    verify_database(self.database)
                self.put(sk, original)
        self.assertTrue(verify_database(self.database)["verified"])

    def test_missing_expiry_work_and_history_are_detected(self):
        action = self.action()
        job = self.store.transact(lambda tx: tx.get("WORK#platform", action["expiry_job_key"]))
        self.store.transact(lambda tx: tx.delete_work(action["expiry_job_key"]))
        with self.assertRaisesRegex(ValueError, "expiry job"):
            verify_database(self.database)
        self.store.transact(lambda tx: tx.put("WORK#platform", action["expiry_job_key"], job))
        with closing(sqlite3.connect(self.database)) as con, con:
            con.execute("DELETE FROM documents WHERE pk=? AND sk LIKE 'HISTORY#actions#%'", (self.pk,))
        with self.assertRaisesRegex(ValueError, "History index"):
            verify_database(self.database)

    def test_coherent_balance_and_action_rewrite_still_conflicts_with_posting_audit(self):
        action = self.action()
        action = self.cmd("action_approve", {"action_id": action["id"], "reason": "Independent posting verification"}, self.reviewer)["action"]
        self.put("ACTION#" + action["id"], {**action, "amount": action["amount"] + 1})
        for account_id, delta in ((self.source, -1), (self.target, 1)):
            account = self.get("ACCOUNT#" + account_id)
            self.put("ACCOUNT#" + account_id, {**account, "balance": account["balance"] + delta})
        usage_key = "USAGE#" + action["settled_at"][:10] + "#GBP"
        usage = self.get(usage_key)
        self.put(usage_key, {**usage, "executed": usage["executed"] + 1})
        journal_key = f"JOURNAL#{action['journal_sequence']:020d}"
        journal = self.get(journal_key)
        for posting in journal["postings"]:
            posting["debit" if posting["debit"] else "credit"] += 1
        self.put(journal_key, journal)
        with self.assertRaisesRegex(ValueError, "posting audit evidence"):
            verify_database(self.database)

    def test_reconciliation_rows_are_verified_against_original_journals(self):
        action = self.action()
        self.cmd("action_approve", {"action_id": action["id"], "reason": "Independent restore test approval"}, self.reviewer)
        record = self.cmd("reconciliation_create", {"account_id": self.source, "currency": "GBP", "source_sha256": "a" * 64,
            "statement_reference": "Recovery statement", "period_start": "2026-09-01", "period_end": "2026-09-30",
            "opening_balance": 250_000_000, "closing_balance": 249_990_000, "ledger_start": 1, "total_rows": 1})["reconciliation"]
        self.cmd("reconciliation_append", {"reconciliation_id": record["id"], "offset": 0, "rows": [{"external_id": "recovery-row", "booked_date": "2026-09-07", "amount": -10_000, "reference": action["id"]}]})
        self.cmd("reconciliation_finish", {"reconciliation_id": record["id"]})
        runner = Runner(self.service)
        runner.tick(); runner.tick()
        self.assertTrue(verify_database(self.database)["verified"])
        row_key = "RECONLEDGER#" + record["id"] + "#00000000000000000002"
        row = self.get(row_key)
        self.put(row_key, {**row, "journal_hash": "f" * 64})
        with self.assertRaisesRegex(ValueError, "original journals"):
            verify_database(self.database)

    def test_corrupt_source_never_gets_a_complete_backup_manifest(self):
        account = self.get("ACCOUNT#" + self.source)
        self.put("ACCOUNT#" + self.source, {**account, "balance": 1})
        with self.assertRaises(ValueError):
            backup(self.database, self.root / "incomplete")
        self.assertFalse((self.root / "incomplete" / "manifest.json").exists())

    def test_recovery_http_uses_separate_login_and_denies_every_execution_path(self):
        self.action()
        backup(self.database, self.root / "backup")
        result = restore(self.root / "backup", self.root / "restored")
        restored_path = self.root / "restored" / DATABASE
        server, _ = local.create_server(0, restored_path, True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        jar = CookieJar()
        browser = build_opener(HTTPCookieProcessor(jar))
        base = "http://127.0.0.1:" + str(server.server_port)
        def call(path, body=None, headers=None):
            request = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                              headers={"Content-Type": "application/json", "Idempotency-Key": key(), **(headers or {})})
            try:
                with browser.open(request, timeout=5) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as error:
                return error.code, json.loads(error.read())
        try:
            self.assertEqual(401, call("/api/platform/me", headers={"Cookie": self.cookies[0]})[0])
            self.assertEqual(200, call("/api/platform-auth/login", {"email": self.owner.email, "password": PASSWORD})[0])
            self.assertTrue(all(c.name.startswith("agentu_recovery_") and c.name != COOKIE for c in jar))
            route = "/api/platform/institutions/" + self.tenant
            self.assertEqual([], call(route + "/overview")[1]["permissions"])
            self.assertTrue(call("/agentu/app/config.json")[1]["readOnly"])
            for path in ("/api/platform-auth/register", route + "/commands/action_propose", "/api/agent/proposals", "/api/platform/invitations/accept", "/api/platform/institutions", "/api/reset"):
                status, body = call(path, {})
                self.assertEqual((403, "recovery_read_only"), (status, body["code"]))
            self.assertEqual(result["integrity"]["institutions"], verify_database(restored_path)["institutions"])
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_recovery_refuses_primary_or_missing_database(self):
        for path in (None, self.root / "missing.sqlite3"):
            with self.assertRaises(ValueError):
                local.create_server(0, path, True)


if __name__ == "__main__":
    unittest.main()
