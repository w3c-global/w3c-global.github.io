# AWS operations and release guide

## Fixed deployment boundary

- Account: `032312375271` only. Deployment scripts verify STS identity and refuse other accounts or root credentials.
- Region: `eu-west-2` (London).
- Stacks: `agentu-sandbox` and `agentu-demo`.
- Source: `w3c-global/w3c-global.github.io`.
- Use a business IAM/federated session. Never save access keys in this repository, GitHub secrets or the website.

AWS sign-in is required before provisioning. A browser console login does not automatically create a command-line session. Use an approved named AWS profile or run the deployment from authenticated AWS CloudShell. Both paths must pass the account check.

## Initial provisioning

Install the pinned tooling with `python -m pip install -r agentu/requirements-tools.txt`. If using a named local profile, append `--profile agentu` to every command below.

```text
python agentu/scripts/deploy.py identity --stage sandbox
python agentu/scripts/deploy.py plan --stage sandbox
python agentu/scripts/deploy.py status --stage sandbox --change-set <returned-name>
python agentu/scripts/deploy.py apply --stage sandbox --change-set <reviewed-name>
python agentu/scripts/deploy.py status --stage sandbox
python agentu/scripts/deploy.py publish --stage sandbox
```

`plan` creates or secures an Agentu-only deployment-artifact bucket, uploads the package and prepares an unexecuted CloudFormation change set. It is not read-only. Inspect the named change set before applying. Repeat with `--stage demo` after the sandbox succeeds.

The template manages website storage, CloudFront, HTTPS, Cognito and exact callback URLs, HTTP API/JWT authentication, Lambda, DynamoDB, retention, logging and an error alarm. Outputs supply the actual website and demo URLs. No custom domain is assumed.

If the account has restricted quotas, request only the permissions or quota changes required by the named Agentu resources. Do not deploy into a different account as a workaround.

## Users and cost monitoring

Create an invited presenter in the environment’s Cognito pool, using the business email agreed by the user. Deliver onboarding only to an explicitly authorised recipient. Do not publish passwords or copy them into task notes. Configure MFA and the recovery process appropriate to the invited users before broader access.

Before declaring cloud readiness, configure and verify the account’s budget and notification recipient. No budget threshold or paid capacity reservation is silently created by these templates. API throttling reduces request spikes; it is not a hard spending cap. Check the [AWS pricing calculator](https://calculator.aws/) for the selected region and current service rates.

CloudWatch logs omit request bodies, authentication tokens and user identifiers from the application’s own error messages. The Lambda error alarm has no email/SNS recipient until one is explicitly configured. Inspect HTTP API 5xx rates as well: handled application errors do not increment Lambda’s Errors metric.

## Short-lived GitHub deployment access

After each stack exists:

```text
python agentu/scripts/setup_release_role.py --stage sandbox
python agentu/scripts/setup_release_role.py --stage sandbox --apply
```

The first command prints the concrete trust and permissions. The applied role allows describing its own stack, updating its Lambda code, writing its own public website prefix and invalidating its own distribution. It cannot manage IAM, modify financial records directly, or create infrastructure.

Create GitHub environments `agentu-sandbox` and `agentu-demo`. Restrict demo deployments to `main`, and sandbox deployments to `main` / `codex/agentu-*`. Set the non-secret environment variable `AWS_RELEASE_ROLE_ARN` to the corresponding output. The trust policy binds short-lived credentials to the exact W3C repository and named environment. No long-lived AWS secret is needed.

The **Release Agentu** workflow runs tests and template validation before assuming the environment’s release role. It is manually dispatched. The **Agentu checks** workflow runs on relevant branches and pull requests.

## Smoke tests before handover

1. Both stack states are `CREATE_COMPLETE` or `UPDATE_COMPLETE`.
2. Website, demo assets and configuration load over HTTPS.
3. `/api/health` succeeds and identifies the intended environment.
4. `/api/state` rejects a request with no token.
5. Invited-user sign-in completes through Cognito and returns to the correct demo URL.
6. Run the three scenarios, approve the pending transfer and export the 10-record audit.
7. A second user or workspace cannot read the first one’s state.
8. Confirm private S3 access is denied and the CloudFront security headers are present.
9. Validate the GitHub release role and run a sandbox release; record the exact deployed revision.
10. Verify cost notifications, operational monitoring and local rehearsal recovery.

## Rollback and recovery

For a code rollback, release the previously tested Git commit through the same workflow. The release script uses Lambda’s revision guard and checks that the deployed backend hash matches the website build before publishing it. CloudFront invalidation refreshes the public assets.

For infrastructure changes, use a new reviewed CloudFormation change set. A failed deployment should be diagnosed from the named stack’s events. Do not delete and recreate the account or unrelated resources.

Website buckets, user pools and records tables use retention on stack deletion/replacement. A later cleanup is a separate explicit action; deleting a stack does not erase the retained data. DynamoDB point-in-time recovery and S3 versions provide recovery primitives, but no restore drill has been completed until it is recorded in readiness.md.

## Sources used for the infrastructure

- [Lambda runtime support](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html)
- [API Gateway JWT authoriser configuration](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-apigatewayv2-authorizer.html)
- [Cognito authorisation and PKCE](https://docs.aws.amazon.com/cognito/latest/developerguide/authorization-endpoint.html)
- [CloudFront directory index rewriting](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/example_cloudfront_functions_url_rewrite_single_page_apps_section.html)
