"""Loopback development identities. Never packaged or enabled in AWS.

Local email ownership is assumed for development only. Hosted identity and email
verification come exclusively from Cognito. No passwords or tokens are logged.
"""
import hashlib
import hmac
import secrets
import time
from http.cookies import SimpleCookie
from platform_core.errors import PlatformError
from platform_core.model import Actor, email, new_id

COOKIE = "agentu_local_session"
LIFETIME = 8 * 60 * 60


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32).hex()


def cookie(value, age=LIFETIME):
    # HTTP is accepted only on loopback; the hosted app uses Cognito, not this cookie.
    return f"{COOKIE}={value}; HttpOnly; SameSite=Strict; Path=/api/; Max-Age={age}"


class LocalAuth:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def token_hash(headers):
        parsed = SimpleCookie()
        try:
            parsed.load(headers.get("Cookie", headers.get("cookie", "")))
        except Exception:
            return None
        token = parsed.get(COOKIE)
        return hashlib.sha256(token.value.encode()).hexdigest() if token else None

    def actor(self, headers):
        token = self.token_hash(headers)
        if not token:
            return None
        def operation(tx):
            session = tx.get("LOCAL_SESSION#" + token, "META")
            if not session or session["expires_at"] <= time.time():
                return None
            user = tx.get("LOCAL_USER#" + session["email"], "META")
            if not user:
                return None
            return Actor(user["sub"], user["email"], True)
        return self.store.transact(operation)

    def authenticate(self, body, register=False):
        address = email(body.get("email"))
        password = body.get("password")
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            raise PlatformError("Use a password with 12–128 characters.")
        salt = secrets.token_hex(16)
        hashed = password_hash(password, salt)
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        def operation(tx):
            pk = "LOCAL_USER#" + address
            user = tx.get(pk, "META")
            attempts = tx.get(pk, "ATTEMPTS") or {"count": 0, "until": 0}
            if attempts["until"] <= time.time():
                attempts = {"count": 0, "until": int(time.time()) + 900}
            if attempts["count"] >= 8:
                return {"error": "Too many attempts. Try again in 15 minutes.", "status": 429}
            if register and user is None:
                user = {"sub": new_id(), "email": address, "salt": salt, "hash": hashed}
                tx.put(pk, "META", user, insert_only=True)
            elif register or user is None or not hmac.compare_digest(password_hash(password, user["salt"]), user["hash"]):
                attempts["count"] += 1
                tx.put(pk, "ATTEMPTS", attempts)
                return {"error": "Sign-in failed. Check your details or use your existing account.", "status": 401}
            tx.put(pk, "ATTEMPTS", {"count": 0, "until": 0})
            tx.put("LOCAL_SESSION#" + token_hash, "META", {"email": address, "expires_at": int(time.time()) + LIFETIME}, insert_only=True)
            return {"user": {"sub": user["sub"], "email": address}, "development": True}
        result = self.store.transact(operation)
        if "error" in result:
            raise PlatformError(result["error"], result["status"], "sign_in_failed")
        return result, cookie(token)

    def logout(self, headers):
        token = self.token_hash(headers)
        if token:
            def operation(tx):
                session = tx.get("LOCAL_SESSION#" + token, "META")
                if session:
                    tx.put("LOCAL_SESSION#" + token, "META", {**session, "expires_at": 0})
            self.store.transact(operation)
        return cookie("", 0)
