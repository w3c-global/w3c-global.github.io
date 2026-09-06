import base64
import json
import os
import re
from domain import DomainError, apply, public_state
from storage import DynamoStore

store = None


def response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff"}, "body": json.dumps(body)}


def handler(event, context):
    global store
    path = event.get("rawPath", "")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if path == "/api/health" and method == "GET":
        return response(200, {"status": "ok", "service": "agentu-demo", "environment": os.getenv("STAGE", "local"), "simulated": True})
    actor = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {}).get("sub")
    if not actor:
        return response(401, {"error": "Sign in to use the hosted demonstration."})
    headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
    workspace = headers.get("x-agentu-workspace", "")
    if not re.fullmatch(r"[a-f0-9-]{36}", workspace):
        return response(400, {"error": "A valid rehearsal identifier is required."})
    try:
        if path not in ("/api/state", "/api/actions", "/api/export"):
            return response(404, {"error": "Route not found."})
        if store is None:
            store = DynamoStore(os.environ["TABLE_NAME"])
        key = f"{actor}#{workspace}"
        if path in ("/api/state", "/api/export") and method == "GET":
            state = store.transact(key)
        elif path == "/api/actions" and method == "POST":
            raw = event.get("body") or "{}"
            if event.get("isBase64Encoded"):
                raw = base64.b64decode(raw).decode()
            if len(raw.encode()) > 8192:
                return response(413, {"error": "Request is too large."})
            if headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                return response(415, {"error": "Send application/json."})
            payload = json.loads(raw)
            state = store.transact(key, lambda s: apply(s, payload, actor))
        else:
            return response(405, {"error": "Method not allowed."})
        return response(200, public_state(state))
    except DomainError as exc:
        return response(exc.status, {"error": str(exc)})
    except (ValueError, UnicodeError):
        return response(400, {"error": "Invalid JSON request."})
    except Exception:
        # No request bodies, tokens, user identifiers or full tracebacks in shared logs.
        print(json.dumps({"error": "request_failed", "request_id": getattr(context, "aws_request_id", "local")}))
        return response(500, {"error": "The demo service could not complete the request. Retry in a moment."})
