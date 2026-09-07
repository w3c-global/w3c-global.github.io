"""Resumable index-only upgrade. Deploy compatible writers before running it."""
from .errors import PlatformError
from .history import COLLECTIONS, VERSION, index_record
from .model import Actor
from .service import audit, required, tenant_key

MARKER = "MIGRATION#history-v1"
SYSTEM = Actor("system:history-migration", "", True)


def begin_rebuild(store, tenant_id):
    """Invalidate a finished index before rebuilding after an older-code rollback."""
    def operation(tx):
        pk = tenant_key(tenant_id)
        tenant = required(tx, pk, "META")
        if tenant.get("history_version", 0) == 0:
            return {"status": "running"}  # Resume an already-started upgrade.
        if tenant.get("history_version") != VERSION:
            raise PlatformError("This storage version needs a different migration.", 409, "unsupported_history_version")
        tenant["history_version"] = 0
        tx.put(pk, "META", tenant)
        tx.put(pk, MARKER, {"status": "running", "collection": 0, "after": None,
                            "checked": {name: 0 for name in COLLECTIONS}})
        audit(tx, pk, SYSTEM, "history_index_rebuild_started", {"version": VERSION})
        return {"status": "running"}
    return store.transact(operation)


def step(store, tenant_id, batch_size=30):
    if type(batch_size) is not int or not 1 <= batch_size <= 30:
        raise PlatformError("Migration batches must contain 1 to 30 records.")

    def operation(tx):
        pk = tenant_key(tenant_id)
        tenant = required(tx, pk, "META")
        if tenant.get("history_version") == VERSION:
            return {"status": "complete", "version": VERSION}
        if tenant.get("history_version", 0) != 0:
            raise PlatformError("This storage version needs a different migration.", 409, "unsupported_history_version")
        progress = tx.get(pk, MARKER) or {"status": "running", "collection": 0, "after": None,
                                         "checked": {name: 0 for name in COLLECTIONS}}
        name = list(COLLECTIONS)[progress["collection"]]
        records, cursor = tx.query(pk, COLLECTIONS[name], after=progress["after"], limit=batch_size)
        for record in records:
            index_record(tx, pk, name, record)
        progress["checked"][name] += len(records)
        progress["after"] = cursor
        if cursor is None:
            progress["collection"] += 1
        if progress["collection"] == len(COLLECTIONS):
            progress["status"] = "complete"
            tenant["history_version"] = VERSION
            tx.put(pk, "META", tenant)
            audit(tx, pk, SYSTEM, "history_index_upgraded", {"version": VERSION, "checked": progress["checked"]})
        tx.put(pk, MARKER, progress)
        return progress

    return store.transact(operation)
