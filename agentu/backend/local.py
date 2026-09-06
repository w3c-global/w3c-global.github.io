"""Local rehearsal: python agentu/backend/local.py --port 4321. Loopback only."""
import argparse
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
            data = json.dumps({"mode": "local", "apiBase": "", "environment": "Local development"}).encode()
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
            if parsed.path.startswith("/api/platform-auth/"):
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4321)
    args = parser.parse_args()
    api.store = FileStore(ROOT / ".local-demo")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Server)
    documents = DocumentStore(SQLiteBackend(ROOT / ".local-platform" / "platform.sqlite3"))
    service = AccountingService(documents)
    server.platform = PlatformAPI(service)
    server.agents = AgentAPI(service)
    server.auth = LocalAuth(documents)
    stop = threading.Event()
    runner = Runner(service)
    def work():
        while not stop.is_set():
            try:
                runner.tick(seconds=40)
            except Exception:
                print(json.dumps({"error": "local_worker_failed"}), flush=True)
            stop.wait(2)
    threading.Thread(target=work, daemon=True).start()
    print(f"Agentu local rehearsal: http://127.0.0.1:{args.port}/agentu/", flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
