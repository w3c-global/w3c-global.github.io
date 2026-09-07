"""Prevent releases across environment boundaries or with drifted controls."""
import base64
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
for directory in ("infra", "scripts"):
    sys.path.insert(0, str(ROOT / directory))
from environments import CONTRACT, EXPECTED_ACCOUNT, REGION, STAGES, environment
from environment_check import api_items, validate_outputs, verify_environment
from setup_release_role import role_documents
from template import template
import deploy
import release

HASH = "a" * 64


def fixture(stage="staging"):
    values = {"Environment": stage, "DeploymentContract": CONTRACT, "BedrockModelArn": "",
        "FunctionName": f"agentu-{stage}-api", "WorkerFunctionName": f"agentu-{stage}-worker",
        "RecordsTable": f"agentu-{stage}-records", "PlatformTable": f"agentu-{stage}-platform",
        "WebBucket": f"agentu-{stage}-web-{EXPECTED_ACCOUNT}-{REGION}",
        "WebsiteUrl": f"https://d{stage}.cloudfront.net/agentu/", "UserPoolId": f"{REGION}_P{stage}",
        "UserPoolClientId": f"client{stage}", "ApiId": f"api{stage}", "DistributionId": "E123EXAMPLE",
        "AuthDomain": f"https://agentu-{stage}-{EXPECTED_ACCOUNT}.auth.{REGION}.amazoncognito.com"}
    values.update(AppUrl=values["WebsiteUrl"] + "app/", DemoUrl=values["WebsiteUrl"] + "demo/")
    stack = {"StackName": "agentu-" + stage, "StackId": f"arn:aws:cloudformation:{REGION}:{EXPECTED_ACCOUNT}:stack/agentu-{stage}/12345678-abcd-1234-5678-0123456789ab",
        "StackStatus": "CREATE_COMPLETE", "Parameters": [{"ParameterKey": "Stage", "ParameterValue": stage}],
        "Tags": [{"Key": "Project", "Value": "Agentu"}, {"Key": "Environment", "Value": stage}],
        "Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in values.items()]}
    functions = {}
    for kind, role, handler in (("api", "lambda", "handler.handler"), ("worker", "worker", "worker.handler")):
        name = f"agentu-{stage}-{kind}"
        variables = {"STAGE": stage, "PLATFORM_TABLE_NAME": values["PlatformTable"], "BEDROCK_MODEL_ARN": ""}
        if kind == "api":
            variables["TABLE_NAME"] = values["RecordsTable"]
        functions[name] = {"FunctionName": name, "FunctionArn": f"arn:aws:lambda:{REGION}:{EXPECTED_ACCOUNT}:function:{name}",
            "State": "Active", "LastUpdateStatus": "Successful", "Runtime": "python3.13", "Architectures": ["arm64"], "Handler": handler,
            "Role": f"arn:aws:iam::{EXPECTED_ACCOUNT}:role/agentu-{stage}-{role}", "Environment": {"Variables": variables},
            "CodeSha256": base64.b64encode(bytes.fromhex(HASH)).decode(), "RevisionId": "verified-" + kind}
    pool = {"Id": values["UserPoolId"], "Arn": f"arn:aws:cognito-idp:{REGION}:{EXPECTED_ACCOUNT}:userpool/{values['UserPoolId']}",
        "Name": f"agentu-{stage}-presenters", "DeletionProtection": "ACTIVE", "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
        "UserAttributeUpdateSettings": {"AttributesRequireVerificationBeforeUpdate": ["email"]}}
    mfa = {"MfaConfiguration": "ON", "SoftwareTokenMfaConfiguration": {"Enabled": True}}
    client = {"ClientId": values["UserPoolClientId"], "UserPoolId": values["UserPoolId"], "AllowedOAuthFlowsUserPoolClient": True,
        "AllowedOAuthFlows": ["code"], "SupportedIdentityProviders": ["COGNITO"],
        "AllowedOAuthScopes": ["openid", "email", "aws.cognito.signin.user.admin"], "CallbackURLs": [values["AppUrl"], values["DemoUrl"]],
        "LogoutURLs": [values["WebsiteUrl"]], "EnableTokenRevocation": True, "PreventUserExistenceErrors": "ENABLED",
        "AccessTokenValidity": 15, "IdTokenValidity": 15, "RefreshTokenValidity": 1,
        "TokenValidityUnits": {"AccessToken": "minutes", "IdToken": "minutes", "RefreshToken": "days"}}
    tables = {values[key]: {"TableName": values[key], "TableArn": f"arn:aws:dynamodb:{REGION}:{EXPECTED_ACCOUNT}:table/{values[key]}",
        "TableStatus": "ACTIVE", "DeletionProtectionEnabled": True, "SSEDescription": {"Status": "ENABLED"}} for key in ("RecordsTable", "PlatformTable")}
    pitr = {name: {"PointInTimeRecoveryDescription": {"PointInTimeRecoveryStatus": "ENABLED"}} for name in tables}
    ttl = {values["RecordsTable"]: {"TimeToLiveStatus": "ENABLED", "AttributeName": "expires_at"}, values["PlatformTable"]: {"TimeToLiveStatus": "DISABLED"}}
    public = {k: True for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")}
    distribution = {"ARN": f"arn:aws:cloudfront::{EXPECTED_ACCOUNT}:distribution/{values['DistributionId']}", "Status": "Deployed", "DomainName": f"d{stage}.cloudfront.net",
        "DistributionConfig": {"Enabled": True, "Origins": {"Items": [
            {"Id": "website", "DomainName": f"{values['WebBucket']}.s3.{REGION}.amazonaws.com"},
            {"Id": "api", "DomainName": f"{values['ApiId']}.execute-api.{REGION}.amazonaws.com"}]}}}
    authorizer = {"AuthorizerId": "auth123", "Name": "PresenterSignIn", "AuthorizerType": "JWT", "IdentitySource": ["$request.header.Authorization"],
        "JwtConfiguration": {"Audience": [values["UserPoolClientId"]], "Issuer": f"https://cognito-idp.{REGION}.amazonaws.com/{values['UserPoolId']}"}}
    integration = {"IntegrationId": "integration123", "IntegrationType": "AWS_PROXY", "PayloadFormatVersion": "2.0",
        "IntegrationUri": f"arn:aws:lambda:{REGION}:{EXPECTED_ACCOUNT}:function:{values['FunctionName']}"}
    routes = []
    for route in ("GET /api/state", "POST /api/actions", "GET /api/export", "GET /api/health", "ANY /api/platform/{proxy+}", "POST /api/agent/proposals"):
        item = {"RouteKey": route, "Target": "integrations/integration123"}
        if route not in ("GET /api/health", "POST /api/agent/proposals"):
            item.update(AuthorizationType="JWT", AuthorizerId="auth123", AuthorizationScopes=["openid"])
        routes.append(item)
    clients = {name: Mock() for name in ("cloudformation", "lambda", "cognito-idp", "apigatewayv2", "dynamodb", "s3", "cloudfront")}
    clients["cloudformation"].describe_stacks.return_value = {"Stacks": [stack]}
    clients["lambda"].get_function_configuration.side_effect = lambda FunctionName: deepcopy(functions[FunctionName])
    clients["cognito-idp"].describe_user_pool.return_value = {"UserPool": pool}
    clients["cognito-idp"].get_user_pool_mfa_config.return_value = mfa
    clients["cognito-idp"].describe_user_pool_client.return_value = {"UserPoolClient": client}
    clients["apigatewayv2"].get_api.return_value = {"Name": "agentu-" + stage, "ProtocolType": "HTTP"}
    clients["apigatewayv2"].get_authorizers.return_value = {"Items": [authorizer]}
    clients["apigatewayv2"].get_integrations.return_value = {"Items": [integration]}
    clients["apigatewayv2"].get_routes.return_value = {"Items": routes}
    clients["dynamodb"].describe_table.side_effect = lambda TableName: {"Table": tables[TableName]}
    clients["dynamodb"].describe_continuous_backups.side_effect = lambda TableName: {"ContinuousBackupsDescription": pitr[TableName]}
    clients["dynamodb"].describe_time_to_live.side_effect = lambda TableName: {"TimeToLiveDescription": ttl[TableName]}
    clients["s3"].get_public_access_block.return_value = {"PublicAccessBlockConfiguration": public}
    clients["s3"].get_bucket_versioning.return_value = {"Status": "Enabled"}
    clients["s3"].get_bucket_encryption.return_value = {"ServerSideEncryptionConfiguration": {"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}}
    clients["cloudfront"].get_distribution.return_value = {"Distribution": distribution}
    session = Mock()
    session.client.side_effect = clients.__getitem__
    return locals()


class EnvironmentTests(unittest.TestCase):
    def test_checked_aws_response_fields_match_installed_sdk_models(self):
        import boto3
        from botocore.validate import validate_parameters
        f = fixture()
        # Explicit fictional credentials prevent local profiles or metadata lookup.
        sdk = boto3.Session(aws_access_key_id="schema-test-only", aws_secret_access_key="schema-test-only", region_name=REGION)
        cases = [("lambda", "GetFunctionConfiguration", next(iter(f["functions"].values()))),
            ("cognito-idp", "DescribeUserPool", {"UserPool": f["pool"]}),
            ("cognito-idp", "DescribeUserPoolClient", {"UserPoolClient": f["client"]}),
            ("cognito-idp", "GetUserPoolMfaConfig", f["mfa"]),
            ("apigatewayv2", "GetRoutes", {"Items": f["routes"]}),
            ("apigatewayv2", "GetAuthorizers", {"Items": [f["authorizer"]]}),
            ("apigatewayv2", "GetIntegrations", {"Items": [f["integration"]]})]
        for service, operation, response in cases:
            with self.subTest(operation=operation):
                with closing(sdk.client(service)) as client:
                    validate_parameters(response, client.meta.service_model.operation_model(operation).output_shape)

    def test_all_stages_require_mfa_and_protect_data_in_generated_template(self):
        generated = template()
        self.assertEqual(["sandbox", "demo", "staging", "production"], generated["Parameters"]["Stage"]["AllowedValues"])
        for stage in STAGES:
            self.assertEqual(environment(stage)["Label"], generated["Mappings"]["Environments"][stage]["Label"])
            f = fixture(stage)
            self.assertTrue(verify_environment(f["session"], stage, HASH)["configuration_verified"])
        r = generated["Resources"]
        self.assertEqual("ON", r["UserPool"]["Properties"]["MfaConfiguration"])
        self.assertEqual(["SOFTWARE_TOKEN_MFA"], r["UserPool"]["Properties"]["EnabledMfas"])
        for name in ("Records", "PlatformRecords"):
            self.assertTrue(r[name]["Properties"]["DeletionProtectionEnabled"])
            self.assertTrue(r[name]["Properties"]["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"])
            self.assertEqual("Retain", r[name]["DeletionPolicy"])
        self.assertNotIn("TimeToLiveSpecification", r["PlatformRecords"]["Properties"])
        for stage in ("staging", "production"):
            self.assertGreaterEqual(generated["Mappings"]["Environments"][stage]["LogRetentionDays"], 90)

    def test_foreign_account_region_and_unrecognized_stage_stop_before_writes(self):
        with patch("deploy.boto3.Session") as session:
            with self.assertRaises(SystemExit):
                deploy.clients(None, "eu-north-1")
            session.assert_not_called()
        with patch("deploy.build") as build, patch("deploy.artifact_bucket") as bucket:
            with self.assertRaises(ValueError):
                deploy.plan(Mock(), "personal")
            build.assert_not_called(); bucket.assert_not_called()
        for field, value in (("StackId", f"arn:aws:cloudformation:{REGION}:111111111111:stack/agentu-staging/123"), ("StackName", "agentu-production")):
            f = fixture(); f["stack"][field] = value
            with self.assertRaisesRegex(ValueError, "different account"):
                verify_environment(f["session"], "staging")
            f["clients"]["lambda"].get_function_configuration.assert_not_called()

    def test_cross_environment_outputs_and_runtime_bindings_are_rejected(self):
        f = fixture()
        values = {**f["values"], "PlatformTable": "agentu-production-platform"}
        with self.assertRaisesRegex(ValueError, "another environment"):
            validate_outputs("staging", f["stack"], values)
        for kind in ("api", "worker"):
            f = fixture()
            f["functions"][f"agentu-staging-{kind}"]["Environment"]["Variables"]["PLATFORM_TABLE_NAME"] = "agentu-production-platform"
            with self.assertRaisesRegex(ValueError, "another stage"):
                verify_environment(f["session"], "staging")
            f["clients"]["s3"].upload_file.assert_not_called()

    def test_identity_drift_blocks_release_before_any_mutation(self):
        mutations = [lambda f: f["mfa"].update(MfaConfiguration="OPTIONAL"),
            lambda f: f["mfa"]["SoftwareTokenMfaConfiguration"].update(Enabled=False),
            lambda f: f["pool"]["AdminCreateUserConfig"].update(AllowAdminCreateUserOnly=False),
            lambda f: f["pool"]["UserAttributeUpdateSettings"].update(AttributesRequireVerificationBeforeUpdate=[]),
            lambda f: f["client"]["CallbackURLs"].append("https://personal.example.test/callback"),
            lambda f: f["client"].update(ClientSecret="fixture-private-client"),
            lambda f: f["client"]["TokenValidityUnits"].update(AccessToken="hours")]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                f = fixture(); mutate(f)
                with patch("release.build") as build, self.assertRaises(ValueError):
                    release.release(f["session"], "staging")
                build.assert_not_called()
                f["clients"]["lambda"].update_function_code.assert_not_called()
                f["clients"]["s3"].upload_file.assert_not_called()

    def test_data_and_website_drift_is_detected(self):
        mutations = [lambda f: f["tables"]["agentu-staging-platform"].update(DeletionProtectionEnabled=False),
            lambda f: f["tables"]["agentu-staging-platform"]["SSEDescription"].update(Status="DISABLING"),
            lambda f: f["pitr"]["agentu-staging-platform"]["PointInTimeRecoveryDescription"].update(PointInTimeRecoveryStatus="DISABLED"),
            lambda f: f["ttl"]["agentu-staging-platform"].update(TimeToLiveStatus="ENABLED", AttributeName="expires_at"),
            lambda f: f["public"].update(BlockPublicPolicy=False),
            lambda f: f["distribution"]["DistributionConfig"]["Origins"]["Items"][1].update(DomainName="production.execute-api.eu-west-2.amazonaws.com")]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                f = fixture(); mutate(f)
                with self.assertRaises(ValueError):
                    verify_environment(f["session"], "staging")

    def test_publish_refuses_mixed_api_worker_packages(self):
        f = fixture()
        f["functions"]["agentu-staging-worker"]["CodeSha256"] = base64.b64encode(bytes.fromhex("b" * 64)).decode()
        with patch("deploy.build", return_value={"lambda_sha256": HASH}), self.assertRaisesRegex(ValueError, "matching API"):
            deploy.publish(f["session"], "staging")
        f["clients"]["s3"].upload_file.assert_not_called()
        f["clients"]["cloudfront"].create_invalidation.assert_not_called()

    def test_api_authorization_and_cross_environment_integrations_cannot_drift(self):
        mutations = [lambda f: f["routes"][0].update(AuthorizationType="NONE"),
            lambda f: f["routes"][0].update(AuthorizationScopes=[]),
            lambda f: f["authorizer"]["JwtConfiguration"].update(Audience=["productionclient"]),
            lambda f: f["integration"].update(IntegrationUri=f"arn:aws:lambda:{REGION}:{EXPECTED_ACCOUNT}:function:agentu-production-api"),
            lambda f: f["routes"].append({"RouteKey": "$default", "Target": "integrations/integration123"})]
        for mutate in mutations:
            f = fixture(); mutate(f)
            with self.subTest(mutation=mutations.index(mutate)), self.assertRaises(ValueError):
                verify_environment(f["session"], "staging")

    def test_api_inventory_is_complete_across_pages_and_rejects_repeated_cursors(self):
        f = fixture()
        f["clients"]["apigatewayv2"].get_routes.side_effect = [{"Items": f["routes"][:3], "NextToken": "second"}, {"Items": f["routes"][3:]}]
        self.assertTrue(verify_environment(f["session"], "staging")["configuration_verified"])
        operation = Mock(return_value={"Items": [], "NextToken": "loop"})
        with self.assertRaisesRegex(ValueError, "pagination token"):
            api_items(operation, f["values"]["ApiId"])

    def test_release_uses_verified_revisions_and_does_not_publish_a_failed_update(self):
        for failed in (False, True):
            f = fixture()
            if failed:
                f["clients"]["lambda"].update_function_code.side_effect = RuntimeError("revision changed after verification")
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory); (path / "lambda.zip").write_bytes(b"fixture-code")
                with patch("release.OUT", path), patch("release.build"), patch("release.publish") as publish:
                    if failed:
                        with self.assertRaisesRegex(RuntimeError, "revision changed"):
                            release.release(f["session"], "staging")
                        publish.assert_not_called()
                    else:
                        release.release(f["session"], "staging")
                        publish.assert_called_once_with(f["session"], "staging")
                updates = f["clients"]["lambda"].update_function_code.call_args_list
                self.assertEqual("verified-worker", updates[0].kwargs["RevisionId"])
                if not failed:
                    self.assertEqual("verified-api", updates[1].kwargs["RevisionId"])

    def test_release_role_cannot_read_financial_records_or_cross_environments(self):
        f = fixture("production")
        doc = role_documents("production", f["stack"], f["values"])
        subject = doc["trust"]["Statement"][0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:sub"]
        self.assertEqual("repo:w3c-global/w3c-global.github.io:environment:agentu-production", subject)
        actions = {a for s in doc["permissions"]["Statement"] for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])}
        self.assertTrue({"cognito-idp:GetUserPoolMfaConfig", "dynamodb:DescribeContinuousBackups", "cloudfront:GetDistribution"} <= actions)
        self.assertFalse({"dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:DeleteItem", "iam:PassRole", "cloudformation:ExecuteChangeSet"} & actions)
        for statement in doc["permissions"]["Statement"]:
            self.assertNotEqual("*", statement["Resource"])
            self.assertNotIn("agentu-staging", json.dumps(statement))

    def test_reports_do_not_claim_hosted_journeys_or_echo_unrecognized_outputs(self):
        f = fixture()
        f["stack"]["Outputs"].append({"OutputKey": "UnrecognizedSecret", "OutputValue": "private-test-value"})
        report = verify_environment(f["session"], "staging")
        self.assertTrue(report["configuration_verified"])
        self.assertFalse(report["hosted_journeys_verified"])
        self.assertFalse(report["provider_execution_verified"])
        self.assertFalse(report["recovery_verified"])
        self.assertNotIn("private-test-value", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
