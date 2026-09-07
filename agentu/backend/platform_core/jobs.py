"""Durable transient work. Completion evidence lives in retained domain records."""
import time
from .model import new_id

QUEUE = "WORK#platform"


def enqueue(tx, kind, tenant_id, reference, *, due=None):
    due = time.time() if due is None else due
    key = f"DUE#{int(due * 1000):013d}#{new_id()}"
    tx.put(QUEUE, key, {"key": key, "kind": kind, "tenant_id": tenant_id, "reference": reference,
                       "due": due, "attempts": 0, "lease_until": 0}, insert_only=True)
    return key
