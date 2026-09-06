"""Scheduled AWS worker; no public URL or human administration interface."""
import json
import os
import time
from platform_core.agents import AgentService
from platform_core.runner import Runner
from platform_core.store import DocumentStore, DynamoBackend

runner = None


def handler(event, context):
    global runner
    if runner is None:
        runner = Runner(AgentService(DocumentStore(DynamoBackend(os.environ["PLATFORM_TABLE_NAME"]))))
    try:
        results = runner.tick(max_jobs=10, seconds=min(65, context.get_remaining_time_in_millis() / 1000 - 10))
        counts = {}
        for result in results:
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        print(json.dumps({"_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{
            "Namespace": "Agentu/Worker", "Dimensions": [["Stage"]], "Metrics": [
                {"Name": name, "Unit": "Count"} for name in ("Heartbeats", "FailedRuns", "ExpiryRetries")]}]},
            "Stage": os.environ.get("STAGE", "local"), "Heartbeats": 1,
            "FailedRuns": counts.get("failed", 0), "ExpiryRetries": counts.get("retry", 0), "worker_results": counts}))
        return {"processed": len(results), "statuses": counts}
    except Exception:
        print(json.dumps({"error": "worker_failed", "request_id": context.aws_request_id}))
        # A failed invocation is visible to the worker's CloudWatch error alarm;
        # unfinished jobs remain durable and are reclaimed after their lease.
        raise RuntimeError("Agentu worker invocation failed.") from None
