"""Institution-owned operations. All mutations are atomic and auditable.

External network execution belongs in an outbox worker, never in these retryable
transactions. Internal ledger transfers are accounting entries, not bank calls.
"""
import copy
import hashlib
import secrets
import time
from datetime import datetime, timezone
from .errors import PlatformError
from .model import Actor, ROLES, amount, canonical, currency, digest, email, identifier, new_id, now, text
from .jobs import enqueue

ZERO = "0" * 64
MANAGERS = {"owner", "administrator"}
PERMISSIONS = {
    "account_create": MANAGERS, "account_status": MANAGERS, "sandbox_fund": {"owner"},
    "invite_create": MANAGERS, "invite_revoke": MANAGERS, "member_update": {"owner"},
    "policy_create": MANAGERS, "policy_publish": MANAGERS, "institution_pause": MANAGERS,
    "action_propose": {"owner", "operator"}, "action_approve": {"owner", "approver"},
    "reversal_propose": {"owner", "operator"},
    "action_decline": {"owner", "approver"}, "action_cancel": {"owner", "administrator", "operator"},
    "action_expire": ROLES,
}
COLLECTIONS = {"accounts": "ACCOUNT#", "actions": "ACTION#", "members": "MEMBER#", "invitations": "INVITATION#",
               "policies": "POLICY#", "journal": "JOURNAL#", "audit": "EVENT#"}


def tenant_key(tenant_id):
    return "TENANT#" + identifier(tenant_id, "Institution ID")


def required(tx, pk, sk):
    result = tx.get(pk, sk)
    if result is None:
        raise PlatformError("Record not found in this institution.", 404, "not_found")
    return result


def access(tx, tenant_id, actor, roles=ROLES):
    pk = tenant_key(tenant_id)
    member = tx.get(pk, "MEMBER#" + actor.sub)
    if not member or member["status"] != "active" or member["role"] not in roles:
        raise PlatformError("Your membership does not permit this operation.", 403, "forbidden")
    tenant = required(tx, pk, "META")
    return pk, tenant, member


def audit(tx, pk, actor, kind, data):
    tenant = required(tx, pk, "META")
    sequence = tenant["event_sequence"] + 1
    event = {"sequence": sequence, "timestamp": now(), "actor": actor.sub, "kind": kind,
             "data": copy.deepcopy(data), "previous_hash": tenant["event_head"]}
    event["hash"] = digest(event)
    tx.put(pk, f"EVENT#{sequence:020d}", event, insert_only=True)
    tenant.update(event_sequence=sequence, event_head=event["hash"], updated_at=event["timestamp"])
    tx.put(pk, "META", tenant)


def validate_policy(body):
    if not isinstance(body, dict):
        raise PlatformError("Policy configuration must be an object.")
    auto = amount(body.get("auto_limit"), "Automatic limit", 0)
    maximum = amount(body.get("transaction_limit"), "Transaction limit")
    daily = amount(body.get("daily_limit"), "Daily limit")
    floor = amount(body.get("liquidity_floor"), "Liquidity floor", 0)
    approvals = body.get("required_approvals", 1)
    minutes = body.get("approval_minutes", 60)
    currencies = body.get("currencies", ["GBP"])
    if not (auto <= maximum <= daily):
        raise PlatformError("Limits must satisfy automatic ≤ transaction ≤ daily.")
    if type(approvals) is not int or not 1 <= approvals <= 3:
        raise PlatformError("Require one, two or three independent approvals.")
    if type(minutes) is not int or not 5 <= minutes <= 1440:
        raise PlatformError("Approval expiry must be between 5 and 1,440 minutes.")
    if not isinstance(currencies, list) or not 1 <= len(currencies) <= 3 or any(not isinstance(c, str) for c in currencies) or len(set(currencies)) != len(currencies):
        raise PlatformError("Select one to three distinct currencies.")
    for code in currencies:
        currency(code)
    return {"auto_limit": auto, "transaction_limit": maximum, "daily_limit": daily,
            "liquidity_floor": floor, "required_approvals": approvals, "approval_minutes": minutes, "currencies": currencies}


class PlatformService:
    permissions = PERMISSIONS
    collections = COLLECTIONS
    def __init__(self, store):
        self.store = store

    def create_institution(self, actor, body, request_id):
        if not actor.verified:
            raise PlatformError("Verify your account email before creating an institution.", 403, "email_unverified")
        if not isinstance(body, dict):
            raise PlatformError("A JSON object is required.")
        name = text(body.get("name"), "Institution name", 2, 120)
        code = currency(body.get("currency", "GBP"))
        identifier(request_id, "Idempotency key")
        if len(request_id) < 16:
            raise PlatformError("Use an idempotency key of at least 16 characters.")
        key = "CREATE#" + request_id
        fingerprint = digest(body)

        def operation(tx):
            existing = tx.get("USER#" + actor.sub, key)
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise PlatformError("Idempotency key was used with different input.", 409, "idempotency_conflict")
                return existing["response"]
            tenant_id = new_id()
            pk = tenant_key(tenant_id)
            policy_id = new_id()
            tenant = {"id": tenant_id, "name": name, "base_currency": code, "mode": "sandbox", "status": "active",
                      "created_at": now(), "created_by": actor.sub, "policy_id": policy_id, "owner_count": 1,
                      "event_sequence": 0, "event_head": ZERO, "ledger_sequence": 0, "account_count": 0,
                      "action_count": 0, "pending_count": 0}
            tx.put(pk, "META", tenant, insert_only=True)
            member = {"sub": actor.sub, "email": actor.email, "role": "owner", "status": "active", "joined_at": now()}
            tx.put(pk, "MEMBER#" + actor.sub, member, insert_only=True)
            tx.put("USER#" + actor.sub, "TENANT#" + tenant_id, {"id": tenant_id, "name": name, "role": "owner", "status": "active"}, insert_only=True)
            # The initial mandate is deliberately restrictive. Any relaxation is
            # versioned and requires a different administrator to publish it.
            config = {"auto_limit": 0, "transaction_limit": 100_000_00, "daily_limit": 1_000_000_00,
                      "liquidity_floor": 0, "required_approvals": 1, "approval_minutes": 60, "currencies": [code]}
            tx.put(pk, "POLICY#" + policy_id, {"id": policy_id, "name": "Initial controlled mandate", "status": "published",
                   "config": config, "created_by": "system", "published_by": "system", "created_at": now(), "published_at": now()}, insert_only=True)
            audit(tx, pk, actor, "institution_created", {"name": name, "mode": "sandbox", "initial_policy": policy_id})
            response = {"institution": required(tx, pk, "META"), "membership": member}
            tx.put("USER#" + actor.sub, key, {"fingerprint": fingerprint, "response": response}, insert_only=True)
            return response
        return self.store.transact(operation)

    def institutions(self, actor, after=None):
        def operation(tx):
            items, cursor = tx.query("USER#" + actor.sub, "TENANT#", after=after)
            return {"items": items, "next_cursor": cursor}
        return self.store.transact(operation)

    def overview(self, tenant_id, actor):
        def operation(tx):
            pk, tenant, member = access(tx, tenant_id, actor)
            policy = required(tx, pk, "POLICY#" + tenant["policy_id"])
            accounts, cursor = tx.query(pk, "ACCOUNT#", limit=40)
            return {"institution": tenant, "membership": member, "policy": policy, "accounts": accounts,
                    "accounts_next_cursor": cursor, "permissions": sorted(k for k, roles in self.permissions.items() if member["role"] in roles)}
        return self.store.transact(operation)

    def collection(self, tenant_id, actor, collection, after=None, limit=40):
        if collection not in self.collections:
            raise PlatformError("Unknown collection.", 404, "not_found")
        def operation(tx):
            pk, tenant, _ = access(tx, tenant_id, actor)
            items, cursor = tx.query(pk, self.collections[collection], after=after, limit=limit)
            if collection in ("invitations", "agent_keys"):
                items = [{k: v for k, v in item.items() if k not in ("token_hash", "secret_hash")} for item in items]
            return {"items": items, "next_cursor": cursor, "as_of_sequence": tenant["event_sequence"], "as_of_head": tenant["event_head"]}
        return self.store.transact(operation)

    def command(self, tenant_id, actor, operation, body, request_id):
        if not isinstance(operation, str) or operation not in self.permissions:
            raise PlatformError("Unknown operation.", 404, "not_found")
        if not isinstance(body, dict):
            raise PlatformError("A JSON object is required.")
        request_id = identifier(request_id, "Idempotency key")
        if len(request_id) < 16:
            raise PlatformError("Use an idempotency key with at least 16 characters.")
        fingerprint = digest({"operation": operation, "body": body})

        def transaction(tx):
            pk, tenant, member = access(tx, tenant_id, actor, self.permissions[operation])
            key = "REQUEST#" + actor.sub + "#" + request_id
            previous = tx.get(pk, key)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise PlatformError("Idempotency key was used with different input.", 409, "idempotency_conflict")
                return previous["response"]
            response = getattr(self, "_" + operation)(tx, pk, tenant, actor, member, body)
            saved = {k: v for k, v in response.items() if k not in ("invite_token", "agent_token")}
            if "invite_token" in response or "agent_token" in response:
                saved["token_shown_once"] = True
            tx.put(pk, key, {"fingerprint": fingerprint, "response": saved, "created_at": now()}, insert_only=True)
            return response
        return self.store.transact(transaction)

    def _account_create(self, tx, pk, tenant, actor, member, body):
        account_id = new_id()
        account = {"id": account_id, "name": text(body.get("name"), "Account name", 2, 100),
                   "currency": currency(body.get("currency", tenant["base_currency"])), "kind": "asset", "normal_side": "debit",
                   "balance": 0, "reserved": 0, "status": "active", "created_at": now(), "created_by": actor.sub}
        tx.put(pk, "ACCOUNT#" + account_id, account, insert_only=True)
        tenant["account_count"] += 1
        tx.put(pk, "META", tenant)
        audit(tx, pk, actor, "account_created", account)
        return {"account": account}

    def _account_status(self, tx, pk, tenant, actor, member, body):
        account_id = identifier(body.get("account_id"))
        account = required(tx, pk, "ACCOUNT#" + account_id)
        status = body.get("status")
        if status not in ("active", "suspended") or account["kind"] != "asset":
            raise PlatformError("An asset account can be active or suspended.")
        account["status"] = status
        tx.put(pk, "ACCOUNT#" + account_id, account)
        audit(tx, pk, actor, "account_status_changed", {"account_id": account_id, "status": status,
              "reason": text(body.get("reason", "Account status updated."), "Status reason", 5, 500)})
        return {"account": account}

    def _journal(self, tx, pk, actor, postings, reference, reason, reversal_of=None):
        totals = {}
        seen = set()
        for posting in postings:
            account_id = posting["account_id"]
            if account_id in seen:
                raise PlatformError("Combine entries for the same account before posting.")
            seen.add(account_id)
            account = required(tx, pk, "ACCOUNT#" + account_id)
            debit, credit = posting["debit"], posting["credit"]
            amount(debit, "Debit", 0)
            amount(credit, "Credit", 0)
            if (debit == 0) == (credit == 0):
                raise PlatformError("A posting must have exactly one positive debit or credit.")
            code = account["currency"]
            if posting["currency"] != code:
                raise PlatformError("Posting currency differs from the account.")
            totals[code] = totals.get(code, 0) + debit - credit
            delta = debit - credit if account["normal_side"] == "debit" else credit - debit
            account["balance"] += delta
            if account["kind"] == "asset" and account["balance"] - account["reserved"] < 0:
                raise PlatformError("The journal would overdraw available funds.", 409, "insufficient_funds")
            tx.put(pk, "ACCOUNT#" + account_id, account)
        if not postings or any(value != 0 for value in totals.values()):
            raise PlatformError("Journal debits and credits must balance in every currency.")
        tenant = required(tx, pk, "META")
        tenant["ledger_sequence"] += 1
        sequence = tenant["ledger_sequence"]
        journal = {"sequence": sequence, "id": new_id(), "timestamp": now(), "actor": actor.sub,
                   "reference": reference, "reason": reason, "postings": postings}
        if reversal_of:
            journal["reversal_of"] = reversal_of
        tx.put(pk, f"JOURNAL#{sequence:020d}", journal, insert_only=True)
        tx.put(pk, "META", tenant)
        return journal

    def _sandbox_fund(self, tx, pk, tenant, actor, member, body):
        if tenant["mode"] != "sandbox":
            raise PlatformError("Manual funding is available only in sandbox institutions.", 403, "sandbox_only")
        target = required(tx, pk, "ACCOUNT#" + identifier(body.get("account_id")))
        if target["kind"] != "asset" or target["status"] != "active":
            raise PlatformError("Choose an active asset account.")
        value = amount(body.get("amount"))
        reason = text(body.get("reason"), "Funding reason", 5, 500)
        equity_id = "sandbox-equity-" + target["currency"]
        if tx.get(pk, "ACCOUNT#" + equity_id) is None:
            tx.put(pk, "ACCOUNT#" + equity_id, {"id": equity_id, "name": "Sandbox funding equity", "kind": "equity",
                   "normal_side": "credit", "balance": 0, "reserved": 0, "currency": target["currency"], "status": "active"}, insert_only=True)
        journal = self._journal(tx, pk, actor, [
            {"account_id": target["id"], "debit": value, "credit": 0, "currency": target["currency"]},
            {"account_id": equity_id, "debit": 0, "credit": value, "currency": target["currency"]}], "sandbox-funding", reason)
        audit(tx, pk, actor, "sandbox_funding_posted", journal)
        return {"journal": journal, "account": required(tx, pk, "ACCOUNT#" + target["id"])}

    def _invite_create(self, tx, pk, tenant, actor, member, body):
        role = body.get("role")
        if not isinstance(role, str) or role not in ROLES or (role in MANAGERS and member["role"] != "owner"):
            raise PlatformError("Only an owner can invite owners or administrators.", 403, "forbidden")
        address = email(body.get("email"))
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        invite = {"id": new_id(), "tenant_id": tenant["id"], "email": address, "role": role,
                  "invited_by": actor.sub, "status": "pending", "expires_at": int(time.time()) + 7 * 86400, "created_at": now(), "token_hash": token_hash}
        tx.put(pk, "INVITATION#" + invite["id"], invite, insert_only=True)
        tx.put("INVITE#" + token_hash, "META", {"tenant_id": tenant["id"], "id": invite["id"]}, insert_only=True)
        public = {k: v for k, v in invite.items() if k != "token_hash"}
        audit(tx, pk, actor, "invitation_created", public)
        return {"invitation": public, "invite_token": token}

    def _invite_revoke(self, tx, pk, tenant, actor, member, body):
        invite = required(tx, pk, "INVITATION#" + identifier(body.get("invitation_id")))
        if invite["role"] in MANAGERS and member["role"] != "owner":
            raise PlatformError("Only an owner can manage this invitation.", 403, "forbidden")
        if invite["status"] != "pending":
            raise PlatformError("Invitation is no longer pending.", 409, "invalid_state")
        invite["status"] = "revoked"
        tx.put(pk, "INVITATION#" + invite["id"], invite)
        audit(tx, pk, actor, "invitation_revoked", {"id": invite["id"], "reason": text(body.get("reason", "Invitation revoked."), "Revocation reason", 5, 500)})
        return {"invitation": {k: v for k, v in invite.items() if k != "token_hash"}}

    def accept_invitation(self, actor, token):
        if not actor.verified:
            raise PlatformError("Verify your account email before accepting an invitation.", 403, "email_unverified")
        token = text(token, "Invitation token", 40, 100)
        hashed = hashlib.sha256(token.encode()).hexdigest()
        def operation(tx):
            pointer = tx.get("INVITE#" + hashed, "META")
            if not pointer:
                raise PlatformError("Invitation is invalid or unavailable.", 404, "invitation_unavailable")
            pk = tenant_key(pointer["tenant_id"])
            invite = required(tx, pk, "INVITATION#" + pointer["id"])
            if invite["email"] != actor.email.lower():
                raise PlatformError("This invitation belongs to a different verified email.", 403, "invitation_mismatch")
            if invite["status"] == "accepted" and invite.get("accepted_by") == actor.sub:
                return {"tenant_id": pointer["tenant_id"]}
            if invite["status"] != "pending" or invite["expires_at"] <= time.time():
                raise PlatformError("Invitation has expired or is no longer pending.", 409, "invitation_unavailable")
            inviter = required(tx, pk, "MEMBER#" + invite["invited_by"])
            allowed = {"owner"} if invite["role"] in MANAGERS else MANAGERS
            if inviter["status"] != "active" or inviter["role"] not in allowed:
                raise PlatformError("The inviter no longer has authority to grant this access.", 403, "invitation_unavailable")
            if tx.get(pk, "MEMBER#" + actor.sub):
                raise PlatformError("A membership already exists. An owner must update it.", 409, "already_member")
            tenant = required(tx, pk, "META")
            member = {"sub": actor.sub, "email": actor.email, "role": invite["role"], "status": "active", "joined_at": now()}
            tx.put(pk, "MEMBER#" + actor.sub, member, insert_only=True)
            tx.put("USER#" + actor.sub, "TENANT#" + tenant["id"], {"id": tenant["id"], "name": tenant["name"], "role": member["role"], "status": "active"}, insert_only=True)
            if member["role"] == "owner":
                tenant["owner_count"] += 1
                tx.put(pk, "META", tenant)
            invite.update(status="accepted", accepted_by=actor.sub, accepted_at=now())
            tx.put(pk, "INVITATION#" + invite["id"], invite)
            audit(tx, pk, actor, "invitation_accepted", {"invitation_id": invite["id"], "role": member["role"]})
            return {"tenant_id": tenant["id"]}
        return self.store.transact(operation)

    def _member_update(self, tx, pk, tenant, actor, member, body):
        subject = identifier(body.get("sub"), "Member subject")
        if subject == actor.sub:
            raise PlatformError("An owner cannot change their own membership.", 409, "self_change")
        target = required(tx, pk, "MEMBER#" + subject)
        if target.get("kind") == "agent":
            raise PlatformError("Manage machine authority through its agent mandate.", 403, "agent_membership")
        role, status = body.get("role", target["role"]), body.get("status", target["status"])
        if not isinstance(role, str) or role not in ROLES or status not in ("active", "suspended"):
            raise PlatformError("Invalid membership role or status.")
        was_owner = target["role"] == "owner" and target["status"] == "active"
        is_owner = role == "owner" and status == "active"
        tenant["owner_count"] += int(is_owner) - int(was_owner)
        if tenant["owner_count"] < 1:
            raise PlatformError("An institution must retain an active owner.", 409, "last_owner")
        target.update(role=role, status=status)
        tx.put(pk, "MEMBER#" + subject, target)
        tx.put(pk, "META", tenant)
        tx.put("USER#" + subject, "TENANT#" + tenant["id"], {"id": tenant["id"], "name": tenant["name"], "role": role, "status": status})
        audit(tx, pk, actor, "membership_changed", {"sub": subject, "role": role, "status": status,
              "reason": text(body.get("reason", "Membership access updated."), "Access change reason", 5, 500)})
        return {"member": target}

    def _policy_create(self, tx, pk, tenant, actor, member, body):
        policy = {"id": new_id(), "name": text(body.get("name"), "Policy name", 2, 120), "status": "draft",
                  "created_by": actor.sub, "created_at": now(), "config": validate_policy(body.get("config", {}))}
        tx.put(pk, "POLICY#" + policy["id"], policy, insert_only=True)
        audit(tx, pk, actor, "policy_drafted", policy)
        return {"policy": policy}

    def _policy_publish(self, tx, pk, tenant, actor, member, body):
        policy = required(tx, pk, "POLICY#" + identifier(body.get("policy_id")))
        if policy["status"] != "draft":
            raise PlatformError("Only a draft policy can be published.", 409, "invalid_state")
        if policy["created_by"] == actor.sub:
            raise PlatformError("A different owner or administrator must publish this policy.", 403, "independent_review_required")
        creator = required(tx, pk, "MEMBER#" + policy["created_by"])
        if creator["status"] != "active" or creator["role"] not in MANAGERS:
            raise PlatformError("The policy author is no longer an active administrator.", 409, "author_inactive")
        reason = text(body.get("reason"), "Publication reason", 5, 500)
        previous = required(tx, pk, "POLICY#" + tenant["policy_id"])
        previous["status"] = "superseded"
        tx.put(pk, "POLICY#" + previous["id"], previous)
        policy.update(status="published", published_by=actor.sub, published_at=now(), publication_reason=reason)
        tx.put(pk, "POLICY#" + policy["id"], policy)
        tenant["policy_id"] = policy["id"]
        tx.put(pk, "META", tenant)
        audit(tx, pk, actor, "policy_published", {"policy": policy, "previous_policy": previous["id"]})
        return {"policy": policy}

    def _institution_pause(self, tx, pk, tenant, actor, member, body):
        status = body.get("status")
        if status not in ("active", "paused"):
            raise PlatformError("Institution status must be active or paused.")
        reason = text(body.get("reason"), "Status reason", 5, 500)
        tenant["status"] = status
        tx.put(pk, "META", tenant)
        audit(tx, pk, actor, "institution_status_changed", {"status": status, "reason": reason})
        return {"institution": required(tx, pk, "META")}

    @staticmethod
    def _usage(tx, pk, date, code):
        key = f"USAGE#{date}#{code}"
        return key, tx.get(pk, key) or {"date": date, "currency": code, "reserved": 0, "executed": 0}

    def _evaluate(self, tx, pk, tenant, action, *, held=False):
        policy = required(tx, pk, "POLICY#" + tenant["policy_id"])
        config = policy["config"]
        source = required(tx, pk, "ACCOUNT#" + action["source_id"])
        target = required(tx, pk, "ACCOUNT#" + action["destination_id"])
        proposer = tx.get(pk, "MEMBER#" + action["proposed_by"])
        date = datetime.now(timezone.utc).date().isoformat()
        _, usage = self._usage(tx, pk, date, action["currency"])
        available = source["balance"] - source["reserved"] + (action["amount"] if held else 0)
        counted = usage["executed"] + usage["reserved"] - (action["amount"] if held and action["usage_date"] == date else 0)
        rules = [
            ("Originator authorised", bool(proposer and proposer["status"] == "active" and proposer["role"] in PERMISSIONS["action_propose"]), "Current institution membership"),
            ("Institution active", tenant["status"] == "active", tenant["status"]),
            ("Accounts active", source["status"] == target["status"] == "active", {"source": source["status"], "destination": target["status"]}),
            ("Eligible ledger accounts", source["kind"] == "asset" and (target["kind"] == "asset" or bool(action.get("reversal_of") and target["id"] == "sandbox-equity-" + action["currency"] and target["kind"] == "equity")), {"source": source["kind"], "destination": target["kind"]}),
            ("Currency mandate", action["currency"] in config["currencies"] and source["currency"] == target["currency"] == action["currency"], config["currencies"]),
            ("Transaction limit", action["amount"] <= config["transaction_limit"], config["transaction_limit"]),
            ("Daily limit", counted + action["amount"] <= config["daily_limit"], {"limit": config["daily_limit"], "committed_and_reserved": counted}),
            ("Liquidity floor", available - action["amount"] >= config["liquidity_floor"], {"available": available, "floor": config["liquidity_floor"]}),
        ]
        if action.get("reversal_of"):
            original = required(tx, pk, f"JOURNAL#{action['reversal_of']['sequence']:020d}")
            link = required(tx, pk, "REVERSAL#" + original["id"])
            rules.append(("Original journal and reversal claim", digest(original) == action["reversal_of"]["hash"] and link["action_id"] == action["id"], action["reversal_of"]))
        rules.extend(self._agent_checks(tx, pk, action, held=held))
        checks = [{"rule": name, "result": "pass" if passed else "fail", "evidence": evidence} for name, passed, evidence in rules]
        automatic_review = action["amount"] > config["auto_limit"]
        agent_review = self._agent_review(tx, pk, action)
        review = automatic_review or agent_review or bool(action.get("reversal_of"))
        if action.get("reversal_of"):
            checks.append({"rule": "Independent correction review", "result": "review", "evidence": "Every journal reversal requires independent approval."})
        checks.append({"rule": "Automatic limit", "result": "review" if automatic_review else "pass", "evidence": config["auto_limit"]})
        if action.get("agent_id"):
            checks.append({"rule": "Agent independent review", "result": "review" if agent_review else "pass", "evidence": {"required": agent_review}})
        decision = "blocked" if any(c["result"] == "fail" for c in checks) else "pending" if review else "allowed"
        return policy, checks, decision

    def _reserve(self, tx, pk, action):
        source = required(tx, pk, "ACCOUNT#" + action["source_id"])
        source["reserved"] += action["amount"]
        key, usage = self._usage(tx, pk, action["usage_date"], action["currency"])
        usage["reserved"] += action["amount"]
        tx.put(pk, "ACCOUNT#" + source["id"], source)
        tx.put(pk, key, usage)
        self._agent_meter(tx, pk, action, action["usage_date"], "reserved", action["amount"])
        action["reserved"] = True

    def _release(self, tx, pk, action):
        if not action["reserved"]:
            return
        source = required(tx, pk, "ACCOUNT#" + action["source_id"])
        key, usage = self._usage(tx, pk, action["usage_date"], action["currency"])
        if source["reserved"] < action["amount"] or usage["reserved"] < action["amount"]:
            raise PlatformError("Reservation accounting is inconsistent. Execution has stopped.", 409, "reservation_inconsistent")
        source["reserved"] -= action["amount"]
        usage["reserved"] -= action["amount"]
        tx.put(pk, "ACCOUNT#" + source["id"], source)
        tx.put(pk, key, usage)
        self._agent_meter(tx, pk, action, action["usage_date"], "reserved", -action["amount"])
        if action.get("expiry_job_key"):
            tx.delete_work(action["expiry_job_key"])
        action["reserved"] = False

    def _settle(self, tx, pk, actor, action):
        self._release(tx, pk, action)
        journal = self._journal(tx, pk, actor, [
            {"account_id": action["source_id"], "debit": 0, "credit": action["amount"], "currency": action["currency"]},
            {"account_id": action["destination_id"], "debit": action["amount"], "credit": 0, "currency": action["currency"]}], action["id"], action["purpose"], action.get("reversal_of"))
        date = datetime.now(timezone.utc).date().isoformat()
        key, usage = self._usage(tx, pk, date, action["currency"])
        usage["executed"] += action["amount"]
        tx.put(pk, key, usage)
        self._agent_meter(tx, pk, action, date, "executed", action["amount"])
        action.update(status="settled", settled_at=now(), journal_sequence=journal["sequence"])
        audit(tx, pk, actor, "journal_reversal_posted" if action.get("reversal_of") else "internal_transfer_posted", journal)

    def _action_propose(self, tx, pk, tenant, actor, member, body, *, reversal=None):
        source_id = identifier(body.get("source_id"), "Source account")
        destination_id = identifier(body.get("destination_id"), "Destination account")
        if source_id == destination_id:
            raise PlatformError("Source and destination must differ.")
        source = required(tx, pk, "ACCOUNT#" + source_id)
        target = required(tx, pk, "ACCOUNT#" + destination_id)
        action = {"id": new_id(), "kind": "internal_transfer", "source_id": source_id, "source_name": source["name"],
                  "destination_id": destination_id, "destination_name": target["name"], "currency": source["currency"],
                  "amount": amount(body.get("amount")), "purpose": text(body.get("purpose"), "Purpose", 5, 500),
                  "created_at": now(), "proposed_by": actor.sub, "initiated_by": actor.initiated_by, "approvals": [], "reserved": False,
                  "usage_date": datetime.now(timezone.utc).date().isoformat(), "mode": tenant["mode"]}
        if reversal:
            action.update(kind="ledger_reversal", reversal_of=reversal)
            tx.put(pk, "REVERSAL#" + reversal["id"], {"action_id": action["id"], "original_sequence": reversal["sequence"]})
        if member.get("kind") == "agent":
            action.update(agent_id=member["agent_id"], agent_revision=member["agent_revision"], credential_id=actor.credential_id)
        policy, checks, decision = self._evaluate(tx, pk, tenant, action)
        action.update(policy_id=policy["id"], policy_snapshot=policy["config"], checks=checks, status=decision,
                      expires_at=int(time.time()) + policy["config"]["approval_minutes"] * 60)
        audit(tx, pk, actor, "action_proposed", copy.deepcopy(action))
        if decision == "allowed":
            self._settle(tx, pk, actor, action)
        elif decision == "pending":
            self._reserve(tx, pk, action)
            action["expiry_job_key"] = enqueue(tx, "action_expire", tenant["id"], action["id"], due=action["expires_at"])
        tx.put(pk, "ACTION#" + action["id"], action, insert_only=True)
        current = required(tx, pk, "META")
        current["action_count"] += 1
        current["pending_count"] += int(action["status"] == "pending")
        tx.put(pk, "META", current)
        return {"action": action}

    def _reversal_propose(self, tx, pk, tenant, actor, member, body):
        if actor.kind != "human":
            raise PlatformError("Journal corrections require a human identity.", 403, "forbidden")
        sequence = amount(body.get("journal_sequence"), "Journal sequence")
        journal = required(tx, pk, f"JOURNAL#{sequence:020d}")
        if journal.get("reversal_of"):
            raise PlatformError("This entry already corrects an earlier journal. Use a new governed transfer or sandbox funding entry to record a subsequent adjustment.", 409, "correction_entry")
        link = tx.get(pk, "REVERSAL#" + journal["id"])
        if link:
            prior = required(tx, pk, "ACTION#" + link["action_id"])
            if prior["status"] in ("pending", "settled"):
                raise PlatformError("This journal already has a pending or posted reversal.", 409, "reversal_exists")
        postings = journal["postings"]
        debits = [p for p in postings if p["debit"] > 0 and p["credit"] == 0]
        credits = [p for p in postings if p["credit"] > 0 and p["debit"] == 0]
        if len(postings) != 2 or len(debits) != 1 or len(credits) != 1 or debits[0]["debit"] != credits[0]["credit"] or debits[0]["currency"] != credits[0]["currency"]:
            raise PlatformError("This journal is outside the supported two-account reversal format.", 409, "journal_format")
        return self._action_propose(tx, pk, tenant, actor, member, {"source_id": debits[0]["account_id"],
            "destination_id": credits[0]["account_id"], "amount": debits[0]["debit"], "purpose": text(body.get("reason"), "Correction reason", 10, 500)},
            reversal={"sequence": sequence, "id": journal["id"], "hash": digest(journal)})

    def _finish_pending(self, tx, pk, actor, action, status, reason):
        self._release(tx, pk, action)
        action.update(status=status, closed_at=now(), close_reason=reason)
        tenant = required(tx, pk, "META")
        tenant["pending_count"] -= 1
        tx.put(pk, "META", tenant)
        tx.put(pk, "ACTION#" + action["id"], action)
        audit(tx, pk, actor, "action_" + status, {"id": action["id"], "reason": reason})
        return {"action": action}

    @staticmethod
    def _pending(tx, pk, body):
        action = required(tx, pk, "ACTION#" + identifier(body.get("action_id")))
        if action["status"] != "pending":
            raise PlatformError("Action is no longer awaiting review.", 409, "invalid_state")
        return action

    def _action_approve(self, tx, pk, tenant, actor, member, body):
        action = self._pending(tx, pk, body)
        if actor.sub in (action["proposed_by"], action.get("initiated_by")):
            raise PlatformError("You cannot approve an action you proposed.", 403, "self_approval")
        reason = text(body.get("reason"), "Approval reason", 5, 500)
        if action["expires_at"] <= time.time():
            return self._finish_pending(tx, pk, actor, action, "expired", "The approval window elapsed.")
        policy, checks, decision = self._evaluate(tx, pk, tenant, action, held=True)
        if policy["id"] != action["policy_id"]:
            action["approvals"] = []
            audit(tx, pk, actor, "approvals_invalidated", {"action_id": action["id"], "previous_policy": action["policy_id"], "current_policy": policy["id"]})
        action.update(policy_id=policy["id"], policy_snapshot=policy["config"], checks=checks)
        if decision == "blocked":
            audit(tx, pk, actor, "approval_controls_failed", {"action_id": action["id"], "checks": checks})
            return self._finish_pending(tx, pk, actor, action, "blocked", "The current mandate no longer permits this action.")
        active_approvals = []
        for approval in action["approvals"]:
            reviewer = tx.get(pk, "MEMBER#" + approval["sub"])
            if reviewer and reviewer["status"] == "active" and reviewer["role"] in PERMISSIONS["action_approve"]:
                active_approvals.append(approval)
        if actor.sub in {item["sub"] for item in active_approvals}:
            raise PlatformError("Your approval is already recorded.", 409, "duplicate_approval")
        action["approvals"] = active_approvals + [{"sub": actor.sub, "timestamp": now(), "reason": reason}]
        audit(tx, pk, actor, "approval_recorded", {"action_id": action["id"], "reason": reason, "policy_id": policy["id"],
              "approvals": action["approvals"], "required": policy["config"]["required_approvals"], "checks": checks})
        if len(action["approvals"]) >= policy["config"]["required_approvals"]:
            self._settle(tx, pk, actor, action)
            current = required(tx, pk, "META")
            current["pending_count"] -= 1
            tx.put(pk, "META", current)
        tx.put(pk, "ACTION#" + action["id"], action)
        return {"action": action}

    def _action_decline(self, tx, pk, tenant, actor, member, body):
        action = self._pending(tx, pk, body)
        if actor.sub in (action["proposed_by"], action.get("initiated_by")):
            raise PlatformError("Cancel your own request instead of reviewing it.", 403, "self_approval")
        return self._finish_pending(tx, pk, actor, action, "declined", text(body.get("reason"), "Decline reason", 5, 500))

    def _action_cancel(self, tx, pk, tenant, actor, member, body):
        action = self._pending(tx, pk, body)
        if actor.sub not in (action["proposed_by"], action.get("initiated_by")) and member["role"] not in MANAGERS:
            raise PlatformError("You can cancel only your own pending actions.", 403, "forbidden")
        return self._finish_pending(tx, pk, actor, action, "cancelled", text(body.get("reason"), "Cancellation reason", 5, 500))

    def _action_expire(self, tx, pk, tenant, actor, member, body):
        action = self._pending(tx, pk, body)
        if action["expires_at"] > time.time():
            raise PlatformError("This action has not expired.", 409, "invalid_state")
        return self._finish_pending(tx, pk, actor, action, "expired", "The approval window elapsed.")

    def _agent_checks(self, tx, pk, action, *, held=False):
        if action.get("agent_id"):
            raise PlatformError("Agent services are not configured.", 503, "agent_unavailable")
        return []

    def _agent_review(self, tx, pk, action):
        return False

    def _agent_meter(self, tx, pk, action, date, field, delta):
        if action.get("agent_id"):
            raise PlatformError("Agent services are not configured.", 503, "agent_unavailable")
