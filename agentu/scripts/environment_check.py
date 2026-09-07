"""Read deployed settings before releasing to a named Agentu environment.

This verifies resource bindings and selected security controls. It does not
replace hosted journey, provider, alert-delivery or recovery verification.
"""
from datetime import datetime, timezone
import base64
import re
from environments import CONTRACT, EXPECTED_ACCOUNT, REGION, environment


def require(condition, message):
    if not condition:
        raise ValueError("Environment verification stopped: " + message)


def stack_identity(stage, stack):
    environment(stage)
    prefix = f"arn:aws:cloudformation:{REGION}:{EXPECTED_ACCOUNT}:stack/agentu-{stage}/"
    require(stack.get("StackName") == "agentu-" + stage and stack.get("StackId", "").startswith(prefix), "The stack belongs to a different account, region or environment.")
    parameters = {p["ParameterKey"]: p.get("ParameterValue") for p in stack.get("Parameters", [])}
    if stack.get("StackStatus") != "REVIEW_IN_PROGRESS":
        require(parameters.get("Stage") == stage, "The stored Stage parameter disagrees with the requested environment.")


def validate_outputs(stage, stack, values):
    stack_identity(stage, stack)
    require(stack.get("StackStatus") in ("CREATE_COMPLETE", "UPDATE_COMPLETE"), "The infrastructure has not completed successfully.")
    tags = {p["Key"]: p["Value"] for p in stack.get("Tags", [])}
    require(tags.get("Project") == "Agentu" and tags.get("Environment") == stage, "The stack ownership tags do not match Agentu.")
    require(values.get("Environment") == stage and values.get("DeploymentContract") == CONTRACT, "Apply the current environment infrastructure before releasing code.")
    for key, suffix in (("FunctionName", "api"), ("WorkerFunctionName", "worker"), ("RecordsTable", "records"), ("PlatformTable", "platform")):
        require(values.get(key) == f"agentu-{stage}-{suffix}", "An output targets another environment: " + key)
    require(values.get("WebBucket") == f"agentu-{stage}-web-{EXPECTED_ACCOUNT}-{REGION}", "The website bucket is outside this environment.")
    require(re.fullmatch(r"https://[a-z0-9]+\.cloudfront\.net/agentu/", values.get("WebsiteUrl", "")), "The website URL is not an Agentu CloudFront origin.")
    for key, path in (("AppUrl", "app/"), ("DemoUrl", "demo/")):
        require(values.get(key) == values["WebsiteUrl"] + path, "The application URLs belong to different deployments.")
    require(values.get("AuthDomain") == f"https://agentu-{stage}-{EXPECTED_ACCOUNT}.auth.{REGION}.amazoncognito.com", "The sign-in domain belongs to another environment.")
    for key, pattern in (("UserPoolId", REGION + r"_[A-Za-z0-9]+"), ("UserPoolClientId", r"[a-z0-9]+"), ("ApiId", r"[a-z0-9]+"), ("DistributionId", r"[A-Z0-9]+")):
        require(re.fullmatch(pattern, values.get(key, "")), "Invalid environment resource identifier: " + key)


def api_items(operation, api_id):
    items, seen, token = [], set(), None
    while True:
        page = operation(ApiId=api_id, **({"NextToken": token} if token else {}))
        items.extend(page.get("Items", []))
        token = page.get("NextToken")
        if not token:
            return items
        require(token not in seen, "An API configuration listing repeated its pagination token.")
        seen.add(token)


def verify_environment(session, stage, expected_hash=None):
    from deploy import outputs
    stack, values = outputs(session, stage)
    validate_outputs(stage, stack, values)
    functions = {}
    for key, suffix, handler in (("FunctionName", "api", "handler.handler"), ("WorkerFunctionName", "worker", "worker.handler")):
        config = session.client("lambda").get_function_configuration(FunctionName=values[key])
        expected_arn = f"arn:aws:lambda:{REGION}:{EXPECTED_ACCOUNT}:function:{values[key]}"
        require(config.get("FunctionArn") == expected_arn and config.get("FunctionName") == values[key], "A function identity is outside the requested environment.")
        require(config.get("State") == "Active" and config.get("LastUpdateStatus") == "Successful", "A function is inactive or still updating.")
        require(config.get("Runtime") == "python3.13" and config.get("Handler") == handler and config.get("Architectures") == ["arm64"], "A function runtime differs from the deployment contract.")
        role = "lambda" if suffix == "api" else "worker"
        require(config.get("Role") == f"arn:aws:iam::{EXPECTED_ACCOUNT}:role/agentu-{stage}-{role}", "A function uses a different environment's role.")
        variables = config.get("Environment", {}).get("Variables", {})
        require(variables.get("STAGE") == stage and variables.get("PLATFORM_TABLE_NAME") == values["PlatformTable"], "A function is bound to another stage or institution table.")
        require(variables.get("BEDROCK_MODEL_ARN", "") == values.get("BedrockModelArn", ""), "The configured model differs from the deployed choice.")
        if suffix == "api":
            require(variables.get("TABLE_NAME") == values["RecordsTable"], "The API is bound to another rehearsal table.")
        code_hash = base64.b64decode(config.get("CodeSha256", ""), validate=True).hex()
        require(len(code_hash) == 64 and config.get("RevisionId"), "A function is missing its code or revision evidence.")
        if expected_hash is not None:
            require(code_hash == expected_hash, "Release the matching API and worker packages before publishing assets.")
        functions[suffix] = {"name": values[key], "revision": config["RevisionId"], "code_sha256": code_hash}

    cognito = session.client("cognito-idp")
    pool = cognito.describe_user_pool(UserPoolId=values["UserPoolId"])["UserPool"]
    require(pool.get("Arn") == f"arn:aws:cognito-idp:{REGION}:{EXPECTED_ACCOUNT}:userpool/{values['UserPoolId']}", "The identity pool belongs to another account or region.")
    require(pool.get("Name") == f"agentu-{stage}-presenters" and pool.get("DeletionProtection") == "ACTIVE", "The identity pool name or deletion protection has drifted.")
    require(pool.get("AdminCreateUserConfig", {}).get("AllowAdminCreateUserOnly") is True, "Hosted identity registration is no longer invitation-only.")
    require("email" in pool.get("UserAttributeUpdateSettings", {}).get("AttributesRequireVerificationBeforeUpdate", []), "Email changes no longer require ownership verification.")
    mfa = cognito.get_user_pool_mfa_config(UserPoolId=values["UserPoolId"])
    require(mfa.get("MfaConfiguration") == "ON" and mfa.get("SoftwareTokenMfaConfiguration", {}).get("Enabled") is True, "Authenticator-app MFA is not required and enabled.")
    client = cognito.describe_user_pool_client(UserPoolId=values["UserPoolId"], ClientId=values["UserPoolClientId"])["UserPoolClient"]
    require(client.get("ClientId") == values["UserPoolClientId"] and client.get("UserPoolId") == values["UserPoolId"] and not client.get("ClientSecret"), "The browser identity client is not a public client of this pool.")
    require(client.get("AllowedOAuthFlowsUserPoolClient") is True and client.get("AllowedOAuthFlows") == ["code"] and client.get("SupportedIdentityProviders") == ["COGNITO"], "The sign-in flow or identity provider differs from the MFA-controlled client.")
    require(set(client.get("AllowedOAuthScopes", [])) == {"openid", "email", "aws.cognito.signin.user.admin"}, "The hosted identity scopes have changed.")
    require(set(client.get("CallbackURLs", [])) == {values["AppUrl"], values["DemoUrl"]} and client.get("LogoutURLs") == [values["WebsiteUrl"]], "The identity client redirects outside this environment.")
    require(client.get("EnableTokenRevocation") is True and client.get("PreventUserExistenceErrors") == "ENABLED", "Identity token or account-enumeration controls have drifted.")
    units = client.get("TokenValidityUnits", {})
    require(client.get("AccessTokenValidity") == 15 and client.get("IdTokenValidity") == 15 and client.get("RefreshTokenValidity") == 1 and units == {"AccessToken": "minutes", "IdToken": "minutes", "RefreshToken": "days"}, "Token lifetimes differ from the hosted security baseline.")

    gateway = session.client("apigatewayv2")
    api = gateway.get_api(ApiId=values["ApiId"])
    require(api.get("Name") == "agentu-" + stage and api.get("ProtocolType") == "HTTP", "The API gateway belongs to another environment or protocol.")
    authorizers = api_items(gateway.get_authorizers, values["ApiId"])
    require(len(authorizers) == 1, "The API must have exactly its configured identity authorizer.")
    authorizer = authorizers[0]
    require(authorizer.get("AuthorizerId") and authorizer.get("AuthorizerType") == "JWT" and authorizer.get("IdentitySource") == ["$request.header.Authorization"], "The API is not using its JWT identity boundary.")
    require(authorizer.get("JwtConfiguration") == {"Audience": [values["UserPoolClientId"]], "Issuer": f"https://cognito-idp.{REGION}.amazonaws.com/{values['UserPoolId']}"}, "The API trusts another environment's identity client or pool.")
    integrations = api_items(gateway.get_integrations, values["ApiId"])
    require(len(integrations) == 1, "The API must have exactly its configured application integration.")
    integration = integrations[0]
    require(integration.get("IntegrationId") and integration.get("IntegrationType") == "AWS_PROXY" and integration.get("PayloadFormatVersion") == "2.0" and integration.get("IntegrationUri") == f"arn:aws:lambda:{REGION}:{EXPECTED_ACCOUNT}:function:{values['FunctionName']}", "The API integration targets another application or payload format.")
    routes = api_items(gateway.get_routes, values["ApiId"])
    expected_routes = {"GET /api/state", "POST /api/actions", "GET /api/export", "GET /api/health", "ANY /api/platform/{proxy+}", "POST /api/agent/proposals"}
    require(len(routes) == len(expected_routes) and {r.get("RouteKey") for r in routes} == expected_routes, "The API route inventory is missing a route or exposes an unexpected route.")
    for route in routes:
        require(route.get("Target") == "integrations/" + integration["IntegrationId"], "An API route targets another integration.")
        if route["RouteKey"] in ("GET /api/health", "POST /api/agent/proposals"):
            require(route.get("AuthorizationType", "NONE") == "NONE", "The health or machine-credential route configuration has changed.")
        else:
            require(route.get("AuthorizationType") == "JWT" and route.get("AuthorizerId") == authorizer["AuthorizerId"] and route.get("AuthorizationScopes") == ["openid"], "A human API route does not enforce this pool's access-token scope.")

    dynamo = session.client("dynamodb")
    for key in ("RecordsTable", "PlatformTable"):
        table = dynamo.describe_table(TableName=values[key])["Table"]
        require(table.get("TableArn") == f"arn:aws:dynamodb:{REGION}:{EXPECTED_ACCOUNT}:table/{values[key]}" and table.get("TableStatus") == "ACTIVE", "A data table is inactive or belongs to another environment.")
        require(table.get("DeletionProtectionEnabled") is True, "A records table is not protected from deletion.")
        require(table.get("SSEDescription", {}).get("Status") == "ENABLED", "A records table's encryption is not ready.")
        backup = dynamo.describe_continuous_backups(TableName=values[key])["ContinuousBackupsDescription"]
        require(backup.get("PointInTimeRecoveryDescription", {}).get("PointInTimeRecoveryStatus") == "ENABLED", "Point-in-time recovery is not enabled.")
        ttl = dynamo.describe_time_to_live(TableName=values[key])["TimeToLiveDescription"]
        if key == "PlatformTable":
            require(ttl.get("TimeToLiveStatus") == "DISABLED", "Institution records must never be deleted by table TTL.")
        else:
            require(ttl.get("TimeToLiveStatus") == "ENABLED" and ttl.get("AttributeName") == "expires_at", "Rehearsal record expiry differs from the template.")

    s3 = session.client("s3")
    bucket = {"Bucket": values["WebBucket"], "ExpectedBucketOwner": EXPECTED_ACCOUNT}
    public = s3.get_public_access_block(**bucket)["PublicAccessBlockConfiguration"]
    require(all(public.get(k) is True for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")), "The website bucket is no longer private.")
    require(s3.get_bucket_versioning(**bucket).get("Status") == "Enabled", "Website versioning is not enabled.")
    encryption = s3.get_bucket_encryption(**bucket)["ServerSideEncryptionConfiguration"]["Rules"]
    require(any(r.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm") == "AES256" for r in encryption), "Website encryption differs from the template.")
    distribution = session.client("cloudfront").get_distribution(Id=values["DistributionId"])["Distribution"]
    require(distribution.get("ARN") == f"arn:aws:cloudfront::{EXPECTED_ACCOUNT}:distribution/{values['DistributionId']}" and distribution.get("Status") == "Deployed", "CloudFront is not deployed in the designated account.")
    require(values["WebsiteUrl"] == f"https://{distribution.get('DomainName')}/agentu/" and distribution.get("DistributionConfig", {}).get("Enabled") is True, "The advertised website does not match the active distribution.")
    origins = {o["Id"]: o["DomainName"] for o in distribution.get("DistributionConfig", {}).get("Origins", {}).get("Items", [])}
    require(origins == {"website": f"{values['WebBucket']}.s3.{REGION}.amazonaws.com", "api": f"{values['ApiId']}.execute-api.{REGION}.amazonaws.com"}, "CloudFront routes to another environment's website or API.")
    return {"schema": "agentu.aws.configuration-check.v1", "checked_at": datetime.now(timezone.utc).isoformat(),
            "account": EXPECTED_ACCOUNT, "region": REGION, "stage": stage, "stack_id": stack["StackId"],
            "configuration_verified": True, "functions": functions,
            "outputs": {key: values[key] for key in ("WebsiteUrl", "AppUrl", "DemoUrl", "WebBucket", "DistributionId", "UserPoolId", "UserPoolClientId", "AuthDomain", "ApiId", "FunctionName", "WorkerFunctionName", "RecordsTable", "PlatformTable", "Environment", "DeploymentContract")},
            "verified_controls": ["environment_bindings", "required_totp_mfa", "identity_client", "api_jwt_routes", "data_encryption", "deletion_protection", "pitr_enabled", "platform_ttl_disabled", "private_versioned_website"],
            "hosted_journeys_verified": False, "provider_execution_verified": False, "recovery_verified": False}
