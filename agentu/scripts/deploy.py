"""Plan/apply only to the user-designated AWS account. Requires boto3.

python agentu/scripts/deploy.py plan --stage demo --profile agentu
python agentu/scripts/deploy.py apply --stage demo --profile agentu --change-set <name>
python agentu/scripts/deploy.py status --stage demo --profile agentu
"""
import argparse
import base64
import json
import mimetypes
import sys
import time
from pathlib import Path
import boto3
from botocore.exceptions import ClientError
from build import build, ROOT, OUT

EXPECTED_ACCOUNT = "032312375271"
REGION = "eu-west-2"


def clients(profile, region):
    session = boto3.Session(profile_name=profile, region_name=region)
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != EXPECTED_ACCOUNT:
        raise SystemExit(f"Refusing deployment: account {identity['Account']} is not the designated Agentu account {EXPECTED_ACCOUNT}.")
    if identity["Arn"].endswith(":root"):
        raise SystemExit("Use an IAM or federated deployment identity, not AWS root credentials.")
    if region != REGION:
        raise SystemExit(f"This deployment is configured for {REGION} only.")
    print(f"Verified designated Agentu account {EXPECTED_ACCOUNT} in {region}.", flush=True)
    return session


def artifact_bucket(session):
    s3 = session.client("s3")
    name = f"agentu-artifacts-{EXPECTED_ACCOUNT}-{REGION}"
    try:
        s3.head_bucket(Bucket=name, ExpectedBucketOwner=EXPECTED_ACCOUNT)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket", "NotFound"):
            raise
        s3.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": REGION}, ObjectOwnership="BucketOwnerEnforced")
    s3.put_public_access_block(Bucket=name, PublicAccessBlockConfiguration={"BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True}, ExpectedBucketOwner=EXPECTED_ACCOUNT)
    s3.put_bucket_encryption(Bucket=name, ServerSideEncryptionConfiguration={"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}, ExpectedBucketOwner=EXPECTED_ACCOUNT)
    s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Enabled"}, ExpectedBucketOwner=EXPECTED_ACCOUNT)
    s3.put_bucket_tagging(Bucket=name, Tagging={"TagSet": [{"Key": "Project", "Value": "Agentu"}, {"Key": "Purpose", "Value": "deployment-artifacts"}]}, ExpectedBucketOwner=EXPECTED_ACCOUNT)
    s3.put_bucket_policy(Bucket=name, ExpectedBucketOwner=EXPECTED_ACCOUNT, Policy=json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": [f"arn:aws:s3:::{name}", f"arn:aws:s3:::{name}/*"], "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}))
    return name


def model_parameter(model_arn, existing_parameters, updating):
    if model_arn is not None:
        return {"ParameterKey": "BedrockModelArn", "ParameterValue": model_arn}
    if updating and any(p["ParameterKey"] == "BedrockModelArn" for p in existing_parameters):
        return {"ParameterKey": "BedrockModelArn", "UsePreviousValue": True}
    return {"ParameterKey": "BedrockModelArn", "ParameterValue": ""}


def plan(session, stage, model_arn=None):
    manifest = build()
    bucket = artifact_bucket(session)
    key = f"{stage}/lambda/{manifest['lambda_sha256']}.zip"
    s3 = session.client("s3")
    s3.upload_file(str(OUT / "lambda.zip"), bucket, key, ExtraArgs={"ServerSideEncryption": "AES256", "ExpectedBucketOwner": EXPECTED_ACCOUNT})
    cf = session.client("cloudformation")
    stack = "agentu-" + stage
    kind = "CREATE"
    existing_parameters = []
    try:
        existing = cf.describe_stacks(StackName=stack)["Stacks"][0]
        status = existing["StackStatus"]
        existing_parameters = existing.get("Parameters", [])
        kind = "CREATE" if status == "REVIEW_IN_PROGRESS" else "UPDATE"
    except ClientError as exc:
        if "does not exist" not in str(exc):
            raise
    name = "agentu-" + time.strftime("%Y%m%d-%H%M%S")
    cf.validate_template(TemplateBody=(OUT / "template.json").read_text())
    cf.create_change_set(StackName=stack, ChangeSetName=name, ChangeSetType=kind,
        Description="Agentu governed operations and agent runtime; simulated funds only",
        TemplateBody=(OUT / "template.json").read_text(), Capabilities=["CAPABILITY_NAMED_IAM"],
        Parameters=[{"ParameterKey": "Stage", "ParameterValue": stage}, {"ParameterKey": "ArtifactBucket", "ParameterValue": bucket}, {"ParameterKey": "ArtifactKey", "ParameterValue": key}, model_parameter(model_arn, existing_parameters, kind == "UPDATE")],
        Tags=[{"Key": "Project", "Value": "Agentu"}, {"Key": "Environment", "Value": stage}, {"Key": "Owner", "Value": "W3C"}])
    # Keep this bounded so callers can continue preparing the walkthrough while AWS works.
    print(json.dumps({"stack": stack, "change_set": name, "type": kind, "next": "inspect status then apply this named change set"}), flush=True)
    return name


def outputs(session, stage):
    stack = session.client("cloudformation").describe_stacks(StackName="agentu-" + stage)["Stacks"][0]
    return stack, {x["OutputKey"]: x["OutputValue"] for x in stack.get("Outputs", [])}


def publish(session, stage):
    stack, values = outputs(session, stage)
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise SystemExit("The AWS stack must finish successfully before uploading the website.")
    manifest = build()
    if "WorkerFunctionName" not in values:
        raise SystemExit("Apply the infrastructure change set containing the worker before publishing.")
    for key in ("WorkerFunctionName", "FunctionName"):
        configuration = session.client("lambda").get_function_configuration(FunctionName=values[key])
        deployed_hash = base64.b64decode(configuration["CodeSha256"]).hex()
        if deployed_hash != manifest["lambda_sha256"] or configuration.get("LastUpdateStatus") != "Successful":
            raise SystemExit("Backend code differs from this build or is still updating. Release the matching API and worker before publishing.")
    config = {"mode": "hosted", "environment": "Founder demo" if stage == "demo" else "Sandbox",
              "apiBase": "", "clientId": values["UserPoolClientId"], "authDomain": values["AuthDomain"],
              "redirectUri": values["DemoUrl"], "logoutUri": values["WebsiteUrl"]}
    (OUT / "static" / "agentu" / "demo" / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (OUT / "static" / "agentu" / "app" / "config.json").write_text(json.dumps({**config, "redirectUri": values["AppUrl"]}), encoding="utf-8")
    s3 = session.client("s3")
    # Upload only the allowlist produced by the current build. No recursive sync of the repository.
    for key in manifest["static_files"]:
        path = OUT / "static" / key
        content_type = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".json": "application/json", ".svg": "image/svg+xml"}.get(path.suffix, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        cache = "no-store" if key.endswith("config.json") else "no-cache" if path.suffix == ".html" else "public,max-age=300"
        s3.upload_file(str(path), values["WebBucket"], key, ExtraArgs={"ContentType": content_type, "CacheControl": cache, "ServerSideEncryption": "AES256", "ExpectedBucketOwner": EXPECTED_ACCOUNT})
    invalidation = session.client("cloudfront").create_invalidation(DistributionId=values["DistributionId"], InvalidationBatch={"Paths": {"Quantity": 1, "Items": ["/agentu/*"]}, "CallerReference": str(time.time_ns())})
    # Only public configuration and resource identifiers are recorded locally.
    (OUT / f"{stage}-outputs.json").write_text(json.dumps(values, indent=2), encoding="utf-8")
    print(json.dumps({"published": values["WebsiteUrl"], "demo": values["DemoUrl"], "invalidation": invalidation["Invalidation"]["Id"]}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["identity", "plan", "apply", "status", "publish"])
    parser.add_argument("--stage", choices=["sandbox", "demo"], required=True)
    parser.add_argument("--profile")
    parser.add_argument("--region", default=REGION)
    parser.add_argument("--change-set")
    parser.add_argument("--bedrock-model-arn", help="Optional approved London foundation model ARN. Omit to preserve the existing choice.")
    args = parser.parse_args()
    session = clients(args.profile, args.region)
    cf = session.client("cloudformation")
    if args.command == "identity":
        return
    if args.command == "plan":
        plan(session, args.stage, args.bedrock_model_arn)
    elif args.command == "apply":
        if not args.change_set:
            raise SystemExit("Specify the reviewed --change-set name.")
        change = cf.describe_change_set(StackName="agentu-" + args.stage, ChangeSetName=args.change_set)
        if change["Status"] != "CREATE_COMPLETE" or change["ExecutionStatus"] != "AVAILABLE":
            raise SystemExit("The named change set is not ready to execute.")
        # Refuse changes outside this stack's generated resource set.
        from build import template
        allowed = set(template()["Resources"])
        if any(c["ResourceChange"]["LogicalResourceId"] not in allowed for c in change.get("Changes", [])):
            raise SystemExit("Change set contains a resource outside the Agentu template.")
        cf.execute_change_set(StackName="agentu-" + args.stage, ChangeSetName=args.change_set)
        print("Deployment started. Use status to observe completion.", flush=True)
    elif args.command == "status":
        if args.change_set:
            change = cf.describe_change_set(StackName="agentu-" + args.stage, ChangeSetName=args.change_set)
            print(json.dumps({k: change.get(k) for k in ("Status", "StatusReason", "ExecutionStatus", "Changes")}, default=str, indent=2))
        else:
            stack, values = outputs(session, args.stage)
            print(json.dumps({"status": stack["StackStatus"], "outputs": values}, indent=2))
            if "FAILED" in stack["StackStatus"] or "ROLLBACK" in stack["StackStatus"]:
                events = cf.describe_stack_events(StackName="agentu-" + args.stage)["StackEvents"]
                print(json.dumps([{k: e.get(k) for k in ("LogicalResourceId", "ResourceStatus", "ResourceStatusReason")} for e in events if "FAILED" in e["ResourceStatus"]], indent=2))
    else:
        publish(session, args.stage)


if __name__ == "__main__":
    main()
