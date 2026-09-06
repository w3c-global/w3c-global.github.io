"""Global history order, current records, migration and transaction boundaries."""
from contextlib import closing
import json
import unittest
from unittest.mock import Mock, patch
import test_agents as fixtures
from platform_core.accounting import AccountingService
from platform_core.errors import PlatformError
from platform_core.history import index_key, prefix
from platform_core.history_migration import step, begin_rebuild, MARKER
from platform_core.model import Actor
from platform_core.store import DocumentStore, SQLiteBackend, DynamoBackend


class HistoryTests(unittest.TestCase):
    tearDown = fixtures.AgentTests.tearDown
    cmd = fixtures.AgentTests.cmd
    get = fixtures.AgentTests.get
    config = fixtures.AgentTests.config
    agent = fixtures.AgentTests.agent
    credentials = fixtures.AgentTests.credentials
    proposal = fixtures.AgentTests.proposal

    def setUp(self):
        fixtures.AgentTests.setUp(self)
        self.service = AccountingService(self.store)

    def action(self, number=0, request_id=None):
        with patch("platform_core.service.now", return_value=f"2026-09-07T01:00:{number:02d}.000Z"):
            return self.cmd("action_propose", self.proposal(1), request_id=request_id)["action"]

    def collection(self, name="actions", after=None, limit=40):
        return self.service.collection(self.tenant, self.owner, name, after, limit)

    def raw(self, collection):
        with closing(self.store.backend.connect()) as con, con:
            return [(sk, json.loads(body)) for sk, body in con.execute(
                "SELECT sk,body FROM documents WHERE pk=? AND sk LIKE ? ORDER BY sk", (self.pk, collection + "%"))]

    def make_legacy(self):
        # Simulate an older release only inside this test's disposable database.
        with closing(self.store.backend.connect()) as con, con:
            con.execute("DELETE FROM documents WHERE pk=? AND sk LIKE 'HISTORY#%'", (self.pk,))
            meta = json.loads(con.execute("SELECT body FROM documents WHERE pk=? AND sk='META'", (self.pk,)).fetchone()[0])
            meta.pop("history_version")
            con.execute("UPDATE documents SET body=?, version=version+1 WHERE pk=? AND sk='META'", (json.dumps(meta), self.pk))

    def test_newest_records_are_visible_across_pages_and_concurrent_inserts(self):
        actions = [self.action(n) for n in range(45)]
        page = self.collection(limit=60)
        self.assertEqual("created_desc", page["order"])
        self.assertEqual([a["id"] for a in reversed(actions[5:])], [a["id"] for a in page["items"]])
        later = self.action(50)
        tail = self.collection(after=page["next_cursor"])
        self.assertEqual([a["id"] for a in reversed(actions[:5])], [a["id"] for a in tail["items"]])
        self.assertIsNone(tail["next_cursor"])
        self.assertEqual(later["id"], self.collection()["items"][0]["id"])
        self.assertEqual(actions[0]["id"], self.service.action(self.tenant, self.owner, actions[0]["id"])["action"]["id"])

    def test_equal_timestamps_have_stable_cursor_order(self):
        actions = [self.action() for _ in range(4)]
        ids, after = [], None
        while True:
            page = self.collection(after=after, limit=1)
            ids.extend(a["id"] for a in page["items"])
            after = page["next_cursor"]
            if after is None:
                break
        self.assertEqual(sorted(a["id"] for a in actions), ids)

    def test_indexes_show_current_status_without_duplicate_replays_or_tenant_leaks(self):
        request_id = fixtures.key()
        action = self.action(request_id=request_id)
        self.action(request_id=request_id)
        self.cmd("action_cancel", {"action_id": action["id"], "reason": "Cancel history verification"})
        self.assertEqual(1, len(self.raw(prefix("actions"))))
        self.assertEqual("cancelled", self.collection()["items"][0]["status"])
        with self.assertRaises(PlatformError) as error:
            self.service.action(self.tenant, Actor(fixtures.key(), "outsider@example.test", True), action["id"])
        self.assertEqual("forbidden", error.exception.code)
        journals = self.collection("journal")
        self.assertEqual("record_key_asc", journals["order"])
        self.assertEqual([1], [r["sequence"] for r in journals["items"]])

    def test_internal_and_external_runs_and_statement_imports_are_indexed(self):
        agent = self.agent()
        internal = self.cmd("agent_run", {"agent_id": agent["id"]})["run"]
        external_agent = self.agent("external")
        credential = self.credentials(external_agent)
        external = self.service.submit_agent(credential["agent_token"], self.proposal(1), fixtures.key())["run"]
        self.assertEqual({internal["id"], external["id"]}, {r["id"] for r in self.collection("runs")["items"]})
        record = self.cmd("reconciliation_create", {"account_id": self.source, "currency": "GBP",
            "source_sha256": "a" * 64, "statement_reference": "History import", "period_start": "2026-09-01", "period_end": "2026-09-30",
            "opening_balance": 250_000_000, "closing_balance": 250_000_000, "total_rows": 0})["reconciliation"]
        self.assertEqual(record["id"], self.collection("reconciliations")["items"][0]["id"])

    def test_migration_resumes_with_new_writers_and_preserves_domain_evidence(self):
        originals = [self.action(n) for n in range(35)]
        before = dict(self.raw("ACTION#"))
        journal = self.raw("JOURNAL#")
        audit_before = self.raw("EVENT#")
        self.make_legacy()
        with self.assertRaises(PlatformError) as error:
            self.collection()
        self.assertEqual("history_upgrade_required", error.exception.code)
        self.assertEqual("running", step(self.store, self.tenant, 7)["status"])
        new_action = self.action(50)
        self.store = DocumentStore(SQLiteBackend(self.store.backend.path))
        self.service = AccountingService(self.store)
        for _ in range(20):
            if step(self.store, self.tenant, 7)["status"] == "complete":
                break
        else:
            self.fail("Migration did not complete its bounded batches")
        self.assertEqual([new_action["id"]] + [a["id"] for a in reversed(originals)], [a["id"] for a in self.collection()["items"]])
        after = dict(self.raw("ACTION#"))
        self.assertTrue(all(after[k] == v for k, v in before.items()))
        self.assertEqual(journal, self.raw("JOURNAL#"))
        self.assertEqual(audit_before, self.raw("EVENT#")[:len(audit_before)])
        events = self.raw("EVENT#")
        self.assertEqual("history_index_upgraded", events[-1][1]["kind"])
        self.assertEqual("complete", step(self.store, self.tenant)["status"])
        self.assertEqual(events, self.raw("EVENT#"))

    def test_bad_source_fails_a_batch_without_partial_index_writes(self):
        actions = [self.action(n) for n in range(3)]
        self.make_legacy()
        bad = dict(actions[1], created_at="invalid")
        self.store.transact(lambda tx: tx.put(self.pk, "ACTION#" + bad["id"], bad))
        with self.assertRaises(PlatformError):
            step(self.store, self.tenant)
        self.assertEqual([], self.raw(prefix("actions")))
        self.assertEqual([], self.raw(MARKER))
        self.store.transact(lambda tx: tx.put(self.pk, "ACTION#" + actions[1]["id"], actions[1]))
        for _ in range(3):
            step(self.store, self.tenant)
        self.assertEqual(3, len(self.collection()["items"]))

    def test_wrong_cursors_and_broken_pointers_fail_closed(self):
        action = self.action()
        for after in ("ACTION#" + action["id"], prefix("runs") + "123"):
            with self.assertRaises(PlatformError):
                self.collection(after=after)
        pointer_key = index_key("actions", action)
        self.store.transact(lambda tx: tx.put(self.pk, pointer_key, {"record_key": "ACCOUNT#" + self.source, "created_at": action["created_at"]}))
        with self.assertRaises(PlatformError) as error:
            self.collection()
        self.assertEqual("history_inconsistent", error.exception.code)

    def test_rebuild_recovers_records_created_by_an_older_writer(self):
        first = self.action()
        older_writer_record = dict(first, id=fixtures.key(), created_at="2026-09-07T02:00:00.000Z")
        self.store.transact(lambda tx: tx.put(self.pk, "ACTION#" + older_writer_record["id"], older_writer_record, insert_only=True))
        self.assertEqual(1, len(self.collection()["items"]))
        begin_rebuild(self.store, self.tenant)
        step(self.store, self.tenant, 1)
        progress = self.raw(MARKER)
        begin_rebuild(self.store, self.tenant)
        self.assertEqual(progress, self.raw(MARKER))
        for _ in range(5):
            if step(self.store, self.tenant, 1)["status"] == "complete":
                break
        self.assertEqual([older_writer_record["id"], first["id"]], [r["id"] for r in self.collection()["items"]])

    def test_dynamo_history_page_fits_and_checks_the_complete_read_set(self):
        records = [self.action(n) for n in range(41)]
        values = dict(self.raw(""))
        def item(sk):
            return {"pk": {"S": self.pk}, "sk": {"S": sk}, "version": {"N": "1"}, "body": {"S": json.dumps(values[sk])}}
        pointer_keys = sorted(k for k in values if k.startswith(prefix("actions")))[:40]
        client = Mock()
        client.get_item.side_effect = lambda **kwargs: {"Item": item(kwargs["Key"]["sk"]["S"])}
        client.query.return_value = {"Items": [item(k) for k in pointer_keys], "LastEvaluatedKey": DynamoBackend.key(self.pk, pointer_keys[-1])}
        service = AccountingService(DocumentStore(DynamoBackend("business-platform", client)))
        page = service.collection(self.tenant, self.owner, "actions", limit=60)
        self.assertEqual([r["id"] for r in reversed(records[1:])], [r["id"] for r in page["items"]])
        request = client.query.call_args.kwargs
        self.assertEqual(40, request["Limit"])
        self.assertTrue(request["ConsistentRead"])
        checks = client.transact_write_items.call_args.kwargs["TransactItems"]
        self.assertEqual(82, len(checks))
        self.assertTrue(all("ConditionCheck" in x for x in checks))


if __name__ == "__main__":
    unittest.main()
