"""Verify sealed exports offline against a separately trusted public-key record.

Python verifies record semantics; Node's standard crypto module verifies RSA-PSS.
No AWS session or application write service is used by this verifier.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))
from environments import EXPECTED_ACCOUNT, REGION, STAGES, environment
from verify_platform_export import verify as verify_export

ALGORITHM = "RSASSA_PSS_SHA_256"
FILES = ("audit.json", "journal.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON property.")
            result[key] = value
        return result
    def invalid(_value):
        raise ValueError("Non-finite JSON numbers are not permitted.")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def export_pair(audit_bytes, journal_bytes):
    audit, journal = strict_json(audit_bytes), strict_json(journal_bytes)
    require(isinstance(audit, dict) and isinstance(journal, dict), "Exports must be JSON objects.")
    require(audit.get("collection") == "audit" and journal.get("collection") == "journal", "Supply an audit export and a journal export.")
    verify_export(audit); verify_export(journal)
    fields = ("institution_id", "mode", "as_of_sequence", "as_of_head", "journal_sequence")
    require(all(audit.get(k) == journal.get(k) for k in fields), "The exports do not share one institution and evidence snapshot.")
    require(re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", audit["institution_id"]) and audit["mode"] in ("sandbox", "live"), "Invalid institution or mode.")
    require(all(type(audit[k]) is int and audit[k] >= 0 for k in ("as_of_sequence", "journal_sequence")), "Snapshot counters must be nonnegative integers.")
    posted = [e["data"] for e in audit["items"] if e["kind"] in ("sandbox_funding_posted", "internal_transfer_posted", "journal_reversal_posted")]
    require(posted == journal["items"], "Journal records disagree with their hash-linked audit evidence.")
    return {k: audit[k] for k in fields}


def validate_trust(trust, stage):
    environment(stage)
    require(isinstance(trust, dict), "The trust record must be a JSON object.")
    require(trust.get("schema") == "agentu.evidence.trust.v1", "Unsupported public-key trust record.")
    require(trust.get("account") == EXPECTED_ACCOUNT and trust.get("region") == REGION and trust.get("stage") == stage, "The trusted key belongs to another business environment.")
    require(re.fullmatch(f"arn:aws:kms:{REGION}:{EXPECTED_ACCOUNT}:key/[a-f0-9-]{{36}}", trust.get("key_arn", "")), "Use a full key ARN in the designated business account.")
    public = base64.b64decode(trust["public_key_spki_base64"], validate=True)
    require(sha256(public) == trust.get("public_key_sha256"), "The trusted public key fingerprint is inconsistent.")
    require(trust.get("algorithm") == ALGORITHM, "Unsupported signing algorithm.")
    return public


def signature(seal, trust):
    require(isinstance(seal, dict), "The evidence seal must be a JSON object.")
    require(seal.get("schema") == "agentu.evidence.seal.v1", "Unsupported evidence seal.")
    # The key comes only from the explicit trust record, never from the bundle.
    result = subprocess.run(["node", str(Path(__file__).with_name("verify_signature.mjs"))],
        input=json.dumps({**{k: seal[k] for k in ("message_base64", "signature_base64")}, "public_key_spki_base64": trust["public_key_spki_base64"]}),
        text=True, capture_output=True, timeout=15, check=False)
    require(result.returncode == 0, result.stderr.strip() or "Offline signature verification failed.")
    verified = strict_json(result.stdout)
    require(verified.get("signature_valid") is True and verified.get("public_key_sha256") == trust["public_key_sha256"], "The verifier did not confirm the trusted signature.")


def verify_bytes(seal, audit_bytes, journal_bytes, trust, stage, institution_id):
    validate_trust(trust, stage)
    signature(seal, trust)
    raw = base64.b64decode(seal["message_base64"], validate=True)
    payload = strict_json(raw)
    require(isinstance(payload, dict), "The signed snapshot must be a JSON object.")
    require(raw == canonical(payload) and payload.get("schema") == "agentu.evidence.snapshot.v1", "Unsupported or noncanonical signed snapshot.")
    require(payload.get("account") == EXPECTED_ACCOUNT and payload.get("region") == REGION and payload.get("stage") == stage, "The signature covers a different environment.")
    require(payload.get("institution_id") == institution_id, "The signature covers a different institution.")
    require(payload.get("signing") == {k: trust[k] for k in ("key_arn", "algorithm", "public_key_sha256")}, "The signed key identity differs from the independently trusted key.")
    require(payload.get("source") == "operator_supplied_exports" and payload.get("source_authenticated") is False, "The seal misrepresents the source's authentication.")
    require(re.fullmatch(r"[a-f0-9-]{36}", payload.get("bundle_id", "")), "Invalid evidence bundle identifier.")
    facts = export_pair(audit_bytes, journal_bytes)
    require(all(payload.get(k) == v for k, v in facts.items()), "The signed snapshot differs from the exported records.")
    require(payload.get("files") == {"audit.json": sha256(audit_bytes), "journal.json": sha256(journal_bytes)}, "Export files differ from their signed digests.")
    return {"verified": True, "signature_valid": True, "bundle_id": payload["bundle_id"], "stage": stage,
            **facts, "key_arn": trust["key_arn"], "public_key_sha256": trust["public_key_sha256"], "source_authenticated": False}


def bundle_files(directory):
    directory = Path(directory).resolve(strict=True)
    files = {}
    for name in (*FILES, "seal.json"):
        path = directory / name
        require(path.is_file() and not path.is_symlink() and path.resolve().parent == directory, "An evidence file is missing or points outside its bundle.")
        files[name] = path.read_bytes()
    return files


def verify_bundle(directory, trust_path, stage, institution_id):
    trust = strict_json(Path(trust_path).read_bytes())
    if Path(directory).is_file():
        packet = strict_json(Path(directory).read_bytes())
        require(isinstance(packet, dict) and packet.get("schema") == "agentu.evidence.archive.v1", "Unsupported downloaded archive packet.")
        return verify_bytes(packet["seal"], base64.b64decode(packet["audit_base64"], validate=True), base64.b64decode(packet["journal_base64"], validate=True), trust, stage, institution_id)
    files = bundle_files(directory)
    return verify_bytes(strict_json(files["seal.json"]), files["audit.json"], files["journal.json"], trust, stage, institution_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--trust", type=Path, required=True, help="Public-key record obtained separately through a trusted source")
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--institution", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_bundle(args.bundle, args.trust, args.stage, args.institution)))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit("Evidence verification failed: " + str(error))
