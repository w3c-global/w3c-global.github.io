"""Real HTTP sessions, CSRF boundary, identity binding and isolated data."""
import json
import sys
import tempfile
import threading
import unittest
import uuid
from http.cookiejar import CookieJar
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import local
from local_auth import LocalAuth
from platform_core.api import PlatformAPI, cognito_actor
from platform_core.errors import PlatformError
from platform_core.service import PlatformService
from platform_core.store import DocumentStore, SQLiteBackend, DynamoBackend, UnitOfWork


class PlatformHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.documents = DocumentStore(SQLiteBackend(Path(cls.temp.name) / "platform.sqlite3"))
        cls.server = local.ThreadingHTTPServer(("127.0.0.1", 0), local.Server)
        cls.server.auth = LocalAuth(cls.documents)
        cls.server.platform = PlatformAPI(PlatformService(cls.documents))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temp.cleanup()

    def setUp(self):
        self.cookies = CookieJar()
        self.browser = build_opener(HTTPCookieProcessor(self.cookies))

    def call(self, path, body=None, headers=None):
        request = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4()), **(headers or {})})
        try:
            with self.browser.open(request, timeout=5) as result:
                return result.status, json.loads(result.read())
        except HTTPError as result:
            raw = result.read()
            return result.code, json.loads(raw) if raw.startswith(b"{") else raw

    def register(self):
        body = {"email": str(uuid.uuid4()) + "@example.test", "password": "LocalVerificationPassword123!"}
        status, result = self.call("/api/platform-auth/register", body)
        self.assertEqual(200, status)
        return body, result["user"]

    def test_browser_session_registration_login_logout_and_institution(self):
        credentials, user = self.register()
        stored_cookie = list(self.cookies)[0]
        self.assertEqual("Strict", stored_cookie.get_nonstandard_attr("SameSite"))
        self.assertTrue(stored_cookie.has_nonstandard_attr("HttpOnly"))
        status, created = self.call("/api/platform/institutions", {"name": "HTTP institution"})
        self.assertEqual(201, status)
        tenant = created["institution"]["id"]
        self.assertEqual(user, self.call("/api/platform/me")[1]["user"])
        self.assertEqual(200, self.call("/api/platform-auth/logout", {})[0])
        self.assertEqual(401, self.call(f"/api/platform/institutions/{tenant}/overview")[0])
        self.assertEqual(200, self.call("/api/platform-auth/login", credentials)[0])
        self.assertEqual(tenant, self.call("/api/platform/institutions")[1]["items"][0]["id"])

    def test_untrusted_identity_headers_and_body_do_not_grant_access(self):
        spoof = {"Authorization": "Bearer fabricated", "X-User-Id": "owner", "X-Agentu-Role": "owner"}
        self.assertEqual(401, self.call("/api/platform/me", headers=spoof)[0])
        self.register()
        _, created = self.call("/api/platform/institutions", {"name": "Owner binding", "role": "administrator", "sub": "attacker"})
        self.assertNotEqual("attacker", created["membership"]["sub"])
        self.assertEqual("owner", created["membership"]["role"])
        self.assertEqual(403, self.call("/api/platform/institutions/other-institution/overview")[0])

    def test_browser_cross_origin_requests_cannot_create_accounts(self):
        body = {"email": "csrf@example.test", "password": "LocalPasswordForTesting"}
        self.assertEqual(403, self.call("/api/platform-auth/register", body, {"Origin": "https://other.example"})[0])
        self.assertEqual(403, self.call("/api/platform/me", headers={"Host": "rebound.example"})[0])

    def test_passwords_and_sessions_are_hashed_and_login_is_rate_limited(self):
        credentials, user = self.register()
        record = self.documents.transact(lambda tx: tx.get("LOCAL_USER#" + credentials["email"], "META"))
        self.assertNotIn(credentials["password"], json.dumps(record))
        self.assertEqual(64, len(record["hash"]))
        for _ in range(8):
            self.assertEqual(401, self.call("/api/platform-auth/login", {**credentials, "password": "WrongLongPassword!"})[0])
        self.assertEqual(429, self.call("/api/platform-auth/login", credentials)[0])

    def test_request_validation_and_pagination(self):
        self.register()
        self.assertEqual(400, self.call("/api/platform/institutions", [1])[0])
        self.assertEqual(400, self.call("/api/platform/institutions", {"name": "Missing key"}, {"Idempotency-Key": ""})[0])
        _, created = self.call("/api/platform/institutions", {"name": "Validation tests"})
        base = "/api/platform/institutions/" + created["institution"]["id"]
        self.assertEqual(400, self.call(base + "/audit?limit=61")[0])
        self.assertEqual(400, self.call(base + "/audit?after=MEMBER%23other")[0])
        self.assertEqual(404, self.call(base + "/commands/invented", {})[0])


class HostedIdentityTests(unittest.TestCase):
    def event(self):
        return {"headers": {"Authorization": "Bearer test-access-token"}, "requestContext": {"authorizer": {"jwt": {
            "claims": {"sub": "subject-123", "token_use": "access"}}}}}

    def test_get_user_binds_verified_email_to_gateway_subject(self):
        client = Mock()
        client.get_user.return_value = {"UserAttributes": [{"Name": "sub", "Value": "subject-123"},
            {"Name": "email", "Value": "person@example.test"}, {"Name": "email_verified", "Value": "true"}]}
        actor = cognito_actor(self.event(), client)
        self.assertTrue(actor.verified)
        client.get_user.assert_called_once_with(AccessToken="test-access-token")
        client.get_user.return_value["UserAttributes"][0]["Value"] = "another-subject"
        with self.assertRaises(PlatformError):
            cognito_actor(self.event(), client)

    def test_id_token_and_unverified_email_are_rejected(self):
        event = self.event(); event["requestContext"]["authorizer"]["jwt"]["claims"]["token_use"] = "id"
        with self.assertRaises(PlatformError):
            cognito_actor(event, Mock())
        client = Mock()
        client.get_user.return_value = {"UserAttributes": [{"Name": "sub", "Value": "subject-123"}, {"Name": "email", "Value": "x@example.test"}]}
        with self.assertRaises(PlatformError):
            cognito_actor(self.event(), client)

    def test_dynamo_commit_checks_dependencies_and_inserts_atomically(self):
        client = Mock()
        backend = DynamoBackend("platform", client)
        tx = UnitOfWork(backend)
        tx.reads = {("TENANT#1", "MEMBER#a"): (4, {"role": "approver"}), ("TENANT#1", "JOURNAL#1"): (None, None)}
        tx.writes = {("TENANT#1", "JOURNAL#1"): {"balanced": True}}
        backend.commit(tx)
        items = client.transact_write_items.call_args.kwargs["TransactItems"]
        self.assertEqual(2, len(items))
        self.assertEqual("#version = :version", items[0]["ConditionCheck"]["ConditionExpression"])
        self.assertEqual("attribute_not_exists(pk)", items[1]["Put"]["ConditionExpression"])
        self.assertEqual("1", items[1]["Put"]["Item"]["version"]["N"])
