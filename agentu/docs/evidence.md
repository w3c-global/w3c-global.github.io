# Signed evidence snapshots

The evidence tooling seals a complete audit export and its matching journal export. It verifies their record chains and accounting first, binds their exact file digests and snapshot identity into a small manifest, and asks the environment's KMS key to sign that manifest. Anyone with the files and a separately trusted public-key record can check the signature and records offline.

This is an **operator-sealed snapshot**. The signed message explicitly identifies operator-supplied exports and `source_authenticated=false`. It does not authenticate a bank, attest that the records came from the deployed API, prove that a supplied snapshot is the latest one, or automatically seal every database write. The preparation time is supplied by the operator's computer, not a trusted timestamp authority. Automatic checkpoint delivery and hosted verification remain outstanding.

## Signing and archive infrastructure

Each environment's template adds a retained RSA-3072 `SIGN_VERIFY` KMS key, a private versioned evidence bucket and an unattached `EvidenceOperatorPolicy`. Application, worker and GitHub release roles receive no signing permission or archive access. Assign the operator policy to a designated business identity; review its other inherited permissions as part of that assignment. No identity has been assigned or resource provisioned yet.

The operator policy can describe its own stack, inspect its public key, sign with `RSASSA_PSS_SHA_256` and write/read its archive prefix. It grants no database access, archive deletion or governance-retention bypass. Key administration remains under the business account's IAM delegation. Retain the public-key trust records when rotating or retiring keys; offline verification needs the key that signed each snapshot.

The archive uses **30-day S3 Object Lock governance retention**. Objects remain stored after that period; the template has no automatic archive-deletion lifecycle. Governance retention can be bypassed by a sufficiently privileged administrator and is not a regulatory retention determination. The deployment retains the bucket and key on stack deletion or replacement. See [AWS Object Lock behavior](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html).

## Obtain and pin the public key

Use Python with the repository tooling requirements and Node.js 22 or later. After deploying the evidence resources, run with a business profile in account `032312375271`, London:

```text
python agentu/scripts/evidence.py trust --stage sandbox --profile agentu --out .build/trusted-sandbox-key.json
```

This reads the stack and KMS configuration and creates a new public-key record. It refuses an existing output file. Verify the stage, full key ARN and public-key SHA-256 fingerprint through the authorised business operator before distributing the record. Recipients must obtain it separately from an untrusted bundle. A public key included by a sender is not sufficient evidence of that sender's authority.

The private key is held by KMS. The CLI requests [RSA-PSS with SHA-256 and a 256-bit salt](https://docs.aws.amazon.com/kms/latest/developerguide/symm-asymm-choose-key-spec.html), and the independent verifier uses Node's standard cryptography module with the corresponding parameters. No handwritten cryptographic algorithm is used. The signer refuses a key that differs from the supplied trust record; rotation requires a reviewed replacement pin.

## Seal and verify matching exports

Export the complete audit and journal for the same institution and evidence head. If activity changes between exports, obtain matching exports again. Keep the files in private business storage because audit records can contain institution and user information.

```text
python agentu/scripts/evidence.py seal --stage sandbox --profile agentu --audit audit.json --journal journal.json --trust .build/trusted-sandbox-key.json --out .build/sealed-snapshot-01
python agentu/scripts/verify_evidence.py .build/sealed-snapshot-01 --trust .build/trusted-sandbox-key.json --stage sandbox --institution <institution-id>
```

The seal directory must be new. It contains unchanged `audit.json` and `journal.json` bytes plus `seal.json`; no private key or credential is written. Validation rejects mixed institutions/heads, altered or truncated records, unbalanced postings, disagreement between journals and posting audit events, duplicate JSON properties, a wrong trust pin and an invalid signature. Even reformatting a file changes its signed byte digest. Failed signing leaves no completed seal and never overwrites a prior bundle.

The verifier accepts the expected stage and institution explicitly. It checks the signature using only the supplied trust record, then independently checks the records and signed snapshot facts. It needs no AWS session. Offline verification cannot discover key compromise, revocation, later events or a replacement trust decision: maintain those records and compare against independently retained earlier checkpoints.

## Archive and retain the exact version

```text
python agentu/scripts/evidence.py archive --stage sandbox --profile agentu --bundle .build/sealed-snapshot-01 --trust .build/trusted-sandbox-key.json --institution <institution-id>
```

The archive command verifies the bundle and the designated bucket's privacy, versioning and governance configuration before upload. It writes under `sealed/<institution>/<audit-sequence>/<bundle-id>.json` with `If-None-Match: *`, an explicit expected bucket owner and a SHA-256 checksum. Existing content is never silently replaced. An uncertain upload can be retried with the same sealed directory; an existing object must match exactly.

Before recording success, the command reads back the returned **version ID**, confirms the full bytes/checksum, and verifies that version's retention against its server-reported modification time. `archive-receipt.json` records bucket, key, exact version, packet digest and actual retention expiry. A changed version, missing/expired retention or conflicting receipt stops the operation. Preserve failed attempts and receipts for investigation.

Retrieve that exact version through the authenticated AWS console or S3 API using the receipt's bucket, key and version ID. The downloaded JSON packet can be verified directly:

```text
python agentu/scripts/verify_evidence.py downloaded-version.json --trust .build/trusted-sandbox-key.json --stage sandbox --institution <institution-id>
```

The download's offline verification checks signature and contents. Confirm current retention and the version against AWS separately; a local receipt is not a signed storage-service attestation.

## Verified progress and remaining checks

Automated tests perform real RSA-3072 signing and independent verification with ephemeral fictional keys and records. They exercise changed signatures/files, mismatched snapshots and trust, corrupted journals, failed signing, exact-version reads, uncertain-upload replay, shortened retention, public storage and downloaded archive verification. KMS and S3 calls are mocked in these tests. The current local platform's 45-event audit and five-journal exports also pass the new cross-check; they have not received an AWS signature.

The CloudFormation template passes schema validation. No AWS KMS key, AWS signature, operator assignment or retained AWS object has been created or verified. Deployment, key-fingerprint confirmation, operator separation, a real sign/archive/download drill, compromise/revocation procedures, retention ownership and automatic checkpoint delivery must be completed before treating this as operational evidence assurance.
