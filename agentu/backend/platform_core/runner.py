"""Lease-based work processing; inference stays outside retryable transactions."""
import time
from .errors import PlatformError
from .jobs import QUEUE
from .model import Actor, new_id, now
from .providers import BedrockProvider, ProviderError, TreasuryRule, decision
from .service import audit, required, tenant_key

SYSTEM = Actor("system-worker", "", False, "system")


class Runner:
    def __init__(self, service, providers=None):
        self.service = service
        self.store = service.store
        self.providers = providers if providers is not None else {"treasury_rule": TreasuryRule(), "bedrock": BedrockProvider()}

    def due(self, after=None):
        return self.store.transact(lambda tx: tx.query(QUEUE, "DUE#", after=after, limit=40))

    def _run_context(self, tx, job):
        pk = tenant_key(job["tenant_id"])
        tenant = required(tx, pk, "META")
        run = required(tx, pk, "RUN#" + job["reference"])
        agent = required(tx, pk, "AGENT#" + run["agent_id"])
        sponsor = tx.get(pk, "MEMBER#" + run["requested_by"])
        if run["status"] not in ("queued", "running") or run["deadline"] <= time.time():
            raise PlatformError("The run was cancelled, completed or expired.", 409, "run_expired")
        if tenant["status"] != "active" or agent["status"] != "active" or agent["revision"] != run["revision"]:
            raise PlatformError("The institution or agent mandate changed before execution.", 409, "mandate_changed")
        if not sponsor or sponsor["status"] != "active" or sponsor["role"] not in self.service.permissions["agent_run"]:
            raise PlatformError("The person who requested the run is no longer authorised.", 403, "sponsor_inactive")
        machine = required(tx, pk, "MEMBER#agent_" + agent["id"])
        if machine["status"] != "active" or machine["agent_revision"] != run["revision"]:
            raise PlatformError("The machine identity is no longer authorised.", 403, "agent_inactive")
        scope = sorted(set(agent["config"]["source_ids"] + agent["config"]["destination_ids"]))
        accounts = {}
        for account_id in scope:
            account = required(tx, pk, "ACCOUNT#" + account_id)
            if account["status"] != "active":
                raise PlatformError("An account in the agent mandate is suspended.", 409, "account_inactive")
            accounts[account_id] = {k: account[k] for k in ("id", "name", "currency", "balance", "reserved")}
            incoming = tx.get(pk, "AGENTDEST#" + agent["id"] + "#" + account_id) or {"reserved": 0}
            accounts[account_id]["incoming_reserved"] = incoming["reserved"]
        return {"tenant": tenant, "run": run, "agent": agent, "member": machine, "accounts": accounts, "event_head": tenant["event_head"]}

    def claim(self, key):
        def operation(tx):
            job = tx.get(QUEUE, key)
            if not job or job["due"] > time.time() or job["lease_until"] > time.time():
                return None
            job.update(lease=new_id(), lease_until=time.time() + 150, attempts=job["attempts"] + 1)
            tx.put(QUEUE, key, job)
            if job["kind"] == "agent_run":
                context = self._run_context(tx, job)
                if job["attempts"] > 3:
                    raise PlatformError("The run exceeded its processing attempt limit.", 409, "attempt_limit")
                run = context["run"]
                run.update(status="running", attempts=job["attempts"], started_at=run.get("started_at", now()))
                pk = tenant_key(job["tenant_id"])
                tx.put(pk, "RUN#" + run["id"], run)
                audit(tx, pk, SYSTEM, "agent_run_started", {"run_id": run["id"], "attempt": job["attempts"], "provider": run["provider"]})
                return {"job": job, "context": context}
            return {"job": job}
        return self.store.transact(operation)

    def fail(self, key, error, lease=None, retryable=False):
        def operation(tx):
            job = tx.get(QUEUE, key)
            if not job or (lease is not None and job.get("lease") != lease):
                return {"status": "ignored"}
            # A claim failure must not disrupt another worker's valid lease.
            if lease is None and job["lease_until"] > time.time():
                return {"status": "ignored"}
            pk = tenant_key(job["tenant_id"])
            if job["kind"] == "agent_run":
                run = required(tx, pk, "RUN#" + job["reference"])
                if run["status"] not in ("queued", "running"):
                    tx.delete_work(key)
                    return {"status": "ignored"}
                retry = retryable and job["attempts"] < 3 and run["deadline"] > time.time() + 30
                run.update(status="queued" if retry else "failed", error={"code": getattr(error, "code", "worker_failed"), "message": str(error)[:500]})
                if retry:
                    job.update(lease_until=time.time() + 30)
                    tx.put(QUEUE, key, job)
                else:
                    run["completed_at"] = now()
                    tx.delete_work(key)
                tx.put(pk, "RUN#" + run["id"], run)
                audit(tx, pk, SYSTEM, "agent_run_retry_queued" if retry else "agent_run_failed", {"run_id": run["id"], "error": run["error"]})
                return {"status": run["status"]}
            # Expiry errors retain work for retry; never discard a financial hold.
            job.update(lease_until=time.time() + 30, last_error={"code": getattr(error, "code", "expiry_failed")})
            tx.put(QUEUE, key, job)
            return {"status": "retry"}
        return self.store.transact(operation)

    def finish(self, claimed, result=None):
        expected = claimed["job"]
        if result is not None:
            result = {**result, "decision": decision(result["decision"])}
        def operation(tx):
            job = tx.get(QUEUE, expected["key"])
            if not job or job.get("lease") != expected["lease"] or job["lease_until"] <= time.time():
                return {"status": "ignored"}
            pk = tenant_key(job["tenant_id"])
            if job["kind"] == "action_expire":
                action = required(tx, pk, "ACTION#" + job["reference"])
                if action["status"] == "pending":
                    if action["expires_at"] > time.time():
                        raise PlatformError("The action has not expired.", 409, "not_expired")
                    self.service._finish_pending(tx, pk, SYSTEM, action, "expired", "The approval window elapsed; the worker released the reservation.")
                tx.delete_work(job["key"])
                return {"status": "expired" if action["status"] == "expired" else "already_resolved"}
            context = self._run_context(tx, job)
            run = context["run"]
            output = result["decision"]
            if output["action"] == "propose_transfer":
                actor = Actor("agent_" + run["agent_id"], "", False, "agent", initiated_by=run["requested_by"])
                action = self.service._action_propose(tx, pk, context["tenant"], actor, context["member"], {k: v for k, v in output.items() if k != "action"})["action"]
                run.update(action_id=action["id"], decision=action["status"], status="completed")
            else:
                run.update(status="no_action", decision="no_action")
            run.update(completed_at=now(), output=output, evidence={k: v for k, v in result.items() if k != "decision"})
            run.pop("error", None)
            tx.put(pk, "RUN#" + run["id"], run)
            tx.delete_work(job["key"])
            audit(tx, pk, SYSTEM, "agent_run_completed", {"run_id": run["id"], "provider": run["provider"], "decision": run["decision"], "action_id": run.get("action_id"), "evidence": run["evidence"]})
            return {"status": run["status"], "run_id": run["id"]}
        return self.store.transact(operation)

    def process(self, key):
        claimed = None
        try:
            claimed = self.claim(key)
            if claimed is None:
                return {"status": "ignored"}
            if claimed["job"]["kind"] == "action_expire":
                return self.finish(claimed)
            provider = self.providers.get(claimed["context"]["agent"]["config"]["provider"])
            if provider is None:
                raise ProviderError("This decision provider is not configured.", "provider_unconfigured")
            result = provider.propose(claimed["context"])
            return self.finish(claimed, result)
        except (PlatformError, ProviderError) as error:
            return self.fail(key, error, claimed["job"]["lease"] if claimed else None, getattr(error, "retryable", False))

    def tick(self, max_jobs=10, seconds=65):
        deadline = time.monotonic() + seconds
        completed, after, pages = [], None, 0
        while len(completed) < max_jobs and pages < 4 and time.monotonic() < deadline:
            jobs, after = self.due(after)
            pages += 1
            for job in jobs:
                if job["due"] > time.time():
                    return completed
                if job["lease_until"] > time.time():
                    continue
                # Leave enough time for the bounded provider call and commit.
                if job["kind"] == "agent_run" and time.monotonic() + 35 > deadline:
                    return completed
                completed.append(self.process(job["key"]))
                if len(completed) >= max_jobs or time.monotonic() >= deadline:
                    return completed
            if not after:
                break
        return completed
