"""Seal verified export snapshots with KMS and archive exact retained versions.

Private signing keys never leave KMS. A seal authenticates an operator-supplied
snapshot; it does not authenticate a bank or make every database write immutable.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
import uuid
from botocore.exceptions import ClientError
from deploy import clients, outputs
from environments import EXPECTED_ACCOUNT, REGION, STAGES, environment
from environment_check import validate_outputs
from verify_evidence import ALGORITHM, canonical, sha256, strict_json, require, export_pair, validate_trust, verify_bytes, bundle_files


def write_new(path, value):
    with Path(path).open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def target(session, stage):
    stack, values = outputs(session, stage)
    validate_outputs(stage, stack, values)
    key = values.get("EvidenceKeyArn", "")
    require(re.fullmatch(f"arn:aws:kms:{REGION}:{EXPECTED_ACCOUNT}:key/[a-f0-9-]{{36}}", key), "Apply the evidence infrastructure before sealing exports.")
    bucket = f"agentu-{stage}-evidence-{EXPECTED_ACCOUNT}-{REGION}"
    require(values.get("EvidenceBucket") == bucket, "The evidence archive belongs to another environment.")
    return key, bucket


def trust_record(session, stage):
    key, _ = target(session, stage)
    kms = session.client("kms")
    metadata = kms.describe_key(KeyId=key)["KeyMetadata"]
    require(metadata.get("Arn") == key and metadata.get("AWSAccountId") == EXPECTED_ACCOUNT and metadata.get("KeyState") == "Enabled" and metadata.get("Enabled") is True,
            "The evidence key is not active in the business account.")
    require(metadata.get("KeyUsage") == "SIGN_VERIFY" and metadata.get("KeySpec") == "RSA_3072" and metadata.get("Origin") == "AWS_KMS", "Use the non-exportable RSA-3072 signing key configured for this environment.")
    public = kms.get_public_key(KeyId=key)
    require(public.get("KeyId") == key and public.get("KeyUsage") == "SIGN_VERIFY" and public.get("KeySpec") == "RSA_3072" and ALGORITHM in public.get("SigningAlgorithms", []), "The public key or signing algorithm does not match.")
    result = {"schema": "agentu.evidence.trust.v1", "account": EXPECTED_ACCOUNT, "region": REGION, "stage": stage,
              "key_arn": key, "algorithm": ALGORITHM, "public_key_spki_base64": base64.b64encode(public["PublicKey"]).decode(), "public_key_sha256": sha256(public["PublicKey"])}
    validate_trust(result, stage)
    return result


def seal_exports(session, stage, audit_path, journal_path, trust_path, destination):
    environment(stage)
    audit_bytes, journal_bytes = Path(audit_path).read_bytes(), Path(journal_path).read_bytes()
    facts = export_pair(audit_bytes, journal_bytes)
    trusted = strict_json(Path(trust_path).read_bytes())
    validate_trust(trusted, stage)
    directory = Path(destination).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    # Validate against the live business key; never silently replace a trust pin.
    current = trust_record(session, stage)
    require(current == trusted, "The current environment key differs from the separately trusted record. Review key rotation before sealing.")
    payload = {"schema": "agentu.evidence.snapshot.v1", "bundle_id": str(uuid.uuid4()),
               "account": EXPECTED_ACCOUNT, "region": REGION, "stage": stage, **facts,
               "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "source": "operator_supplied_exports", "source_authenticated": False,
               "files": {"audit.json": sha256(audit_bytes), "journal.json": sha256(journal_bytes)},
               "signing": {k: trusted[k] for k in ("key_arn", "algorithm", "public_key_sha256")}}
    message = canonical(payload)
    require(len(message) <= 4096, "The snapshot manifest exceeds the KMS raw-message limit.")
    signed = session.client("kms").sign(KeyId=trusted["key_arn"], Message=message, MessageType="RAW", SigningAlgorithm=ALGORITHM)
    require(signed.get("KeyId") == trusted["key_arn"] and signed.get("SigningAlgorithm") == ALGORITHM, "KMS returned a different key or signing algorithm.")
    seal = {"schema": "agentu.evidence.seal.v1", "message_base64": base64.b64encode(message).decode(), "signature_base64": base64.b64encode(signed["Signature"]).decode()}
    verified = verify_bytes(seal, audit_bytes, journal_bytes, trusted, stage, facts["institution_id"])
    # A failed signing/verification attempt never creates a completed seal.
    for name, body in (("audit.json", audit_bytes), ("journal.json", journal_bytes)):
        with (directory / name).open("xb") as stream:
            stream.write(body)
    write_new(directory / "seal.json", seal)
    return verified


def archive_bundle(session, stage, directory, trust_path, institution_id):
    directory = Path(directory).resolve(strict=True)
    files = bundle_files(directory)
    trust = strict_json(Path(trust_path).read_bytes())
    seal = strict_json(files["seal.json"])
    verified = verify_bytes(seal, files["audit.json"], files["journal.json"], trust, stage, institution_id)
    _, bucket = target(session, stage)
    s3 = session.client("s3")
    owner = {"Bucket": bucket, "ExpectedBucketOwner": EXPECTED_ACCOUNT}
    require(s3.get_bucket_versioning(**owner).get("Status") == "Enabled", "The evidence bucket must have versioning enabled.")
    public = s3.get_public_access_block(**owner)["PublicAccessBlockConfiguration"]
    require(all(public.get(k) is True for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")), "The evidence bucket must remain private.")
    lock = s3.get_object_lock_configuration(**owner)["ObjectLockConfiguration"]
    require(lock.get("ObjectLockEnabled") == "Enabled" and lock.get("Rule", {}).get("DefaultRetention") == {"Mode": "GOVERNANCE", "Days": 30}, "Apply the reviewed 30-day governance archive configuration before uploading.")
    key = f"sealed/{institution_id}/{verified['as_of_sequence']:020d}/{verified['bundle_id']}.json"
    packet = canonical({"schema": "agentu.evidence.archive.v1", "seal": seal,
                        "audit_base64": base64.b64encode(files["audit.json"]).decode(), "journal_base64": base64.b64encode(files["journal.json"]).decode()})
    checksum = base64.b64encode(bytes.fromhex(sha256(packet))).decode()
    try:
        saved = s3.put_object(**owner, Key=key, Body=packet, ContentType="application/json", ServerSideEncryption="AES256",
                              ChecksumAlgorithm="SHA256", ChecksumSHA256=checksum, IfNoneMatch="*")
        version = saved.get("VersionId")
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("PreconditionFailed", "412"):
            raise
        # An uncertain previous upload can be verified with the same bundle.
        version = s3.head_object(**owner, Key=key).get("VersionId")
    require(isinstance(version, str) and version not in ("", "null"), "The archive did not return an exact retained object version.")
    archived = s3.get_object(**owner, Key=key, VersionId=version, ChecksumMode="ENABLED")
    stream = archived["Body"]
    try:
        require(archived.get("VersionId") == version and archived.get("ContentLength") == len(packet), "The retained archive version has different contents.")
        require(stream.read(len(packet) + 1) == packet and archived.get("ChecksumSHA256") == checksum, "The archived version does not match the verified bundle bytes.")
    finally:
        stream.close()
    retention = s3.get_object_retention(**owner, Key=key, VersionId=version)["Retention"]
    until = retention.get("RetainUntilDate")
    require(retention.get("Mode") == "GOVERNANCE" and isinstance(until, datetime) and until.tzinfo is not None,
            "The archived version has no confirmed governance retention.")
    require(until >= archived["LastModified"] + timedelta(days=30, seconds=-1) and until > datetime.now(timezone.utc), "The required archive retention is missing or has expired.")
    receipt = {"schema": "agentu.evidence.archive-receipt.v1", "account": EXPECTED_ACCOUNT, "region": REGION, "stage": stage,
               "bucket": bucket, "key": key, "version_id": version, "packet_sha256": sha256(packet),
               "retention_mode": "GOVERNANCE", "retain_until": until.isoformat(), "bundle_id": verified["bundle_id"], "source_authenticated": False}
    receipt_path = directory / "archive-receipt.json"
    if receipt_path.exists():
        require(strict_json(receipt_path.read_bytes()) == receipt, "The archive receipt refers to different content or retention. Preserve it and investigate.")
    else:
        write_new(receipt_path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("trust", "seal", "archive"))
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--profile")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--trust", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--institution")
    args = parser.parse_args()
    needed = {"trust": ("out",), "seal": ("out", "audit", "journal", "trust"), "archive": ("bundle", "trust", "institution")}[args.command]
    if any(getattr(args, field) is None for field in needed):
        parser.error("Required for this operation: " + ", ".join("--" + field for field in needed))
    session = clients(args.profile, REGION)
    if args.command == "trust":
        result = trust_record(session, args.stage)
        write_new(args.out, result)
    elif args.command == "seal":
        result = seal_exports(session, args.stage, args.audit, args.journal, args.trust, args.out)
    else:
        result = archive_bundle(session, args.stage, args.bundle, args.trust, args.institution)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, ClientError) as error:
        raise SystemExit("Evidence operation stopped: " + str(error))
