"""Build creation-ordered history for one existing institution.

Stop old local processes, or deploy both compatible AWS writers and drain older
invocations, before this upgrade. Each batch is atomic and safe to resume.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from platform_core.history_migration import step, begin_rebuild
from platform_core.store import DocumentStore, SQLiteBackend, DynamoBackend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--sqlite", type=Path, help="Existing local platform database")
    target.add_argument("--stage", choices=["sandbox", "demo"])
    parser.add_argument("--institution", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--rebuild", action="store_true", help="Reindex after an older-code rollback; retains source records and existing pointers")
    parser.add_argument("--max-batches", type=int, default=100, help="Bound work per invocation; rerun to resume")
    args = parser.parse_args()
    if not 1 <= args.max_batches <= 1000:
        parser.error("Use between 1 and 1,000 batches.")
    if args.sqlite:
        if not args.sqlite.is_file():
            parser.error("The local database must already exist.")
        backend = SQLiteBackend(args.sqlite)
    else:
        from deploy import clients, outputs, REGION
        session = clients(args.profile, REGION)
        _, values = outputs(session, args.stage)
        backend = DynamoBackend(values["PlatformTable"], session.client("dynamodb"))
    store = DocumentStore(backend)
    if args.rebuild:
        begin_rebuild(store, args.institution)
    for _ in range(args.max_batches):
        result = step(store, args.institution)
        if result["status"] == "complete":
            print(json.dumps(result))
            return 0
    print(json.dumps({**result, "resume": "Run the same command to continue."}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
