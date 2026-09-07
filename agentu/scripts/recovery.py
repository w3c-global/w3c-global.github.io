"""Consistent local backups and quarantined restore copies; never overwrite a source.

Backup files contain the complete local database, including hashed credentials.
Keep their directory private. Manifests detect changes but are not signatures.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from verify_database import verify_database

DATABASE = "platform.sqlite3"
MANIFEST = "manifest.json"


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def copy_database(source, target):
    source = Path(source).resolve(strict=True)
    target = Path(target).resolve()
    # Exclusively claim the output. A failed copy is left for investigation.
    with target.open("xb"):
        pass
    deadline = time.monotonic() + 55
    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise TimeoutError("The backup exceeded 55 seconds. Retry into a new directory during a quieter period.")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst, pages=256, progress=progress, sleep=0.1)
        # A self-contained backup must not depend on a sidecar WAL file.
        dst.execute("PRAGMA journal_mode=DELETE")


def backup(source, destination):
    destination = Path(destination).resolve()
    Path(source).resolve(strict=True)
    started = timestamp()
    destination.mkdir(parents=True, exist_ok=False)
    database = destination / DATABASE
    copy_database(source, database)
    integrity = verify_database(database)
    manifest = {"schema": "agentu.recovery.backup.v1", "database": DATABASE,
                "started_at": started, "completed_at": timestamp(), "file_sha256": sha256(database),
                "integrity": integrity, "contains_local_credentials": True,
                "external_signature": False, "scope": "institution platform; guided-demo file state is separate"}
    write_json(destination / MANIFEST, manifest)
    return manifest


def verify_backup(directory):
    directory = Path(directory).resolve(strict=True)
    manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("schema") != "agentu.recovery.backup.v1" or manifest.get("database") != DATABASE:
        raise ValueError("Unsupported backup manifest.")
    database = directory / DATABASE
    if database.is_symlink() or database.resolve().parent != directory:
        raise ValueError("The backup database must be a regular file in its manifest directory.")
    if sha256(database) != manifest["file_sha256"]:
        raise ValueError("The backup file differs from its recorded digest.")
    integrity = verify_database(database)
    if integrity != manifest["integrity"]:
        raise ValueError("The reconstructed records differ from the backup manifest.")
    return manifest


def restore(directory, destination):
    manifest = verify_backup(directory)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    database = destination / DATABASE
    copy_database(Path(directory) / DATABASE, database)
    before = verify_database(database)
    if before != manifest["integrity"]:
        raise ValueError("The restored data differs from the verified backup.")
    # Do not resurrect browser sessions from a recovery point. Financial and
    # historical authority records remain unchanged for independent inspection.
    with closing(sqlite3.connect(database)) as connection, connection:
        count = 0
        for pk, sk, body in connection.execute("SELECT pk,sk,body FROM documents WHERE pk LIKE 'LOCAL_SESSION#%'").fetchall():
            value = json.loads(body)
            if value.get("expires_at", 0) > 0:
                value["expires_at"] = 0
                connection.execute("UPDATE documents SET body=?,version=version+1 WHERE pk=? AND sk=?", (json.dumps(value), pk, sk))
                count += 1
    after = verify_database(database)
    if before["institutions"] != after["institutions"]:
        raise ValueError("The restore changed institution records during session invalidation.")
    report = {"schema": "agentu.recovery.restore.v1", "completed_at": timestamp(),
              "source_file_sha256": manifest["file_sha256"], "source_logical_sha256": before["logical_sha256"],
              "sessions_invalidated": count, "integrity": after, "quarantined": True,
              "instructions": "Use local.py --recovery --platform-db <this directory>/platform.sqlite3 --port 4323. Reauthorize identities and reconcile post-backup activity before any live cutover."}
    write_json(destination / "recovery.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("backup")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--out", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("directory", type=Path)
    recover = commands.add_parser("restore")
    recover.add_argument("directory", type=Path)
    recover.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "backup":
        result = backup(args.source, args.out)
    elif args.command == "verify":
        result = verify_backup(args.directory)
    else:
        result = restore(args.directory, args.out)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
        raise SystemExit("Recovery stopped: " + str(error))
