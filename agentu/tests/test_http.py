"""Exercise the real HTTP adapter, including the public-file boundary."""
import json
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import local
from storage import FileStore


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        local.api.store = FileStore(cls.folder.name)
        cls.server = local.ThreadingHTTPServer(("127.0.0.1", 0), local.Server)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.folder.cleanup()

    def call(self, path, body=None, headers=None):
        request = Request(self.base + path, data=json.dumps(body).encode() if body else None,
                          headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(request, timeout=5) as result:
                return result.status, result.read()
        except HTTPError as error:
            return error.code, error.read()

    def test_static_pages_and_local_config(self):
        self.assertEqual(self.call("/agentu/")[0], 200)
        self.assertEqual(self.call("/agentu/demo/")[0], 200)
        self.assertEqual(json.loads(self.call("/agentu/demo/config.json")[1])["mode"], "local")

    def test_source_and_encoded_paths_are_not_served(self):
        for path in ("/.git/config", "/agentu/backend/domain.py", "/agentu/%62ackend/domain.py", "/agentu/%2e%2e/.git/config", "/.local-demo/"):
            with self.subTest(path=path):
                self.assertEqual(self.call(path)[0], 404)

    def test_cross_origin_and_rebinding_requests_rejected(self):
        self.assertEqual(self.call("/api/state", headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.call("/api/health", headers={"Host": "attacker.example"})[0], 403)

    def test_end_to_end_three_scenarios_and_approval(self):
        headers = {"X-Agentu-Workspace": str(uuid.uuid4())}
        for scenario, expected in (("sweep", "executed"), ("blocked", "blocked"), ("approval", "pending")):
            status, body = self.call("/api/actions", {"operation": "propose", "scenario": scenario, "request_id": str(uuid.uuid4())}, headers)
            self.assertEqual(status, 200)
            state = json.loads(body)
            self.assertEqual(state["actions"][-1]["status"], expected)
        action = state["actions"][-1]
        _, body = self.call("/api/actions", {"operation": "approve", "action_id": action["id"], "note": "Reviewed in HTTP rehearsal", "request_id": str(uuid.uuid4())}, headers)
        state = json.loads(body)
        self.assertEqual(state["accounts"]["operating"]["balance"], 225000000)
        self.assertEqual(state["accounts"]["reserve"]["balance"], 115000000)
        self.assertEqual(state["actions"][-1]["status"], "executed")
        exported = json.loads(self.call("/api/export", headers=headers)[1])
        self.assertEqual(exported["events"], state["events"])
        self.assertEqual(exported["integrity"]["count"], 10)
