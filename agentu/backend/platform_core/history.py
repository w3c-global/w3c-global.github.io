"""Creation-ordered indexes, committed atomically with their domain records.

An index contains a pointer, not a copy of mutable status or financial evidence.
Journal and audit exports retain their original ascending sequence order.
"""
import re
from datetime import datetime
from .errors import PlatformError
from .model import identifier

VERSION = 1
COLLECTIONS = {"actions": "ACTION#", "runs": "RUN#", "reconciliations": "RECON#"}


def prefix(collection):
    return "HISTORY#" + collection + "#"


def index_key(collection, record):
    created = record.get("created_at", "")
    if not isinstance(created, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", created):
        raise PlatformError("History needs a canonical creation timestamp.", 409, "history_inconsistent")
    try:
        datetime.fromisoformat(created)
    except ValueError as exc:
        raise PlatformError("History has an invalid creation date.", 409, "history_inconsistent") from exc
    # Fixed-width UTC components sort chronologically. Inverting the digits
    # gives newest-first order with the same query on both storage adapters.
    reversed_time = "".join(str(9 - int(c)) for c in created if c.isdigit())
    return prefix(collection) + reversed_time + "#" + identifier(record["id"])


def index_record(tx, pk, collection, record):
    key = index_key(collection, record)
    pointer = {"record_key": COLLECTIONS[collection] + record["id"], "created_at": record["created_at"]}
    existing = tx.get(pk, key)
    if existing is not None and existing != pointer:
        raise PlatformError("The history pointer is inconsistent.", 409, "history_inconsistent")
    if existing is None:
        tx.put(pk, key, pointer, insert_only=True)


def page(tx, pk, tenant, collection, after=None, limit=40):
    if tenant.get("history_version") != VERSION:
        raise PlatformError("Activity history needs a storage upgrade. An administrator must run the history migration before this list is available.", 503, "history_upgrade_required")
    if type(limit) is not int or not 1 <= limit <= 60:
        raise PlatformError("Page size must be between 1 and 60.")
    if after is not None and (not isinstance(after, str) or not after.startswith(prefix(collection))):
        raise PlatformError("The history cursor is invalid or belongs to an older version. Refresh the list.")
    # Pointers and original records both count towards the 90-item read set.
    pointers, cursor = tx.query(pk, prefix(collection), after=after, limit=min(limit, 40))
    items = []
    for pointer in pointers:
        key = pointer.get("record_key")
        if not isinstance(key, str) or not key.startswith(COLLECTIONS[collection]):
            raise PlatformError("The history pointer is inconsistent.", 409, "history_inconsistent")
        record = tx.get(pk, key)
        if not record or key != COLLECTIONS[collection] + record["id"] or record["created_at"] != pointer["created_at"]:
            raise PlatformError("A history record is missing or inconsistent.", 409, "history_inconsistent")
        items.append(record)
    return items, cursor
