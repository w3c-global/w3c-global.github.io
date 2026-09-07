"""Local rehearsal: python agentu/backend/local.py --port 4321. Loopback only."""
import argparse
import hashlib
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote
import handler as api
from storage import FileStore
from local_auth import LocalAuth
from platform_core.api import PlatformAPI, AgentAPI, payload, response
from platform_core.errors import PlatformError
from platform_core.accounting import AccountingService
from platform_core.runner import Runner
from platform_core.store import DocumentStore, SQLiteBackend

ROOT = Path(__file__).resolve().parents[2]


class Server(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, *_):
        pass

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path.startswith("/api/"):
            return self.api("GET")
        if path in ("/agentu/demo/config.json", "/agentu/app/config.json"):
            recovery = getattr(self.server, "recovery_mode", False)
            data = json.dumps({"mode": "local", "apiBase": "", "environment": "Recovery inspection" if recovery else "Local development", "readOnly": recovery}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        # The rehearsal server serves only the intended public site, never source or credentials.
        relative = Path(path.lstrip("/"))
        resolved = (ROOT / relative).resolve()
        allowed = ROOT / "agentu"
        if not resolved.is_relative_to(allowed) or any(p.startswith(".") for p in relative.parts) or any(p in ("backend", "tests", "infra", "scripts", "docs") for p in relative.parts) or (resolved.is_file() and resolved.suffix not in (".html", ".css", ".js", ".json", ".svg", ".txt")):
            self.send_error(404)
            return
        super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/"):
            self.send_error(404)
            return
        self.api("POST")

    def api(self, method):
        # Reject cross-origin/browser DNS-rebinding requests even on loopback.
        host = self.headers.get("Host", "")
        allowed = (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")
        if host not in allowed or self.headers.get("Origin", "http://" + host) != "http://" + host:
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 32768:
                self.send_error(413)
                return
            body = self.rfile.read(length).decode() if length else None
        except (ValueError, UnicodeError):
            self.send_error(400)
            return
        parsed = urlparse(self.path)
        event = {"rawPath": parsed.path, "rawQueryString": parsed.query, "headers": dict(self.headers), "body": body,
                 "requestContext": {"http": {"method": method}}}
        try:
            recovery = getattr(self.server, "recovery_mode", False)
            allowed_recovery = (method == "GET" and parsed.path.startswith("/api/platform/")) or (method == "POST" and parsed.path in ("/api/platform-auth/login", "/api/platform-auth/logout"))
            if recovery and not allowed_recovery:
                result = response(403, {"error": "This restored copy is for inspection. Changes and background execution are paused.", "code": "recovery_read_only"})
            elif parsed.path.startswith("/api/platform-auth/"):
                if method != "POST":
                    result = response(405, {"error": "Use POST."})
                elif parsed.path in ("/api/platform-auth/login", "/api/platform-auth/register"):
                    value, session_cookie = self.server.auth.authenticate(payload(event), parsed.path.endswith("/register"))
                    result = response(200, value)
                    result["headers"]["Set-Cookie"] = session_cookie
                elif parsed.path == "/api/platform-auth/logout":
                    result = response(200, {"signed_out": True})
                    result["headers"]["Set-Cookie"] = self.server.auth.logout(dict(self.headers))
                else:
                    result = response(404, {"error": "Route not found."})
            elif parsed.path.startswith("/api/agent/"):
                result = self.server.agents.handle(event)
            elif parsed.path.startswith("/api/platform/"):
                result = self.server.platform.handle(event, self.server.auth.actor(dict(self.headers)))
                if recovery and method == "GET" and parsed.path.endswith("/overview") and result["statusCode"] == 200:
                    value = json.loads(result["body"])
                    value["permissions"] = []
                    result = response(200, value)
            else:
                event["requestContext"]["authorizer"] = {"jwt": {"claims": {"sub": "local-presenter"}}}
                result = api.handler(event, None)
        except PlatformError as exc:
            result = response(exc.status, {"error": str(exc), "code": exc.code})
        except (ValueError, UnicodeError):
            result = response(400, {"error": "Invalid JSON request."})
        except Exception:
            result = response(500, {"error": "The service could not complete the request. Retry with the same request key."})
        data = result["body"].encode()
        self.send_response(result["statusCode"])
        for k, v in result["headers"].items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Navigation or an interrupted response does not undo a committed
            # command. Retrying its key retrieves the persistent receipt.
            pass


def create_server(port, platform_db=None, recovery=False):
    default = ROOT / ".local-platform" / "platform.sqlite3"
    database = Path(platform_db or default).resolve()
    primary = database == default.resolve() or (database.is_file() and default.is_file() and database.samefile(default))
    if recovery and (not database.is_file() or primary):
        raise ValueError("Recovery inspection needs a separate existing restored database.")
    documents = DocumentStore(SQLiteBackend(database))
    service = AccountingService(documents)
    server = ThreadingHTTPServer(("127.0.0.1", port), Server)
    server.recovery_mode = recovery
    server.platform = PlatformAPI(service)
    server.agents = AgentAPI(service)
    cookie_name = "agentu_recovery_" + hashlib.sha256(str(database).encode()).hexdigest()[:12] if recovery else "agentu_local_session"
    server.auth = LocalAuth(documents, cookie_name)
    return server, service


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4321)
    parser.add_argument("--platform-db", type=Path, help="Separate institution database for a local recovery inspection")
    parser.add_argument("--recovery", action="store_true", help="Inspect a restored copy; deny domain writes and do not start the worker")
    args = parser.parse_args()
    api.store = FileStore(ROOT / ".local-demo")
    server, service = create_server(args.port, args.platform_db, args.recovery)
    stop = threading.Event()
    runner = Runner(service)
    def work():
        while not stop.is_set():
            try:
                runner.tick(seconds=40)
            except Exception:
                print(json.dumps({"error": "local_worker_failed"}), flush=True)
            stop.wait(2)
    if not args.recovery:
        threading.Thread(target=work, daemon=True).start()
    print(f"Agentu {'recovery inspection (changes and worker paused)' if args.recovery else 'local rehearsal'}: http://127.0.0.1:{args.port}/agentu/", flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
