"""Set up a narrowly scoped GitHub OIDC role AFTER the AWS stack exists.

Without --apply this only prints the exact role and policies for review.
No passwords or long-lived AWS access keys are stored in GitHub.
"""
import argparse
import json
from deploy import clients, outputs, EXPECTED_ACCOUNT, REGION
from environments import STAGES
from environment_check import validate_outputs, verify_environment


def role_documents(stage, stack, values):
    validate_outputs(stage, stack, values)
    provider = f"arn:aws:iam::{EXPECTED_ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
    name = f"agentu-{stage}-github-release"
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Federated": provider}, "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {"StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com", "token.actions.githubusercontent.com:sub": f"repo:w3c-global/w3c-global.github.io:environment:agentu-{stage}"}}}]}
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "cloudformation:DescribeStacks", "Resource": stack["StackId"]},
        {"Effect": "Allow", "Action": ["lambda:GetFunctionConfiguration", "lambda:UpdateFunctionCode"], "Resource": [f"arn:aws:lambda:{REGION}:{EXPECTED_ACCOUNT}:function:{values[key]}" for key in ("FunctionName", "WorkerFunctionName")]},
        {"Effect": "Allow", "Action": ["cognito-idp:DescribeUserPool", "cognito-idp:DescribeUserPoolClient", "cognito-idp:GetUserPoolMfaConfig"], "Resource": f"arn:aws:cognito-idp:{REGION}:{EXPECTED_ACCOUNT}:userpool/{values['UserPoolId']}"},
        {"Effect": "Allow", "Action": "apigateway:GET", "Resource": [f"arn:aws:apigateway:{REGION}::/apis/{values['ApiId']}" + suffix for suffix in ("", "/routes", "/authorizers", "/integrations")]},
        {"Effect": "Allow", "Action": ["dynamodb:DescribeTable", "dynamodb:DescribeContinuousBackups", "dynamodb:DescribeTimeToLive"], "Resource": [f"arn:aws:dynamodb:{REGION}:{EXPECTED_ACCOUNT}:table/{values[key]}" for key in ("RecordsTable", "PlatformTable")]},
        {"Effect": "Allow", "Action": ["s3:GetBucketPublicAccessBlock", "s3:GetBucketVersioning", "s3:GetEncryptionConfiguration"], "Resource": f"arn:aws:s3:::{values['WebBucket']}"},
        {"Effect": "Allow", "Action": "s3:PutObject", "Resource": f"arn:aws:s3:::{values['WebBucket']}/agentu/*"},
        {"Effect": "Allow", "Action": ["cloudfront:GetDistribution", "cloudfront:CreateInvalidation"], "Resource": f"arn:aws:cloudfront::{EXPECTED_ACCOUNT}:distribution/{values['DistributionId']}"}]}
    return {"role": name, "trust": trust, "permissions": policy}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--profile")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    session = clients(args.profile, REGION)
    verify_environment(session, args.stage)
    stack, values = outputs(session, args.stage)
    documents = role_documents(args.stage, stack, values)
    print(json.dumps(documents, indent=2))
    if args.apply:
        name, trust, policy = documents["role"], documents["trust"], documents["permissions"]
        provider = trust["Statement"][0]["Principal"]["Federated"]
        iam = session.client("iam")
        try:
            iam.get_open_id_connect_provider(OpenIDConnectProviderArn=provider)
        except iam.exceptions.NoSuchEntityException:
            iam.create_open_id_connect_provider(Url="https://token.actions.githubusercontent.com", ClientIDList=["sts.amazonaws.com"], Tags=[{"Key": "Project", "Value": "Agentu"}])
        try:
            iam.get_role(RoleName=name)
            iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
        except iam.exceptions.NoSuchEntityException:
            iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Description="Release tested Agentu code to one verified business environment", MaxSessionDuration=3600, Tags=[{"Key": "Project", "Value": "Agentu"}])
        iam.put_role_policy(RoleName=name, PolicyName="AgentuReleaseOnly", PolicyDocument=json.dumps(policy))
        print(f"AWS_RELEASE_ROLE_ARN=arn:aws:iam::{EXPECTED_ACCOUNT}:role/{name}")
