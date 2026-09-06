"""Governed machine identities and bounded agent runs.

Machine credentials never authenticate to the human administration API. They can
submit one tightly validated internal-transfer proposal, within their own mandate.
"""
import hashlib
import os
import re
import secrets
import time
from datetime import datetime, timezone
from .errors import PlatformError
from .jobs import enqueue
from .model import Actor, amount, currency, digest, identifier, new_id, now, text
from .service import PlatformService, PERMISSIONS, COLLECTIONS, MANAGERS, access, audit, required, tenant_key


def credential_key(agent_id, key_id):
    return "AGENTKEY#" + agent_id + "#" + key_id


def validate_config(tx, pk, body):
    if not isinstance(body, dict):
        raise PlatformError("Agent configuration must be an object.")
    config = {"objective": text(body.get("objective"), "Agent objective", 10, 1000),
              "transaction_limit": amount(body.get("transaction_limit"), "Agent transaction limit"),
              "daily_limit": amount(body.get("daily_limit"), "Agent daily limit"),
              "require_review": body.get("require_review", True), "daily_runs": body.get("daily_runs", 20),
              "provider": body.get("provider", "treasury_rule")}
    if config["provider"] not in ("external", "treasury_rule", "bedrock"):
        raise PlatformError("Choose external, treasury_rule or bedrock.")
    if type(config["require_review"]) is not bool or type(config["daily_runs"]) is not int or not 1 <= config["daily_runs"] <= 1000:
        raise PlatformError("Set an explicit review requirement and 1–1,000 daily runs.")
    if config["transaction_limit"] > config["daily_limit"]:
        raise PlatformError("Agent transaction limit must not exceed its daily limit.")
    for field in ("source_ids", "destination_ids"):
        values = body.get(field)
        if not isinstance(values, list) or not 1 <= len(values) <= 10:
            raise PlatformError("Select one to ten accounts for each side of the mandate.")
        config[field] = [identifier(value, "Account") for value in values]
        if len(set(values)) != len(values):
            raise PlatformError("Account scopes must not contain duplicates.")
        for value in values:
            account = required(tx, pk, "ACCOUNT#" + value)
            if account["kind"] != "asset" or account["status"] != "active":
                raise PlatformError("Agent scopes must contain active asset accounts.")
    codes = body.get("currencies")
    if not isinstance(codes, list) or not 1 <= len(codes) <= 3:
        raise PlatformError("Select one to three currencies.")
    config["currencies"] = [currency(code) for code in codes]
    if len(set(codes)) != len(codes):
        raise PlatformError("Currencies must be distinct.")
    if config["provider"] == "treasury_rule":
        if len(config["source_ids"]) != 1 or len(config["destination_ids"]) != 1 or config["source_ids"] == config["destination_ids"]:
            raise PlatformError("A treasury rule requires one source and a different destination.")
        config["source_floor"] = amount(body.get("source_floor", 0), "Source balance floor", 0)
        config["destination_target"] = amount(body.get("destination_target"), "Destination target")
        source = required(tx, pk, "ACCOUNT#" + config["source_ids"][0])
        destination = required(tx, pk, "ACCOUNT#" + config["destination_ids"][0])
        if source["currency"] != destination["currency"] or source["currency"] not in config["currencies"]:
            raise PlatformError("Treasury rule accounts must use the same permitted currency.")
    return config


class AgentService(PlatformService):
    permissions = {**PERMISSIONS, "agent_create": MANAGERS, "agent_revision": MANAGERS, "agent_publish": MANAGERS,
                   "agent_suspend": MANAGERS, "agent_key_create": {"owner"}, "agent_key_revoke": {"owner"},
                   "agent_run": {"owner", "operator"}, "agent_run_cancel": {"owner", "administrator", "operator"}}
    collections = {**COLLECTIONS, "agents": "AGENT#", "agent_keys": "AGENTKEY#", "runs": "RUN#"}

    @staticmethod
    def capabilities():
        return {"treasury_rule": {"configured": True, "uses_model": False}, "external": {"configured": True},
                "bedrock": {"configured": bool(os.getenv("BEDROCK_MODEL_ARN")), "connection_verified": False}}

    def _agent_create(self, tx, pk, tenant, actor, member, body):
        agent = {"id": new_id(), "name": text(body.get("name"), "Agent name", 2, 100), "status": "draft",
                 "revision": new_id(), "created_by": actor.sub, "created_at": now(),
                 "config": validate_config(tx, pk, body.get("config"))}
        tx.put(pk, "AGENT#" + agent["id"], agent, insert_only=True)
        audit(tx, pk, actor, "agent_drafted", agent)
        return {"agent": agent}

    def _agent_revision(self, tx, pk, tenant, actor, member, body):
        agent = required(tx, pk, "AGENT#" + identifier(body.get("agent_id")))
        previous = agent["revision"]
        agent.update(config=validate_config(tx, pk, body.get("config")), revision=new_id(), status="draft",
                     created_by=actor.sub, created_at=now())
        for field in ("published_by", "published_at"):
            agent.pop(field, None)
        tx.put(pk, "AGENT#" + agent["id"], agent)
        machine = tx.get(pk, "MEMBER#agent_" + agent["id"])
        if machine:
            tx.put(pk, "MEMBER#agent_" + agent["id"], {**machine, "status": "suspended"})
        audit(tx, pk, actor, "agent_revision_drafted", {"agent": agent, "previous_revision": previous})
        return {"agent": agent}

    def _agent_publish(self, tx, pk, tenant, actor, member, body):
        agent = required(tx, pk, "AGENT#" + identifier(body.get("agent_id")))
        if agent["status"] != "draft":
            raise PlatformError("Publish a draft mandate. To restore a suspended agent, draft and review a new version.", 409, "invalid_state")
        if actor.sub == agent["created_by"]:
            raise PlatformError("A different owner or administrator must publish this agent mandate.", 403, "independent_review_required")
        author = required(tx, pk, "MEMBER#" + agent["created_by"])
        if author["status"] != "active" or author["role"] not in MANAGERS:
            raise PlatformError("The mandate author is no longer authorised.", 409, "author_inactive")
        validate_config(tx, pk, agent["config"])
        policy = required(tx, pk, "POLICY#" + tenant["policy_id"])["config"]
        if agent["config"]["transaction_limit"] > policy["transaction_limit"] or agent["config"]["daily_limit"] > policy["daily_limit"] or not set(agent["config"]["currencies"]) <= set(policy["currencies"]):
            raise PlatformError("The agent mandate must fit within the institution's current limits and currencies.")
        agent.update(status="active", published_by=actor.sub, published_at=now(), publication_reason=text(body.get("reason"), "Publication reason", 5, 500))
        tx.put(pk, "AGENT#" + agent["id"], agent)
        machine = {"sub": "agent_" + agent["id"], "kind": "agent", "agent_id": agent["id"], "agent_revision": agent["revision"],
                   "email": agent["name"], "role": "operator", "status": "active", "joined_at": now()}
        tx.put(pk, "MEMBER#" + machine["sub"], machine)
        audit(tx, pk, actor, "agent_mandate_published", agent)
        return {"agent": agent}

    def _agent_suspend(self, tx, pk, tenant, actor, member, body):
        agent = required(tx, pk, "AGENT#" + identifier(body.get("agent_id")))
        reason = text(body.get("reason"), "Suspension reason", 5, 500)
        agent.update(status="suspended", suspended_at=now())
        tx.put(pk, "AGENT#" + agent["id"], agent)
        machine = tx.get(pk, "MEMBER#agent_" + agent["id"])
        if machine:
            tx.put(pk, "MEMBER#" + machine["sub"], {**machine, "status": "suspended"})
        audit(tx, pk, actor, "agent_suspended", {"id": agent["id"], "reason": reason})
        return {"agent": agent}

    def _agent_key_create(self, tx, pk, tenant, actor, member, body):
        agent = required(tx, pk, "AGENT#" + identifier(body.get("agent_id")))
        if agent["status"] != "active" or agent["config"]["provider"] != "external":
            raise PlatformError("Credentials are issued only for active external agents.", 409, "invalid_state")
        days = body.get("days", 7)
        if type(days) is not int or not 1 <= days <= 90:
            raise PlatformError("Credential lifetime must be 1–90 days.")
        token = "agtu_" + secrets.token_urlsafe(32)
        value = {"id": new_id(), "agent_id": agent["id"], "revision": agent["revision"],
                 "label": text(body.get("label"), "Credential label", 2, 100), "status": "active",
                 "created_by": actor.sub, "created_at": now(), "expires_at": int(time.time()) + days * 86400,
                 "secret_hash": hashlib.sha256(token.encode()).hexdigest()}
        tx.put(pk, credential_key(agent["id"], value["id"]), value, insert_only=True)
        tx.put("AGENTTOKEN#" + value["secret_hash"], "META", {"tenant_id": tenant["id"], "agent_id": agent["id"], "key_id": value["id"]}, insert_only=True)
        public = {k: v for k, v in value.items() if k != "secret_hash"}
        audit(tx, pk, actor, "agent_credential_issued", public)
        return {"credential": public, "agent_token": token}

    def _agent_key_revoke(self, tx, pk, tenant, actor, member, body):
        value = required(tx, pk, credential_key(identifier(body.get("agent_id")), identifier(body.get("key_id"))))
        value.update(status="revoked", revoked_at=now())
        tx.put(pk, credential_key(value["agent_id"], value["id"]), value)
        audit(tx, pk, actor, "agent_credential_revoked", {"id": value["id"], "agent_id": value["agent_id"], "reason": text(body.get("reason"), "Revocation reason", 5, 500)})
        return {"credential": {k: v for k, v in value.items() if k != "secret_hash"}}

    @staticmethod
    def _run_quota(tx, pk, agent):
        day = datetime.now(timezone.utc).date().isoformat()
        key = "AGENTRUNS#" + agent["id"] + "#" + day
        usage = tx.get(pk, key) or {"date": day, "runs": 0}
        if usage["runs"] >= agent["config"]["daily_runs"]:
            raise PlatformError("This agent has reached its daily run budget.", 429, "agent_run_budget")
        usage["runs"] += 1
        tx.put(pk, key, usage)

    def _agent_run(self, tx, pk, tenant, actor, member, body):
        agent = required(tx, pk, "AGENT#" + identifier(body.get("agent_id")))
        if agent["status"] != "active" or tenant["status"] != "active":
            raise PlatformError("The agent and institution must be active.", 409, "invalid_state")
        if agent["config"]["provider"] == "external":
            raise PlatformError("External agents submit through the agent proposal API.")
        if agent["config"]["provider"] == "bedrock" and not self.capabilities()["bedrock"]["configured"]:
            raise PlatformError("The business Bedrock model must be configured before starting model runs.", 503, "provider_unconfigured")
        self._run_quota(tx, pk, agent)
        run = {"id": new_id(), "agent_id": agent["id"], "agent_name": agent["name"], "revision": agent["revision"],
               "provider": agent["config"]["provider"], "status": "queued", "requested_by": actor.sub,
               "created_at": now(), "deadline": int(time.time()) + 900,
               "instruction": text(body.get("instruction", agent["config"]["objective"]), "Run instruction", 10, 1000)}
        run["job_key"] = enqueue(tx, "agent_run", tenant["id"], run["id"])
        tx.put(pk, "RUN#" + run["id"], run, insert_only=True)
        audit(tx, pk, actor, "agent_run_requested", run)
        return {"run": run}

    def _agent_run_cancel(self, tx, pk, tenant, actor, member, body):
        run = required(tx, pk, "RUN#" + identifier(body.get("run_id")))
        if run["status"] not in ("queued", "running"):
            raise PlatformError("This run has finished. Manage its resulting action instead.", 409, "invalid_state")
        if member["role"] not in MANAGERS and run["requested_by"] != actor.sub:
            raise PlatformError("You can cancel only your own run.", 403, "forbidden")
        run.update(status="cancelled", completed_at=now(), reason=text(body.get("reason"), "Cancellation reason", 5, 500))
        tx.put(pk, "RUN#" + run["id"], run)
        tx.delete_work(run["job_key"])
        audit(tx, pk, actor, "agent_run_cancelled", {"id": run["id"], "reason": run["reason"]})
        return {"run": run}

    def submit_agent(self, token, body, request_id):
        if not isinstance(token, str) or not re.fullmatch(r"agtu_[a-zA-Z0-9_-]{43}", token):
            raise PlatformError("A valid agent credential is required.", 401, "agent_unauthenticated")
        if not isinstance(body, dict) or set(body) != {"source_id", "destination_id", "amount", "purpose"}:
            raise PlatformError("Provide only source_id, destination_id, amount and purpose.")
        identifier(request_id, "Idempotency key")
        if len(request_id) < 16:
            raise PlatformError("Use an idempotency key with at least 16 characters.")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        fingerprint = digest(body)
        def transaction(tx):
            pointer = tx.get("AGENTTOKEN#" + token_hash, "META")
            if not pointer:
                raise PlatformError("Agent credential is unavailable.", 401, "agent_unauthenticated")
            pk = tenant_key(pointer["tenant_id"])
            credential = required(tx, pk, credential_key(pointer["agent_id"], pointer["key_id"]))
            agent = required(tx, pk, "AGENT#" + pointer["agent_id"])
            issuer = tx.get(pk, "MEMBER#" + credential["created_by"])
            issuer_active = issuer and issuer["status"] == "active" and issuer["role"] == "owner"
            if not issuer_active or credential["status"] != "active" or credential["expires_at"] <= time.time() or credential["revision"] != agent["revision"] or agent["status"] != "active":
                raise PlatformError("Agent credential or mandate is expired, revoked or superseded.", 401, "agent_unauthenticated")
            actor = Actor("agent_" + agent["id"], "", False, "agent", credential["id"], credential["created_by"])
            _, tenant, member = access(tx, pointer["tenant_id"], actor, PERMISSIONS["action_propose"])
            key = "AGENTREQUEST#" + agent["id"] + "#" + request_id
            previous = tx.get(pk, key)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise PlatformError("Idempotency key was used with different input.", 409, "idempotency_conflict")
                return previous["response"]
            self._run_quota(tx, pk, agent)
            result = self._action_propose(tx, pk, tenant, actor, member, body)
            run = {"id": new_id(), "agent_id": agent["id"], "agent_name": agent["name"], "revision": agent["revision"],
                   "provider": "external", "status": "completed", "created_at": now(), "completed_at": now(),
                   "requested_by": credential["created_by"], "credential_id": credential["id"], "action_id": result["action"]["id"],
                   "decision": result["action"]["status"], "output": body}
            tx.put(pk, "RUN#" + run["id"], run, insert_only=True)
            audit(tx, pk, actor, "external_agent_proposal_recorded", {"run_id": run["id"], "action_id": run["action_id"], "decision": run["decision"]})
            result["run"] = run
            tx.put(pk, key, {"fingerprint": fingerprint, "response": result}, insert_only=True)
            return result
        return self.store.transact(transaction)

    @staticmethod
    def _agent_usage(tx, pk, agent_id, date, code):
        key = f"AGENTUSAGE#{agent_id}#{date}#{code}"
        return key, tx.get(pk, key) or {"date": date, "currency": code, "reserved": 0, "executed": 0}

    def _agent_meter(self, tx, pk, action, date, field, delta):
        if not action.get("agent_id"):
            return
        key, usage = self._agent_usage(tx, pk, action["agent_id"], date, action["currency"])
        usage[field] += delta
        if usage[field] < 0:
            raise PlatformError("Agent usage accounting is inconsistent.", 409, "reservation_inconsistent")
        tx.put(pk, key, usage)
        if field == "reserved":
            destination_key = "AGENTDEST#" + action["agent_id"] + "#" + action["destination_id"]
            incoming = tx.get(pk, destination_key) or {"reserved": 0}
            incoming["reserved"] += delta
            if incoming["reserved"] < 0:
                raise PlatformError("Agent destination reservations are inconsistent.", 409, "reservation_inconsistent")
            tx.put(pk, destination_key, incoming)

    def _agent_checks(self, tx, pk, action, *, held=False):
        if not action.get("agent_id"):
            return []
        agent = required(tx, pk, "AGENT#" + action["agent_id"])
        config = agent["config"]
        date = datetime.now(timezone.utc).date().isoformat()
        _, usage = self._agent_usage(tx, pk, agent["id"], date, action["currency"])
        counted = usage["executed"] + usage["reserved"] - (action["amount"] if held and action["usage_date"] == date else 0)
        valid_credential = True
        if action.get("credential_id"):
            credential = required(tx, pk, credential_key(agent["id"], action["credential_id"]))
            issuer = tx.get(pk, "MEMBER#" + credential["created_by"])
            valid_credential = bool(issuer and issuer["status"] == "active" and issuer["role"] == "owner" and credential["status"] == "active" and credential["expires_at"] > time.time() and credential["revision"] == agent["revision"])
        sponsor = tx.get(pk, "MEMBER#" + (action.get("initiated_by") or ""))
        sponsor_roles = {"owner"} if action.get("credential_id") else self.permissions["agent_run"]
        rules = [
            ("Agent sponsor authorised", bool(sponsor and sponsor["status"] == "active" and sponsor["role"] in sponsor_roles), "Current authority of the human who initiated the run or issued its credential"),
            ("Agent mandate active", agent["status"] == "active" and action["agent_revision"] == agent["revision"], {"status": agent["status"], "revision": agent["revision"]}),
            ("Agent credential valid", valid_credential, "Current credential status and expiry"),
            ("Agent account scope", action["source_id"] in config["source_ids"] and action["destination_id"] in config["destination_ids"], {"sources": config["source_ids"], "destinations": config["destination_ids"]}),
            ("Agent currency scope", action["currency"] in config["currencies"], config["currencies"]),
            ("Agent transaction limit", action["amount"] <= config["transaction_limit"], config["transaction_limit"]),
            ("Agent daily limit", counted + action["amount"] <= config["daily_limit"], {"limit": config["daily_limit"], "committed_and_reserved": counted}),
        ]
        if config["provider"] == "treasury_rule":
            source = required(tx, pk, "ACCOUNT#" + action["source_id"])
            destination = required(tx, pk, "ACCOUNT#" + action["destination_id"])
            incoming = tx.get(pk, "AGENTDEST#" + agent["id"] + "#" + action["destination_id"]) or {"reserved": 0}
            available = source["balance"] - source["reserved"] + (action["amount"] if held else 0)
            committed = destination["balance"] + incoming["reserved"] - (action["amount"] if held else 0)
            rules += [
                ("Agent source floor", available - action["amount"] >= config["source_floor"], {"available": available, "floor": config["source_floor"]}),
                ("Agent reserve target", committed + action["amount"] <= config["destination_target"], {"committed": committed, "target": config["destination_target"]}),
            ]
        return rules

    def _agent_review(self, tx, pk, action):
        return bool(action.get("agent_id") and required(tx, pk, "AGENT#" + action["agent_id"])["config"]["require_review"])
