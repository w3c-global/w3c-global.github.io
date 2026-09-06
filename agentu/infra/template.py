"""Generate the same isolated AWS stack for sandbox and founder demo."""
import json
import sys
from pathlib import Path

R = lambda name: {"Ref": name}
A = lambda name, attr="Arn": {"Fn::GetAtt": [name, attr]}
S = lambda text: {"Fn::Sub": text}


def template():
    resources = {}
    def add(name, kind, props, **extra):
        resources[name] = {"Type": kind, "Properties": props, **extra}

    add("Records", "AWS::DynamoDB::Table", {
        "TableName": S("agentu-${Stage}-records"), "BillingMode": "PAY_PER_REQUEST",
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
    add("ApiLogs", "AWS::Logs::LogGroup", {"LogGroupName": S("/agentu/${Stage}/api"), "RetentionInDays": 14})
    add("FunctionLogs", "AWS::Logs::LogGroup", {"LogGroupName": S("/aws/lambda/agentu-${Stage}-api"), "RetentionInDays": 14})
    add("ApiRole", "AWS::IAM::Role", {
        "RoleName": S("agentu-${Stage}-lambda"),
        "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
        "Policies": [{"PolicyName": "DemoStateAndLogs", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem"], "Resource": A("Records")},
            {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": A("FunctionLogs")}]}}]})
    add("ApiFunction", "AWS::Lambda::Function", {
        "FunctionName": S("agentu-${Stage}-api"), "Runtime": "python3.13", "Handler": "handler.handler",
        "Architectures": ["arm64"], "MemorySize": 256, "Timeout": 15, "Role": A("ApiRole"),
        "Code": {"S3Bucket": R("ArtifactBucket"), "S3Key": R("ArtifactKey")},
        "Environment": {"Variables": {"TABLE_NAME": R("Records"), "STAGE": R("Stage")}},
        "Tags": [{"Key": "Project", "Value": "Agentu"}, {"Key": "Environment", "Value": R("Stage")}]}, DependsOn="FunctionLogs")
    add("UserPool", "AWS::Cognito::UserPool", {
        "UserPoolName": S("agentu-${Stage}-presenters"),
        "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
        "UsernameAttributes": ["email"], "AutoVerifiedAttributes": ["email"],
        "Policies": {"PasswordPolicy": {"MinimumLength": 14, "RequireLowercase": True, "RequireUppercase": True, "RequireNumbers": True, "RequireSymbols": True, "TemporaryPasswordValidityDays": 3}},
        "AccountRecoverySetting": {"RecoveryMechanisms": [{"Name": "verified_email", "Priority": 1}]},
        "UserPoolTags": {"Project": "Agentu", "Environment": R("Stage")}},
        DeletionPolicy="Retain", UpdateReplacePolicy="Retain")
    add("UserPoolDomain", "AWS::Cognito::UserPoolDomain", {"Domain": S("agentu-${Stage}-${AWS::AccountId}"), "UserPoolId": R("UserPool")})
    add("UserPoolClient", "AWS::Cognito::UserPoolClient", {
        "ClientName": S("agentu-${Stage}-browser"), "UserPoolId": R("UserPool"), "GenerateSecret": False,
        "AllowedOAuthFlowsUserPoolClient": True, "AllowedOAuthFlows": ["code"], "AllowedOAuthScopes": ["openid", "email"],
        "SupportedIdentityProviders": ["COGNITO"], "PreventUserExistenceErrors": "ENABLED", "EnableTokenRevocation": True,
        "AccessTokenValidity": 60, "IdTokenValidity": 60, "RefreshTokenValidity": 1,
        "TokenValidityUnits": {"AccessToken": "minutes", "IdToken": "minutes", "RefreshToken": "days"},
        "CallbackURLs": [S("https://${Distribution.DomainName}/agentu/demo/")], "LogoutURLs": [S("https://${Distribution.DomainName}/agentu/")]})
    add("HttpApi", "AWS::ApiGatewayV2::Api", {"Name": S("agentu-${Stage}"), "ProtocolType": "HTTP"})
    add("ApiIntegration", "AWS::ApiGatewayV2::Integration", {
        "ApiId": R("HttpApi"), "IntegrationType": "AWS_PROXY", "IntegrationUri": A("ApiFunction"), "PayloadFormatVersion": "2.0", "TimeoutInMillis": 15000})
    add("JwtAuthorizer", "AWS::ApiGatewayV2::Authorizer", {
        "ApiId": R("HttpApi"), "Name": "PresenterSignIn", "AuthorizerType": "JWT", "IdentitySource": ["$request.header.Authorization"],
        "JwtConfiguration": {"Audience": [R("UserPoolClient")], "Issuer": S("https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPool}")}})
    for name, route in {"State": "GET /api/state", "Actions": "POST /api/actions", "Export": "GET /api/export", "Health": "GET /api/health"}.items():
        props = {"ApiId": R("HttpApi"), "RouteKey": route, "Target": {"Fn::Join": ["", ["integrations/", R("ApiIntegration")]]}}
        if name != "Health":
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
            {"PathPattern": "/agentu/demo/config.json", "TargetOriginId": "website", "ViewerProtocolPolicy": "redirect-to-https", "AllowedMethods": ["GET", "HEAD"], "CachePolicyId": "413f160f-8c3f-4f01-bc5b-3c7054ab4058", "ResponseHeadersPolicyId": R("SecurityHeaders")}],
        "ViewerCertificate": {"CloudFrontDefaultCertificate": True}}, "Tags": [{"Key": "Project", "Value": "Agentu"}, {"Key": "Environment", "Value": R("Stage")}]})
    add("WebBucketPolicy", "AWS::S3::BucketPolicy", {"Bucket": R("WebBucket"), "PolicyDocument": {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "cloudfront.amazonaws.com"}, "Action": "s3:GetObject", "Resource": S("${WebBucket.Arn}/*"), "Condition": {"StringEquals": {"AWS:SourceArn": S("arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${Distribution}")}}},
        {"Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": [A("WebBucket"), S("${WebBucket.Arn}/*")], "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}})
    add("ApiErrors", "AWS::CloudWatch::Alarm", {"AlarmName": S("agentu-${Stage}-api-errors"), "AlarmDescription": "Demo API errors; inspect CloudWatch logs.", "Namespace": "AWS/Lambda", "MetricName": "Errors", "Dimensions": [{"Name": "FunctionName", "Value": R("ApiFunction")}], "Statistic": "Sum", "Period": 300, "EvaluationPeriods": 1, "Threshold": 3, "ComparisonOperator": "GreaterThanOrEqualToThreshold", "TreatMissingData": "notBreaching"})
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Agentu isolated founder demonstration. Simulated funds only.",
            "Parameters": {"Stage": {"Type": "String", "AllowedValues": ["sandbox", "demo"]}, "ArtifactBucket": {"Type": "String"}, "ArtifactKey": {"Type": "String"}},
            "Resources": resources, "Outputs": {
                "WebsiteUrl": {"Value": S("https://${Distribution.DomainName}/agentu/")}, "DemoUrl": {"Value": S("https://${Distribution.DomainName}/agentu/demo/")},
                "DistributionId": {"Value": R("Distribution")}, "WebBucket": {"Value": R("WebBucket")},
                "UserPoolId": {"Value": R("UserPool")}, "UserPoolClientId": {"Value": R("UserPoolClient")},
                "AuthDomain": {"Value": S("https://agentu-${Stage}-${AWS::AccountId}.auth.${AWS::Region}.amazoncognito.com")},
                "ApiId": {"Value": R("HttpApi")}, "FunctionName": {"Value": R("ApiFunction")}, "RecordsTable": {"Value": R("Records")}}}


if __name__ == "__main__":
    result = json.dumps(template(), indent=2)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(result, encoding="utf-8")
    else:
        print(result)
