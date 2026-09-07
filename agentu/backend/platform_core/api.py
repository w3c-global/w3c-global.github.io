"""HTTP boundary. Identity is supplied by the trusted server, never request JSON."""
import base64
import json
from urllib.parse import parse_qs
from .errors import PlatformError
from .model import Actor, email, identifier


def response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff"}, "body": json.dumps(body)}


def payload(event):
    headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
    if headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise PlatformError("Send application/json.", 415, "content_type")
    raw = event.get("body") or "{}"
    if len(raw) > 48_000:
        raise PlatformError("Request is too large.", 413, "request_too_large")
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw, validate=True).decode()
    if len(raw.encode()) > 32_768:
        raise PlatformError("Request is too large.", 413, "request_too_large")
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise PlatformError("A JSON object is required.")
    return body


def cognito_actor(event, client=None):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
    authorization = headers.get("authorization", "")
    if not claims.get("sub") or claims.get("token_use") != "access" or not authorization.startswith("Bearer "):
        raise PlatformError("Sign in to Agentu.", 401, "unauthenticated")
    if client is None:
        import boto3
        client = boto3.client("cognito-idp")
    try:
        attributes = {a["Name"]: a["Value"] for a in client.get_user(AccessToken=authorization[7:])["UserAttributes"]}
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code in {"NotAuthorizedException", "UserNotFoundException", "UserNotConfirmedException", "PasswordResetRequiredException"}:
            raise PlatformError("Your session is no longer valid. Sign in again.", 401, "unauthenticated") from exc
        raise
    if attributes.get("sub") != claims["sub"] or attributes.get("email_verified") != "true":
        raise PlatformError("A verified account email is required.", 403, "email_unverified")
    return Actor(identifier(claims["sub"], "User subject"), email(attributes.get("email")), True)


class PlatformAPI:
    def __init__(self, service):
        self.service = service

    def handle(self, event, actor):
        try:
            if not isinstance(actor, Actor) or actor.kind != "human":
                raise PlatformError("Sign in to Agentu.", 401, "unauthenticated")
            if not actor.verified:
                raise PlatformError("Verify your account email.", 403, "email_unverified")
            method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
            path = event.get("rawPath", "")
            parts = path.strip("/").split("/")
            query = parse_qs(event.get("rawQueryString", ""), max_num_fields=10)
            after = query.get("after", [None])[0]
            headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
            if method == "GET" and path == "/api/platform/me":
                return response(200, {"user": {"sub": actor.sub, "email": actor.email}})
            if method == "GET" and path == "/api/platform/capabilities":
                return response(200, self.service.capabilities() if hasattr(self.service, "capabilities") else {})
            if path == "/api/platform/institutions":
                if method == "GET":
                    return response(200, self.service.institutions(actor, after))
                if method == "POST":
                    return response(201, self.service.create_institution(actor, payload(event), headers.get("idempotency-key")))
            if path == "/api/platform/invitations/accept" and method == "POST":
                return response(200, self.service.accept_invitation(actor, payload(event).get("token")))
            if len(parts) >= 4 and parts[:3] == ["api", "platform", "institutions"]:
                tenant_id = identifier(parts[3], "Institution ID")
                if method == "GET" and len(parts) == 5:
                    if parts[4] == "overview":
                        return response(200, self.service.overview(tenant_id, actor))
                    limit = int(query.get("limit", ["40"])[0])
                    return response(200, self.service.collection(tenant_id, actor, parts[4], after, limit))
                if method == "GET" and len(parts) in (6, 7) and parts[4] == "reconciliations" and hasattr(self.service, "reconciliation"):
                    return response(200, self.service.reconciliation(tenant_id, actor, parts[5], parts[6] if len(parts) == 7 else None, after, int(query.get("limit", ["40"])[0])))
                if method == "GET" and len(parts) == 6 and parts[4] == "actions":
                    return response(200, self.service.action(tenant_id, actor, parts[5]))
                if method == "POST" and len(parts) == 6 and parts[4] == "commands":
                    return response(200, self.service.command(tenant_id, actor, parts[5], payload(event), headers.get("idempotency-key")))
            return response(404, {"error": "Route not found.", "code": "not_found"})
        except PlatformError as exc:
            return response(exc.status, {"error": str(exc), "code": exc.code})
        except (ValueError, UnicodeError):
            return response(400, {"error": "Invalid request encoding, JSON or query.", "code": "invalid_request"})


class AgentAPI:
    def __init__(self, service):
        self.service = service

    def handle(self, event):
        method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
        if event.get("rawPath") != "/api/agent/proposals" or method != "POST":
            return response(404, {"error": "Agent route not found.", "code": "not_found"})
        try:
            headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
            authorization = headers.get("authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else ""
            return response(200, self.service.submit_agent(token, payload(event), headers.get("idempotency-key")))
        except PlatformError as exc:
            return response(exc.status, {"error": str(exc), "code": exc.code})
        except (ValueError, UnicodeError):
            return response(400, {"error": "Invalid JSON request.", "code": "invalid_request"})
