"""Generate isolated business stacks for development, rehearsal and operation."""
import json
import sys
from pathlib import Path
from environments import CONTRACT, ENVIRONMENTS, STAGES

R = lambda name: {"Ref": name}
A = lambda name, attr="Arn": {"Fn::GetAtt": [name, attr]}
S = lambda text: {"Fn::Sub": text}


def template():
    resources = {}
    def add(name, kind, props, **extra):
        resources[name] = {"Type": kind, "Properties": props, **extra}

    platform_access = {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:ConditionCheckItem"], "Resource": A("PlatformRecords")}
    work_delete = {"Effect": "Allow", "Action": "dynamodb:DeleteItem", "Resource": A("PlatformRecords"),
                   "Condition": {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": ["WORK#platform"]}}}

    add("Records", "AWS::DynamoDB::Table", {
        "TableName": S("agentu-${Stage}-records"), "BillingMode": "PAY_PER_REQUEST", "DeletionProtectionEnabled": True,
        "AttributeDefinitions": [{"AttributeName": "pk", "AttributeType": "S"}],
        "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}],
        "SSESpecification": {"SSEEnabled": True},
        "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
        "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
        "Tags": [{"Key": "Project", "Value": "Agentu"}, {"Key": "Environment", "Value": R("Stage")}]},
        DeletionPolicy="Retain", UpdateReplacePolicy="Retain")
    add("WebBucket", "AWS::S3::Bucket", {
        "BucketName": S("agentu-${Stage}-web-${AWS::AccountId}-${AWS::Region}"),
        "PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True, "RestrictPublicBuckets": True},
        "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
        "BucketEncryption": {"ServerSideEncryptionConfiguration": [{"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
        "VersioningConfiguration": {"Status": "Enabled"},
        "LifecycleConfiguration": {"Rules": [{"Id": "OldVersions", "Status": "Enabled", "NoncurrentVersionExpiration": {"NoncurrentDays": 30}}]},
        "Tags": [{"Key": "Project", "Value": "Agentu"}, {"Key": "Environment", "Value": R("Stage")}]},
        DeletionPolicy="Retain", UpdateReplacePolicy="Retain")
    add("PlatformRecords", "AWS::DynamoDB::Table", {
        "TableName": S("agentu-${Stage}-platform"), "BillingMode": "PAY_PER_REQUEST", "DeletionProtectionEnabled": True,
        "AttributeDefinitions": [{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
        "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        "SSESpecification": {"SSEEnabled": True},
        "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
        "Tags": [{"Key": "Project", "Value": "Agentu"}, {"Key": "Environment", "Value": R("Stage")}]},
        DeletionPolicy="Retain", UpdateReplacePolicy="Retain")
    retention = {"Fn::FindInMap": ["Environments", R("Stage"), "LogRetentionDays"]}
    add("ApiLogs", "AWS::Logs::LogGroup", {"LogGroupName": S("/agentu/${Stage}/api"), "RetentionInDays": retention})
    add("FunctionLogs", "AWS::Logs::LogGroup", {"LogGroupName": S("/aws/lambda/agentu-${Stage}-api"), "RetentionInDays": retention})
    add("ApiRole", "AWS::IAM::Role", {
        "RoleName": S("agentu-${Stage}-lambda"),
        "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
        "Policies": [{"PolicyName": "DemoStateAndLogs", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem"], "Resource": A("Records")},
            platform_access, work_delete,
            {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": A("FunctionLogs")}]}}]})
    add("ApiFunction", "AWS::Lambda::Function", {
        "FunctionName": S("agentu-${Stage}-api"), "Runtime": "python3.13", "Handler": "handler.handler",
        "Architectures": ["arm64"], "MemorySize": 256, "Timeout": 15, "Role": A("ApiRole"),
        "Code": {"S3Bucket": R("ArtifactBucket"), "S3Key": R("ArtifactKey")},
        "Environment": {"Variables": {"TABLE_NAME": R("Records"), "PLATFORM_TABLE_NAME": R("PlatformRecords"), "STAGE": R("Stage"), "BEDROCK_MODEL_ARN": R("BedrockModelArn")}},
        "Tags": [{"Key": "Project", "Value": "Agentu"}, {"Key": "Environment", "Value": R("Stage")}]}, DependsOn="FunctionLogs")
    add("WorkerLogs", "AWS::Logs::LogGroup", {"LogGroupName": S("/aws/lambda/agentu-${Stage}-worker"), "RetentionInDays": retention})
    add("WorkerRole", "AWS::IAM::Role", {
        "RoleName": S("agentu-${Stage}-worker"),
        "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
        "Policies": [{"PolicyName": "AgentWorkAndLogs", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
            platform_access, work_delete,
            {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": A("WorkerLogs")},
            {"Fn::If": ["EnableBedrock", {"Effect": "Allow", "Action": "bedrock:InvokeModel", "Resource": R("BedrockModelArn")}, R("AWS::NoValue")]}]}}]})
    add("WorkerFunction", "AWS::Lambda::Function", {
        "FunctionName": S("agentu-${Stage}-worker"), "Runtime": "python3.13", "Handler": "worker.handler",
        "Architectures": ["arm64"], "MemorySize": 256, "Timeout": 120, "ReservedConcurrentExecutions": 1, "Role": A("WorkerRole"),
        "Code": {"S3Bucket": R("ArtifactBucket"), "S3Key": R("ArtifactKey")},
        "Environment": {"Variables": {"PLATFORM_TABLE_NAME": R("PlatformRecords"), "STAGE": R("Stage"), "BEDROCK_MODEL_ARN": R("BedrockModelArn")}},
        "Tags": [{"Key": "Project", "Value": "Agentu"}, {"Key": "Environment", "Value": R("Stage")}]}, DependsOn="WorkerLogs")
    add("WorkerSchedule", "AWS::Events::Rule", {
        "Name": S("agentu-${Stage}-worker"), "ScheduleExpression": "rate(1 minute)", "State": "ENABLED",
        "Targets": [{"Id": "AgentuWorker", "Arn": A("WorkerFunction"), "Input": '{"source":"agentu.worker"}',
                     "RetryPolicy": {"MaximumEventAgeInSeconds": 300, "MaximumRetryAttempts": 2}}]})
    add("WorkerPermission", "AWS::Lambda::Permission", {
        "Action": "lambda:InvokeFunction", "FunctionName": R("WorkerFunction"), "Principal": "events.amazonaws.com", "SourceArn": A("WorkerSchedule")})
    add("WorkerInvokeConfig", "AWS::Lambda::EventInvokeConfig", {"FunctionName": R("WorkerFunction"), "Qualifier": "$LATEST", "MaximumEventAgeInSeconds": 300, "MaximumRetryAttempts": 0})
    add("WorkerErrors", "AWS::CloudWatch::Alarm", {"AlarmName": S("agentu-${Stage}-worker-errors"), "AlarmDescription": "Worker invocation failed; durable jobs are retained for retry.", "Namespace": "AWS/Lambda", "MetricName": "Errors", "Dimensions": [{"Name": "FunctionName", "Value": R("WorkerFunction")}], "Statistic": "Sum", "Period": 300, "EvaluationPeriods": 1, "Threshold": 1, "ComparisonOperator": "GreaterThanOrEqualToThreshold", "TreatMissingData": "notBreaching"})
    for metric in ("FailedRuns", "ExpiryRetries", "Heartbeats"):
        heartbeat = metric == "Heartbeats"
        add("Worker" + metric, "AWS::CloudWatch::Alarm", {
            "AlarmName": S("agentu-${Stage}-worker-" + metric.lower()), "Namespace": "Agentu/Worker", "MetricName": metric,
            "Dimensions": [{"Name": "Stage", "Value": R("Stage")}], "Statistic": "Sum", "Period": 300, "EvaluationPeriods": 1,
            "Threshold": 1, "ComparisonOperator": "LessThanThreshold" if heartbeat else "GreaterThanOrEqualToThreshold",
            "TreatMissingData": "breaching" if heartbeat else "notBreaching"})
    add("UserPool", "AWS::Cognito::UserPool", {
        "UserPoolName": S("agentu-${Stage}-presenters"),
        "DeletionProtection": "ACTIVE", "MfaConfiguration": "ON", "EnabledMfas": ["SOFTWARE_TOKEN_MFA"],
        "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
        "UsernameAttributes": ["email"], "AutoVerifiedAttributes": ["email"],
        "UserAttributeUpdateSettings": {"AttributesRequireVerificationBeforeUpdate": ["email"]},
        "Policies": {"PasswordPolicy": {"MinimumLength": 14, "RequireLowercase": True, "RequireUppercase": True, "RequireNumbers": True, "RequireSymbols": True, "TemporaryPasswordValidityDays": 3}},
        "AccountRecoverySetting": {"RecoveryMechanisms": [{"Name": "verified_email", "Priority": 1}]},
        "UserPoolTags": {"Project": "Agentu", "Environment": R("Stage")}},
        DeletionPolicy="Retain", UpdateReplacePolicy="Retain")
    add("UserPoolDomain", "AWS::Cognito::UserPoolDomain", {"Domain": S("agentu-${Stage}-${AWS::AccountId}"), "UserPoolId": R("UserPool")})
    add("UserPoolClient", "AWS::Cognito::UserPoolClient", {
        "ClientName": S("agentu-${Stage}-browser"), "UserPoolId": R("UserPool"), "GenerateSecret": False,
        "AllowedOAuthFlowsUserPoolClient": True, "AllowedOAuthFlows": ["code"], "AllowedOAuthScopes": ["openid", "email", "aws.cognito.signin.user.admin"],
        "SupportedIdentityProviders": ["COGNITO"], "PreventUserExistenceErrors": "ENABLED", "EnableTokenRevocation": True,
        "AccessTokenValidity": 15, "IdTokenValidity": 15, "RefreshTokenValidity": 1,
        "TokenValidityUnits": {"AccessToken": "minutes", "IdToken": "minutes", "RefreshToken": "days"},
        "CallbackURLs": [S("https://${Distribution.DomainName}/agentu/demo/"), S("https://${Distribution.DomainName}/agentu/app/")], "LogoutURLs": [S("https://${Distribution.DomainName}/agentu/")]})
    add("HttpApi", "AWS::ApiGatewayV2::Api", {"Name": S("agentu-${Stage}"), "ProtocolType": "HTTP"})
    add("ApiIntegration", "AWS::ApiGatewayV2::Integration", {
        "ApiId": R("HttpApi"), "IntegrationType": "AWS_PROXY", "IntegrationUri": A("ApiFunction"), "PayloadFormatVersion": "2.0", "TimeoutInMillis": 15000})
    add("JwtAuthorizer", "AWS::ApiGatewayV2::Authorizer", {
        "ApiId": R("HttpApi"), "Name": "PresenterSignIn", "AuthorizerType": "JWT", "IdentitySource": ["$request.header.Authorization"],
        "JwtConfiguration": {"Audience": [R("UserPoolClient")], "Issuer": S("https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPool}")}})
    for name, route in {"State": "GET /api/state", "Actions": "POST /api/actions", "Export": "GET /api/export", "Health": "GET /api/health", "Platform": "ANY /api/platform/{proxy+}", "AgentProposal": "POST /api/agent/proposals"}.items():
        props = {"ApiId": R("HttpApi"), "RouteKey": route, "Target": {"Fn::Join": ["", ["integrations/", R("ApiIntegration")]]}}
        if name not in ("Health", "AgentProposal"):
            props.update(AuthorizationType="JWT", AuthorizerId=R("JwtAuthorizer"), AuthorizationScopes=["openid"])
        add(name + "Route", "AWS::ApiGatewayV2::Route", props)
    add("ApiStage", "AWS::ApiGatewayV2::Stage", {
        "ApiId": R("HttpApi"), "StageName": "$default", "AutoDeploy": True,
        "DefaultRouteSettings": {"ThrottlingBurstLimit": 20, "ThrottlingRateLimit": 10},
        "AccessLogSettings": {"DestinationArn": A("ApiLogs"), "Format": '{"requestId":"$context.requestId","status":"$context.status","route":"$context.routeKey","latency":"$context.responseLatency"}'}})
    add("InvokePermission", "AWS::Lambda::Permission", {
        "Action": "lambda:InvokeFunction", "FunctionName": R("ApiFunction"), "Principal": "apigateway.amazonaws.com",
        "SourceArn": S("arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*")})
    add("OriginAccess", "AWS::CloudFront::OriginAccessControl", {"OriginAccessControlConfig": {
        "Name": S("agentu-${Stage}-${AWS::AccountId}"), "OriginAccessControlOriginType": "s3", "SigningBehavior": "always", "SigningProtocol": "sigv4"}})
    add("DirectoryIndex", "AWS::CloudFront::Function", {"Name": S("agentu-${Stage}-directory-index"), "AutoPublish": True,
        "FunctionConfig": {"Comment": "Agentu paths only", "Runtime": "cloudfront-js-2.0"},
        "FunctionCode": "function handler(event){var r=event.request;if(r.uri==='/'){return {statusCode:302,headers:{location:{value:'/agentu/'}}};}if(r.uri.endsWith('/')){r.uri+='index.html';}else if(!r.uri.includes('.')){r.uri+='/index.html';}return r;}"})
    add("SecurityHeaders", "AWS::CloudFront::ResponseHeadersPolicy", {"ResponseHeadersPolicyConfig": {
        "Name": S("agentu-${Stage}-security"), "SecurityHeadersConfig": {
            "ContentTypeOptions": {"Override": True}, "FrameOptions": {"FrameOption": "DENY", "Override": True},
            "ReferrerPolicy": {"ReferrerPolicy": "strict-origin-when-cross-origin", "Override": True},
            "StrictTransportSecurity": {"AccessControlMaxAgeSec": 31536000, "IncludeSubdomains": True, "Override": True},
            "ContentSecurityPolicy": {"ContentSecurityPolicy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' https://*.amazoncognito.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'", "Override": True}},
        "CustomHeadersConfig": {"Items": [{"Header": "Permissions-Policy", "Value": "camera=(), microphone=(), geolocation=()", "Override": True}]}}})
    add("Distribution", "AWS::CloudFront::Distribution", {"DistributionConfig": {
        "Comment": S("Agentu ${Stage} - simulated financial operations"), "Enabled": True, "HttpVersion": "http2and3", "PriceClass": "PriceClass_100",
        "Origins": [{"Id": "website", "DomainName": A("WebBucket", "RegionalDomainName"), "S3OriginConfig": {"OriginAccessIdentity": ""}, "OriginAccessControlId": R("OriginAccess")},
                    {"Id": "api", "DomainName": S("${HttpApi}.execute-api.${AWS::Region}.amazonaws.com"), "CustomOriginConfig": {"OriginProtocolPolicy": "https-only", "OriginSSLProtocols": ["TLSv1.2"]}}],
        "DefaultCacheBehavior": {"TargetOriginId": "website", "ViewerProtocolPolicy": "redirect-to-https", "AllowedMethods": ["GET", "HEAD"], "Compress": True,
            "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6", "ResponseHeadersPolicyId": R("SecurityHeaders"),
            "FunctionAssociations": [{"EventType": "viewer-request", "FunctionARN": A("DirectoryIndex", "FunctionARN")}]},
        "CacheBehaviors": [{"PathPattern": "/api/*", "TargetOriginId": "api", "ViewerProtocolPolicy": "https-only", "AllowedMethods": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
            "CachePolicyId": "413f160f-8c3f-4f01-bc5b-3c7054ab4058", "OriginRequestPolicyId": "b689b0a8-53d0-40ab-baf2-68738e2966ac", "ResponseHeadersPolicyId": R("SecurityHeaders"), "Compress": True},
            {"PathPattern": "/agentu/*/config.json", "TargetOriginId": "website", "ViewerProtocolPolicy": "redirect-to-https", "AllowedMethods": ["GET", "HEAD"], "CachePolicyId": "413f160f-8c3f-4f01-bc5b-3c7054ab4058", "ResponseHeadersPolicyId": R("SecurityHeaders")}],
        "ViewerCertificate": {"CloudFrontDefaultCertificate": True}}, "Tags": [{"Key": "Project", "Value": "Agentu"}, {"Key": "Environment", "Value": R("Stage")}]})
    add("WebBucketPolicy", "AWS::S3::BucketPolicy", {"Bucket": R("WebBucket"), "PolicyDocument": {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "cloudfront.amazonaws.com"}, "Action": "s3:GetObject", "Resource": S("${WebBucket.Arn}/*"), "Condition": {"StringEquals": {"AWS:SourceArn": S("arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${Distribution}")}}},
        {"Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": [A("WebBucket"), S("${WebBucket.Arn}/*")], "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}})
    add("ApiErrors", "AWS::CloudWatch::Alarm", {"AlarmName": S("agentu-${Stage}-api-errors"), "AlarmDescription": "Demo API errors; inspect CloudWatch logs.", "Namespace": "AWS/Lambda", "MetricName": "Errors", "Dimensions": [{"Name": "FunctionName", "Value": R("ApiFunction")}], "Statistic": "Sum", "Period": 300, "EvaluationPeriods": 1, "Threshold": 3, "ComparisonOperator": "GreaterThanOrEqualToThreshold", "TreatMissingData": "notBreaching"})
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Agentu governed operations and agent runtime. Simulated funds only.",
            "Mappings": {"Environments": ENVIRONMENTS},
            "Parameters": {"Stage": {"Type": "String", "AllowedValues": list(STAGES)}, "ArtifactBucket": {"Type": "String"}, "ArtifactKey": {"Type": "String"},
                           "BedrockModelArn": {"Type": "String", "Default": "", "AllowedPattern": "^$|^arn:aws:bedrock:eu-west-2::foundation-model/[a-z0-9][a-z0-9.:-]{1,200}$", "Description": "Optional approved London foundation model ARN; empty disables Bedrock."}},
            "Conditions": {"EnableBedrock": {"Fn::Not": [{"Fn::Equals": [R("BedrockModelArn"), ""]}]}},
            "Resources": resources, "Outputs": {
                "Environment": {"Value": R("Stage")}, "DeploymentContract": {"Value": CONTRACT},
                "WebsiteUrl": {"Value": S("https://${Distribution.DomainName}/agentu/")}, "DemoUrl": {"Value": S("https://${Distribution.DomainName}/agentu/demo/")}, "AppUrl": {"Value": S("https://${Distribution.DomainName}/agentu/app/")},
                "DistributionId": {"Value": R("Distribution")}, "WebBucket": {"Value": R("WebBucket")},
                "UserPoolId": {"Value": R("UserPool")}, "UserPoolClientId": {"Value": R("UserPoolClient")},
                "AuthDomain": {"Value": S("https://agentu-${Stage}-${AWS::AccountId}.auth.${AWS::Region}.amazoncognito.com")},
                "ApiId": {"Value": R("HttpApi")}, "FunctionName": {"Value": R("ApiFunction")}, "WorkerFunctionName": {"Value": R("WorkerFunction")},
                "RecordsTable": {"Value": R("Records")}, "PlatformTable": {"Value": R("PlatformRecords")}, "BedrockModelArn": {"Value": R("BedrockModelArn")}}}


if __name__ == "__main__":
    result = json.dumps(template(), indent=2)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(result, encoding="utf-8")
    else:
        print(result)
