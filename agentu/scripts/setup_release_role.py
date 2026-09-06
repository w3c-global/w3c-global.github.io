"""Set up a narrowly scoped GitHub OIDC role AFTER the AWS stack exists.

Without --apply this only prints the exact role and policies for review.
No passwords or long-lived AWS access keys are stored in GitHub.
"""
import argparse
import json
from botocore.exceptions import ClientError
from deploy import clients, outputs, EXPECTED_ACCOUNT, REGION

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["sandbox", "demo"], required=True)
    parser.add_argument("--profile")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    session = clients(args.profile, REGION)
    stack, values = outputs(session, args.stage)
    provider = f"arn:aws:iam::{EXPECTED_ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
    name = f"agentu-{args.stage}-github-release"
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Federated": provider}, "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {"StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com", "token.actions.githubusercontent.com:sub": f"repo:w3c-global/w3c-global.github.io:environment:agentu-{args.stage}"}}}]}
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "cloudformation:DescribeStacks", "Resource": stack["StackId"]},
        {"Effect": "Allow", "Action": ["lambda:GetFunctionConfiguration", "lambda:UpdateFunctionCode"], "Resource": f"arn:aws:lambda:{REGION}:{EXPECTED_ACCOUNT}:function:{values['FunctionName']}"},
        {"Effect": "Allow", "Action": "s3:PutObject", "Resource": f"arn:aws:s3:::{values['WebBucket']}/agentu/*"},
        {"Effect": "Allow", "Action": "cloudfront:CreateInvalidation", "Resource": f"arn:aws:cloudfront::{EXPECTED_ACCOUNT}:distribution/{values['DistributionId']}"}]}
    print(json.dumps({"role": name, "trust": trust, "permissions": policy}, indent=2))
    if args.apply:
        iam = session.client("iam")
        try:
            iam.get_open_id_connect_provider(OpenIDConnectProviderArn=provider)
        except iam.exceptions.NoSuchEntityException:
            iam.create_open_id_connect_provider(Url="https://token.actions.githubusercontent.com", ClientIDList=["sts.amazonaws.com"], Tags=[{"Key": "Project", "Value": "Agentu"}])
        try:
            iam.get_role(RoleName=name)
            iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
        except iam.exceptions.NoSuchEntityException:
            iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Description="Release tested Agentu demo code only", MaxSessionDuration=3600, Tags=[{"Key": "Project", "Value": "Agentu"}])
        iam.put_role_policy(RoleName=name, PolicyName="AgentuReleaseOnly", PolicyDocument=json.dumps(policy))
        print(f"AWS_RELEASE_ROLE_ARN=arn:aws:iam::{EXPECTED_ACCOUNT}:role/{name}")
