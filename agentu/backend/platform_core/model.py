import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from .errors import PlatformError

CURRENCIES = {"GBP", "EUR", "USD"}
ROLES = {"owner", "administrator", "operator", "approver", "auditor"}


@dataclass(frozen=True)
class Actor:
    sub: str
    email: str
    verified: bool = False


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id():
    return str(uuid.uuid4())


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(document):
    return hashlib.sha256(canonical(document).encode()).hexdigest()


def text(value, name, minimum=1, maximum=200):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise PlatformError(f"{name} must contain {minimum}–{maximum} characters.")
    return value.strip()


def identifier(value, name="Identifier"):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value):
        raise PlatformError(f"{name} is invalid.")
    return value


def amount(value, name="Amount", minimum=1):
    if type(value) is not int or not minimum <= value <= 100_000_000_000:
        raise PlatformError(f"{name} must be a whole number of minor units between {minimum} and 100,000,000,000.")
    return value


def currency(value):
    if not isinstance(value, str) or value not in CURRENCIES:
        raise PlatformError("Supported currencies are GBP, EUR and USD.")
    return value


def email(value):
    value = text(value, "Email", 3, 254).lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise PlatformError("A valid email address is required.")
    return value
