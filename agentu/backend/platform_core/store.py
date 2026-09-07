"""Version-checked document transactions shared by SQLite and DynamoDB.

The unit of work records every item read, including missing items. Commit validates
the whole read set, so an approval cannot race a changed role, policy or balance.
Domain code must never perform external side effects inside a retryable callback.
"""
import copy
import json
import random
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from .errors import Conflict, PlatformError


class UnitOfWork:
    def __init__(self, backend):
        self.backend = backend
        self.reads = {}
        self.writes = {}

    def get(self, pk, sk):
        key = (pk, sk)
        if key in self.writes:
            return copy.deepcopy(self.writes[key])
        if key not in self.reads:
            self.reads[key] = self.backend.get(pk, sk)
        return copy.deepcopy(self.reads[key][1])

    def put(self, pk, sk, document, *, insert_only=False):
        existing = self.get(pk, sk)
        if insert_only and existing is not None:
            raise PlatformError("This record already exists.", 409, "already_exists")
        encoded = json.dumps(document, separators=(",", ":"))
        if len(encoded.encode()) > 300_000:
            raise PlatformError("Record exceeds the supported size.", 413, "record_too_large")
        self.writes[(pk, sk)] = copy.deepcopy(document)

    def query(self, pk, prefix, *, after=None, limit=40):
        if not 1 <= limit <= 60:
            raise PlatformError("Page size must be between 1 and 60.")
        rows, cursor = self.backend.query(pk, prefix, after=after, limit=limit)
        for sk, version, body in rows:
            key = (pk, sk)
            if key in self.reads and self.reads[key][0] != version:
                raise Conflict()
            self.reads[key] = (version, body)
        return [copy.deepcopy(body) for _, _, body in rows], cursor

    def delete_work(self, sk):
        # Deletion is deliberately limited to the transient work queue. Domain
        # accounts, journal and evidence records cannot use this operation.
        if not isinstance(sk, str) or not sk.startswith("DUE#"):
            raise PlatformError("Only transient work items can be deleted.")
        key = ("WORK#platform", sk)
        if self.get(*key) is not None:
            self.writes[key] = None


class DocumentStore:
    def __init__(self, backend):
        self.backend = backend

    def transact(self, operation):
        for attempt in range(6):
            transaction = UnitOfWork(self.backend)
            try:
                try:
                    result = operation(transaction)
                except PlatformError:
                    # A domain failure can itself result from an inconsistent
                    # read (e.g. another writer used the next journal number).
                    # Validate without applying partial writes, then retry if stale.
                    transaction.writes.clear()
                    if len(transaction.reads) <= 90:
                        self.backend.commit(transaction)
                    raise
                if len(transaction.reads) > 90:
                    raise PlatformError("Transaction requires a smaller page or batch.", 413, "transaction_too_large")
                self.backend.commit(transaction)
                return result
            except Conflict:
                if attempt == 5:
                    raise PlatformError("The records changed concurrently. Retry this request with the same idempotency key.", 409, "concurrent_change")
                time.sleep(random.uniform(0.002, 0.02) * (attempt + 1))


class SQLiteBackend:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS documents (pk TEXT NOT NULL, sk TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY (pk, sk)) WITHOUT ROWID")
            connection.execute("PRAGMA optimize")

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def get(self, pk, sk):
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT version, body FROM documents WHERE pk=? AND sk=?", (pk, sk)).fetchone()
            return (row[0], json.loads(row[1])) if row else (None, None)

    def query(self, pk, prefix, *, after=None, limit=40):
        if after is not None and (not isinstance(after, str) or not after.startswith(prefix)):
            raise PlatformError("Invalid page cursor.")
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT sk, version, body FROM documents WHERE pk=? AND sk>=? AND sk<? AND sk>? ORDER BY sk LIMIT ?",
                (pk, prefix, prefix + "\uffff", after or "", limit + 1)).fetchall()
        cursor = rows[limit - 1][0] if len(rows) > limit else None
        return [(sk, version, json.loads(body)) for sk, version, body in rows[:limit]], cursor

    def commit(self, transaction):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for (pk, sk), (expected, _) in transaction.reads.items():
                row = connection.execute("SELECT version FROM documents WHERE pk=? AND sk=?", (pk, sk)).fetchone()
                actual = row[0] if row else None
                if actual != expected:
                    raise Conflict()
            for (pk, sk), document in transaction.writes.items():
                if document is None:
                    connection.execute("DELETE FROM documents WHERE pk=? AND sk=?", (pk, sk))
                    continue
                version = (transaction.reads[(pk, sk)][0] or 0) + 1
                connection.execute("INSERT INTO documents (pk, sk, version, body) VALUES (?, ?, ?, ?) ON CONFLICT(pk, sk) DO UPDATE SET version=excluded.version, body=excluded.body",
                                   (pk, sk, version, json.dumps(document, separators=(",", ":"))))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class DynamoBackend:
    def __init__(self, table_name, client=None):
        if client is None:
            import boto3
            client = boto3.client("dynamodb")
        self.client = client
        self.table_name = table_name

    @staticmethod
    def key(pk, sk):
        return {"pk": {"S": pk}, "sk": {"S": sk}}

    def get(self, pk, sk):
        item = self.client.get_item(TableName=self.table_name, Key=self.key(pk, sk), ConsistentRead=True).get("Item")
        return (int(item["version"]["N"]), json.loads(item["body"]["S"])) if item else (None, None)

    def query(self, pk, prefix, *, after=None, limit=40):
        if after is not None and (not isinstance(after, str) or not after.startswith(prefix)):
            raise PlatformError("Invalid page cursor.")
        kwargs = {"TableName": self.table_name, "KeyConditionExpression": "pk=:pk AND begins_with(sk,:prefix)",
                  "ExpressionAttributeValues": {":pk": {"S": pk}, ":prefix": {"S": prefix}}, "ConsistentRead": True, "Limit": limit}
        if after:
            kwargs["ExclusiveStartKey"] = self.key(pk, after)
        result = self.client.query(**kwargs)
        rows = [(i["sk"]["S"], int(i["version"]["N"]), json.loads(i["body"]["S"])) for i in result.get("Items", [])]
        cursor = result.get("LastEvaluatedKey", {}).get("sk", {}).get("S")
        return rows, cursor

    def commit(self, transaction):
        items = []
        for (pk, sk), (version, _) in transaction.reads.items():
            args = {"TableName": self.table_name, "ConditionExpression": "attribute_not_exists(pk)" if version is None else "#version = :version"}
            if version is not None:
                args.update(ExpressionAttributeNames={"#version": "version"}, ExpressionAttributeValues={":version": {"N": str(version)}})
            if (pk, sk) in transaction.writes and transaction.writes[(pk, sk)] is None:
                args["Key"] = self.key(pk, sk)
                items.append({"Delete": args})
            elif (pk, sk) in transaction.writes:
                args["Item"] = {**self.key(pk, sk), "version": {"N": str((version or 0) + 1)},
                                "body": {"S": json.dumps(transaction.writes[(pk, sk)], separators=(",", ":"))}}
                items.append({"Put": args})
            else:
                args["Key"] = self.key(pk, sk)
                items.append({"ConditionCheck": args})
        if not items:
            return
        # A single call is atomic. The persistent request record provides business
        # idempotency beyond DynamoDB's 10-minute client-token window.
        try:
            self.client.transact_write_items(TransactItems=items)
        except Exception as exc:
            detail = getattr(exc, "response", {})
            if detail.get("Error", {}).get("Code") == "TransactionConflictException":
                raise Conflict() from exc
            if detail.get("Error", {}).get("Code") == "TransactionCanceledException":
                reasons = {r.get("Code") for r in detail.get("CancellationReasons", [])} - {None, "None"}
                if reasons and reasons <= {"ConditionalCheckFailed", "TransactionConflict"}:
                    raise Conflict() from exc
            raise
