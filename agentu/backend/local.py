"""Local rehearsal: python agentu/backend/local.py --port 4321. Loopback only."""
import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote
import handler as api
from storage import FileStore

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
        if path == "/agentu/demo/config.json":
            data = json.dumps({"mode": "local", "apiBase": "", "environment": "Local rehearsal"}).encode()
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
            if length < 0 or length > 8192:
                self.send_error(413)
                return
            body = self.rfile.read(length).decode() if length else None
        except (ValueError, UnicodeError):
            self.send_error(400)
            return
        result = api.handler({"rawPath": urlparse(self.path).path, "headers": dict(self.headers), "body": body,
                "requestContext": {"http": {"method": method}, "authorizer": {"jwt": {"claims": {"sub": "local-presenter"}}}}}, None)
        data = result["body"].encode()
        self.send_response(result["statusCode"])
        for k, v in result["headers"].items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4321)
    args = parser.parse_args()
    api.store = FileStore(ROOT / ".local-demo")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Server)
    print(f"Agentu local rehearsal: http://127.0.0.1:{args.port}/agentu/", flush=True)
    server.serve_forever()
