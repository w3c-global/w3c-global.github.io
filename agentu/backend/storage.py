"""Optimistic writes keep decisions, balances and evidence together atomically."""
import json
import os
import threading
import time
from pathlib import Path
from domain import fresh_state, DomainError


class FileStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def transact(self, key, transform=None):
        import hashlib
        path = self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        with self.lock:
            state = json.loads(path.read_text()) if path.exists() else fresh_state()
            if transform:
                state, changed = transform(state)
                if changed:
                    temp = path.with_suffix(".tmp")
                    temp.write_text(json.dumps(state), encoding="utf-8")
                    os.replace(temp, path)
            return state


class DynamoStore:
    def __init__(self, name):
        import boto3
        self.table = boto3.resource("dynamodb").Table(name)

    def transact(self, key, transform=None):
        for _ in range(5):
            item = self.table.get_item(Key={"pk": key}, ConsistentRead=True).get("Item")
            # Expired records are no longer readable even while TTL deletion is pending.
            expired = bool(item and int(item["expires_at"]) <= int(time.time()))
            state = json.loads(item["document"]) if item and not expired else fresh_state()
            if not transform:
                return state
            updated, changed = transform(state)
            if not changed:
                return updated
            document = json.dumps(updated, separators=(",", ":"))
            if len(document.encode()) > 350_000:
                raise DomainError("This rehearsal is full. Start a new rehearsal.", 409)
            values = {":version": item["version"]} if item else None
            kwargs = {"Item": {"pk": key, "version": updated["version"], "document": document,
                       "expires_at": int(time.time()) + 7 * 86400},
                      "ConditionExpression": "#v = :version" if item else "attribute_not_exists(pk)"}
            if values:
                kwargs.update(ExpressionAttributeValues=values, ExpressionAttributeNames={"#v": "version"})
            try:
                self.table.put_item(**kwargs)
                return updated
            except self.table.meta.client.exceptions.ConditionalCheckFailedException:
                continue
        raise DomainError("Another request changed this rehearsal. Please try again.", 409)
