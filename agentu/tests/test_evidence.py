"""Real RSA verification with mocked, business-scoped KMS/archive transports."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
for directory in ("infra", "scripts", "backend"):
    sys.path.insert(0, str(ROOT / directory))
from evidence import archive_bundle, seal_exports, trust_record, write_new
from verify_evidence import ALGORITHM, canonical, sha256, strict_json, verify_bundle
from environments import CONTRACT, EXPECTED_ACCOUNT, REGION
from platform_core.model import Actor
from platform_core.service import PlatformService
from platform_core.store import DocumentStore, SQLiteBackend
from template import template

KEY_ARN = f"arn:aws:kms:{REGION}:{EXPECTED_ACCOUNT}:key/11111111-2222-4333-8444-555555555555"
KEYGEN = """import {generateKeyPairSync} from 'node:crypto';
const k=generateKeyPairSync('rsa',{modulusLength:3072});
process.stdout.write(JSON.stringify({public:k.publicKey.export({type:'spki',format:'der'}).toString('base64'),private:k.privateKey.export({type:'pkcs8',format:'pem'})}));"""
SIGN = """import {constants,sign} from 'node:crypto';
let raw='';for await(const p of process.stdin)raw+=p;
const d=JSON.parse(raw);
process.stdout.write(sign('sha256',Buffer.from(d.message,'base64'),{key:d.private,padding:constants.RSA_PKCS1_PSS_PADDING,saltLength:32}).toString('base64'));"""


class Archive:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.retention_days = 30
        self.mode = "GOVERNANCE"
        self.private = True

    def get_bucket_versioning(self, **args):
        return {"Status": "Enabled"}

    def get_public_access_block(self, **args):
        return {"PublicAccessBlockConfiguration": {k: self.private for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")}}

    def get_object_lock_configuration(self, **args):
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": {"Mode": self.mode, "Days": 30}}}}

    def put_object(self, **args):
        self.calls.append(("put", args))
        if args["Key"] in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[args["Key"]] = {"body": args["Body"], "hash": args["ChecksumSHA256"], "modified": datetime.now(timezone.utc).replace(microsecond=0)}
        return {"VersionId": "retained-version-1"}

    def head_object(self, **args):
        self.calls.append(("head", args))
        return {"VersionId": "retained-version-1"}

    def get_object(self, **args):
        self.calls.append(("get", args))
        record = self.objects[args["Key"]]
        return {"VersionId": "retained-version-1", "Body": io.BytesIO(record["body"]), "ContentLength": len(record["body"]),
                "ChecksumSHA256": record["hash"], "LastModified": record["modified"]}

    def get_object_retention(self, **args):
        self.calls.append(("retention", args))
        return {"Retention": {"Mode": self.mode, "RetainUntilDate": self.objects[args["Key"]]["modified"] + timedelta(days=self.retention_days)}}


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ephemeral fixture key only; no AWS credentials or private key files.
        cls.keys = json.loads(subprocess.run(["node", "--input-type=module", "-e", KEYGEN], capture_output=True, text=True, check=True, timeout=30).stdout)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = PlatformService(DocumentStore(SQLiteBackend(self.root / "source.sqlite3")))
        self.owner = Actor("seal-owner", "owner@evidence.example.test", True)
        self.tenant = self.service.create_institution(self.owner, {"name": "Evidence verification", "currency": "GBP"}, "create-evidence-institution")["institution"]["id"]
        account = self.service.command(self.tenant, self.owner, "account_create", {"name": "Operating", "currency": "GBP"}, "create-evidence-account")["account"]["id"]
        self.service.command(self.tenant, self.owner, "sandbox_fund", {"account_id": account, "amount": 250_000_000, "reason": "Fictional evidence verification capital"}, "fund-evidence-account")
        self.paths = {}
        meta = self.service.overview(self.tenant, self.owner)["institution"]
        for collection in ("audit", "journal"):
            document = {"schema": "agentu.platform.export.v1", "collection": collection, "institution_id": self.tenant, "mode": "sandbox",
                "as_of_sequence": meta["event_sequence"], "as_of_head": meta["event_head"], "journal_sequence": meta["ledger_sequence"],
                "items": self.service.collection(self.tenant, self.owner, collection)["items"]}
            self.paths[collection] = self.root / (collection + "-input.json")
            write_new(self.paths[collection], document)
        self.values = {"Environment": "sandbox", "DeploymentContract": CONTRACT, "FunctionName": "agentu-sandbox-api", "WorkerFunctionName": "agentu-sandbox-worker",
            "RecordsTable": "agentu-sandbox-records", "PlatformTable": "agentu-sandbox-platform", "WebBucket": f"agentu-sandbox-web-{EXPECTED_ACCOUNT}-{REGION}",
            "WebsiteUrl": "https://dexample.cloudfront.net/agentu/", "AppUrl": "https://dexample.cloudfront.net/agentu/app/", "DemoUrl": "https://dexample.cloudfront.net/agentu/demo/",
            "AuthDomain": f"https://agentu-sandbox-{EXPECTED_ACCOUNT}.auth.{REGION}.amazoncognito.com", "UserPoolId": REGION + "_Example", "UserPoolClientId": "client123",
            "ApiId": "api123", "DistributionId": "E123EXAMPLE", "EvidenceKeyArn": KEY_ARN, "EvidenceBucket": f"agentu-sandbox-evidence-{EXPECTED_ACCOUNT}-{REGION}"}
        self.stack = {"StackName": "agentu-sandbox", "StackId": f"arn:aws:cloudformation:{REGION}:{EXPECTED_ACCOUNT}:stack/agentu-sandbox/123",
            "StackStatus": "CREATE_COMPLETE", "Parameters": [{"ParameterKey": "Stage", "ParameterValue": "sandbox"}], "Tags": [{"Key": "Project", "Value": "Agentu"}, {"Key": "Environment", "Value": "sandbox"}],
            "Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in self.values.items()]}
        self.kms = Mock()
        self.kms.describe_key.return_value = {"KeyMetadata": {"Arn": KEY_ARN, "AWSAccountId": EXPECTED_ACCOUNT, "KeyState": "Enabled", "Enabled": True, "KeyUsage": "SIGN_VERIFY", "KeySpec": "RSA_3072", "Origin": "AWS_KMS"}}
        self.kms.get_public_key.return_value = {"KeyId": KEY_ARN, "KeyUsage": "SIGN_VERIFY", "KeySpec": "RSA_3072", "SigningAlgorithms": [ALGORITHM], "PublicKey": base64.b64decode(self.keys["public"])}
        self.kms.sign.side_effect = self.sign
        self.cf = Mock(); self.cf.describe_stacks.return_value = {"Stacks": [self.stack]}
        self.archive = Archive()
        self.session = Mock(); self.session.client.side_effect = {"cloudformation": self.cf, "kms": self.kms, "s3": self.archive}.__getitem__
        self.trust = self.root / "trusted-business-key.json"
        write_new(self.trust, trust_record(self.session, "sandbox"))
        self.bundle = self.root / "sealed"

    def sign(self, **args):
        raw = subprocess.run(["node", "--input-type=module", "-e", SIGN], input=json.dumps({"private": self.keys["private"], "message": base64.b64encode(args["Message"]).decode()}), capture_output=True, text=True, check=True, timeout=15).stdout
        return {"KeyId": KEY_ARN, "SigningAlgorithm": ALGORITHM, "Signature": base64.b64decode(raw)}

    def seal(self):
        return seal_exports(self.session, "sandbox", self.paths["audit"], self.paths["journal"], self.trust, self.bundle)

    def test_real_rsa_signature_and_record_pair_verify_without_aws(self):
        sealed = self.seal()
        self.assertEqual(verified := verify_bundle(self.bundle, self.trust, "sandbox", self.tenant), sealed)
        self.assertTrue(verified["signature_valid"])
        self.assertFalse(verified["source_authenticated"])
        args = self.kms.sign.call_args.kwargs
        self.assertEqual(("RAW", ALGORITHM), (args["MessageType"], args["SigningAlgorithm"]))
        self.assertLessEqual(len(args["Message"]), 4096)
        self.assertNotIn(self.keys["private"], "".join(p.read_text() for p in self.bundle.iterdir()))

    def test_modified_signature_file_bytes_or_missing_records_are_rejected(self):
        self.seal()
        path = self.bundle / "seal.json"; original = path.read_bytes()
        seal = strict_json(original)
        raw = bytearray(base64.b64decode(seal["signature_base64"])); raw[-1] ^= 1
        seal["signature_base64"] = base64.b64encode(raw).decode(); path.write_bytes(canonical(seal))
        with self.assertRaisesRegex(ValueError, "Signature"):
            verify_bundle(self.bundle, self.trust, "sandbox", self.tenant)
        path.write_bytes(original)
        audit = self.bundle / "audit.json"; original = audit.read_bytes()
        audit.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "signed digests"):
            verify_bundle(self.bundle, self.trust, "sandbox", self.tenant)
        doc = strict_json(original); doc["items"].pop(); audit.write_bytes(canonical(doc))
        with self.assertRaises(ValueError):
            verify_bundle(self.bundle, self.trust, "sandbox", self.tenant)

    def test_snapshot_or_institution_mismatch_never_reaches_signing(self):
        path = self.paths["journal"]; original = strict_json(path.read_bytes())
        for change in ({"institution_id": "other-institution"}, {"as_of_head": "f" * 64}):
            path.write_bytes(canonical({**original, **change}))
            with self.assertRaisesRegex(ValueError, "one institution"):
                self.seal()
        self.kms.sign.assert_not_called()
        self.assertFalse(self.bundle.exists())

    def test_coherent_balanced_journal_change_disagrees_with_audit(self):
        path = self.paths["journal"]; document = strict_json(path.read_bytes())
        for posting in document["items"][0]["postings"]:
            posting["debit" if posting["debit"] else "credit"] += 100
        path.write_bytes(canonical(document))
        with self.assertRaisesRegex(ValueError, "audit evidence"):
            self.seal()
        self.kms.sign.assert_not_called()

    def test_wrong_trust_environment_or_institution_is_rejected(self):
        self.seal()
        for stage, institution in (("production", self.tenant), ("sandbox", "someone-else")):
            with self.assertRaises(ValueError):
                verify_bundle(self.bundle, self.trust, stage, institution)
        trusted = strict_json(self.trust.read_bytes()); trusted["key_arn"] = KEY_ARN.replace("555555555555", "666666666666")
        alternate = self.root / "different-key-label.json"; write_new(alternate, trusted)
        with self.assertRaisesRegex(ValueError, "signed key identity"):
            verify_bundle(self.bundle, alternate, "sandbox", self.tenant)

    def test_foreign_key_never_completes_a_seal(self):
        self.kms.describe_key.return_value["KeyMetadata"]["AWSAccountId"] = "111111111111"
        with self.assertRaisesRegex(ValueError, "business account"):
            self.seal()
        self.kms.sign.assert_not_called()
        self.assertFalse((self.bundle / "seal.json").exists())

    def test_bad_kms_signature_or_untrusted_public_key_is_rejected(self):
        self.kms.sign.side_effect = None
        self.kms.sign.return_value = {"KeyId": KEY_ARN, "SigningAlgorithm": ALGORITHM, "Signature": b"x" * 384}
        with self.assertRaisesRegex(ValueError, "Signature"):
            self.seal()
        self.assertFalse((self.bundle / "seal.json").exists())
        self.bundle = self.root / "second-seal"; self.kms.sign.side_effect = self.sign
        self.seal()
        other = json.loads(subprocess.run(["node", "--input-type=module", "-e", KEYGEN], capture_output=True, text=True, check=True, timeout=30).stdout)
        trusted = strict_json(self.trust.read_bytes())
        trusted.update(public_key_spki_base64=other["public"], public_key_sha256=sha256(base64.b64decode(other["public"])))
        alternate = self.root / "untrusted-other-key.json"; write_new(alternate, trusted)
        with self.assertRaisesRegex(ValueError, "Signature"):
            verify_bundle(self.bundle, alternate, "sandbox", self.tenant)

    def test_existing_bundle_is_not_overwritten(self):
        self.seal(); before = (self.bundle / "seal.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.seal()
        self.assertEqual(before, (self.bundle / "seal.json").read_bytes())
        self.assertEqual(1, self.kms.sign.call_count)

    def test_archive_verifies_exact_version_and_retry_keeps_same_receipt(self):
        self.seal()
        first = archive_bundle(self.session, "sandbox", self.bundle, self.trust, self.tenant)
        second = archive_bundle(self.session, "sandbox", self.bundle, self.trust, self.tenant)
        self.assertEqual(first, second)
        self.assertEqual("retained-version-1", first["version_id"])
        self.assertEqual(1, len(self.archive.objects))
        for operation, args in self.archive.calls:
            self.assertEqual(EXPECTED_ACCOUNT, args["ExpectedBucketOwner"])
            if operation == "put":
                self.assertEqual("*", args["IfNoneMatch"])
            if operation in ("get", "retention"):
                self.assertEqual("retained-version-1", args["VersionId"])

    def test_archive_refuses_wrong_content_short_retention_or_public_storage(self):
        self.seal()
        self.archive.private = False
        with self.assertRaisesRegex(ValueError, "private"):
            archive_bundle(self.session, "sandbox", self.bundle, self.trust, self.tenant)
        self.assertFalse(self.archive.objects)
        self.archive.private = True; self.archive.retention_days = 29
        with self.assertRaisesRegex(ValueError, "retention"):
            archive_bundle(self.session, "sandbox", self.bundle, self.trust, self.tenant)
        self.assertFalse((self.bundle / "archive-receipt.json").exists())
        self.archive.retention_days = 30
        record = next(iter(self.archive.objects.values())); record["body"] += b"altered"
        with self.assertRaisesRegex(ValueError, "different contents"):
            archive_bundle(self.session, "sandbox", self.bundle, self.trust, self.tenant)

    def test_downloaded_archive_verifies_offline_and_rejects_ambiguous_json(self):
        self.seal()
        archive_bundle(self.session, "sandbox", self.bundle, self.trust, self.tenant)
        packet = next(iter(self.archive.objects.values()))["body"]
        downloaded = self.root / "downloaded-version.json"; downloaded.write_bytes(packet)
        verified = verify_bundle(downloaded, self.trust, "sandbox", self.tenant)
        self.assertEqual(verify_bundle(self.bundle, self.trust, "sandbox", self.tenant), verified)
        self.assertFalse(verified["source_authenticated"])
        downloaded.write_bytes(packet.replace(b'"schema":"agentu.evidence.archive.v1"', b'"schema":"other","schema":"agentu.evidence.archive.v1"'))
        with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
            verify_bundle(downloaded, self.trust, "sandbox", self.tenant)

    def test_template_separates_signing_permissions_and_preserves_archive_versions(self):
        r = template()["Resources"]
        self.assertEqual(("RSA_3072", "SIGN_VERIFY"), (r["EvidenceKey"]["Properties"]["KeySpec"], r["EvidenceKey"]["Properties"]["KeyUsage"]))
        for name in ("EvidenceKey", "EvidenceBucket"):
            self.assertEqual("Retain", r[name]["DeletionPolicy"])
        self.assertEqual({"Mode": "GOVERNANCE", "Days": 30}, r["EvidenceBucket"]["Properties"]["ObjectLockConfiguration"]["Rule"]["DefaultRetention"])
        self.assertNotIn("LifecycleConfiguration", r["EvidenceBucket"]["Properties"])
        for name in ("ApiRole", "WorkerRole"):
            self.assertNotIn("kms:Sign", json.dumps(r[name]))
            self.assertNotIn("EvidenceBucket", json.dumps(r[name]))
        policy = json.dumps(r["EvidenceOperatorPolicy"])
        self.assertIn("kms:Sign", policy)
        signing = next(s for s in r["EvidenceOperatorPolicy"]["Properties"]["PolicyDocument"]["Statement"] if s["Action"] == "kms:Sign")
        self.assertEqual(ALGORITHM, signing["Condition"]["StringEquals"]["kms:SigningAlgorithm"])
        for forbidden in ("s3:DeleteObject", "s3:BypassGovernanceRetention", "dynamodb:PutItem", "dynamodb:GetItem"):
            self.assertNotIn(forbidden, policy)


if __name__ == "__main__":
    unittest.main()
