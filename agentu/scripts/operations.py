"""Read environment monitoring without sending alerts or changing resources."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
from botocore.exceptions import ClientError
from deploy import clients, outputs
from environments import EXPECTED_ACCOUNT, REGION, STAGES
from environment_check import validate_outputs
from monitoring import alarm_specs, dashboard, topic_policy


def require(condition, message):
    if not condition:
        raise ValueError("Operations inspection stopped: " + message)


def pages(operation, field, **args):
    items, seen, token = [], set(), None
    for _ in range(100):
        page = operation(**args, **({"NextToken": token} if token else {}))
        require(not page.get("Messages"), "A service returned an inspection warning; no completeness claim was made.")
        items.extend(page.get(field, []))
        token = page.get("NextToken")
        if not token:
            return items
        require(isinstance(token, str) and token not in seen, "A listing repeated or returned an invalid pagination token.")
        seen.add(token)
    raise ValueError("Operations inspection stopped: Listing exceeds the inspection bound; no completeness claim was made.")


def comparable(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def metric_observations(cw, stage, api_id, at):
    start = at - timedelta(minutes=15)
    definitions = {
        "heartbeats": ("Agentu/Worker", "Heartbeats", [{"Name": "Stage", "Value": stage}]),
        "api_requests": ("AWS/ApiGateway", "Count", [{"Name": "ApiId", "Value": api_id}, {"Name": "Stage", "Value": "$default"}]),
        "api_server_errors": ("AWS/ApiGateway", "5xx", [{"Name": "ApiId", "Value": api_id}, {"Name": "Stage", "Value": "$default"}]),
    }
    queries = [{"Id": key, "ReturnData": True, "AccountId": EXPECTED_ACCOUNT, "MetricStat": {"Metric": {"Namespace": ns, "MetricName": metric, "Dimensions": dimensions},
                "Period": 60, "Stat": "Sum"}} for key, (ns, metric, dimensions) in definitions.items()]
    rows = pages(cw.get_metric_data, "MetricDataResults", MetricDataQueries=queries, StartTime=start, EndTime=at,
                 ScanBy="TimestampAscending", MaxDatapoints=1000)
    points = {key: {} for key in definitions}
    statuses = {key: [] for key in definitions}
    for row in rows:
        key = row.get("Id")
        require(key in definitions, "Metrics returned an unexpected query identity.")
        statuses[key].append(row.get("StatusCode"))
        require(not row.get("Messages"), "Metrics returned a warning; inspect CloudWatch before using this observation.")
        times, values = row.get("Timestamps", []), row.get("Values", [])
        require(len(times) == len(values), "Metric timestamps and values disagree.")
        for timestamp, value in zip(times, values):
            require(isinstance(timestamp, datetime) and timestamp.tzinfo is not None and start <= timestamp < at,
                    "Metrics returned an invalid or out-of-window timestamp.")
            require(type(value) in (int, float) and math.isfinite(value) and value >= 0, "Metrics returned an invalid count.")
            require(timestamp not in points[key], "Metric pagination repeated a timestamp.")
            points[key][timestamp] = value
    result = {}
    for key, samples in points.items():
        positive = [t for t, value in samples.items() if value > 0]
        result[key] = {"complete": bool(statuses[key]) and all(s == "Complete" for s in statuses[key]),
                       "samples": len(samples), "sum": sum(samples.values()) if samples else None,
                       "latest_sample": max(samples).isoformat() if samples else None,
                       "recent_sample": bool(samples) and max(samples) >= at - timedelta(minutes=10),
                       "recent_positive_sample": bool(positive) and max(positive) >= at - timedelta(minutes=10)}
    return result


def inspect(session, stage, at=None):
    at = at or datetime.now(timezone.utc)
    require(isinstance(at, datetime) and at.tzinfo is not None, "Use a timezone-aware inspection time.")
    at = at.astimezone(timezone.utc).replace(second=0, microsecond=0)
    stack, values = outputs(session, stage)
    validate_outputs(stage, stack, values)
    topic = f"arn:aws:sns:{REGION}:{EXPECTED_ACCOUNT}:agentu-{stage}-operations"
    name = f"agentu-{stage}-operations"
    require(values.get("OperationsTopicArn") == topic and values.get("OperationsDashboard") == name,
            "Apply the current operations infrastructure to this business environment first.")
    cw, sns, events = (session.client(service) for service in ("cloudwatch", "sns", "events"))
    expected = {f"agentu-{stage}-{suffix}": props for suffix, props in alarm_specs(stage, values).values()}
    alarms = pages(cw.describe_alarms, "MetricAlarms", AlarmNames=list(expected), AlarmTypes=["MetricAlarm"])
    require(len(alarms) == len(expected) and {a.get("AlarmName") for a in alarms} == set(expected), "The required alarm inventory is incomplete or duplicated.")
    states = []
    for alarm in alarms:
        name = alarm["AlarmName"]
        require(alarm.get("AlarmArn") == f"arn:aws:cloudwatch:{REGION}:{EXPECTED_ACCOUNT}:alarm:{name}", "An alarm belongs to another account or region.")
        require(alarm.get("ActionsEnabled") is True and alarm.get("AlarmActions") == [topic] and alarm.get("OKActions") == [topic]
                and not alarm.get("InsufficientDataActions"), "Alarm delivery is disabled or routed outside the designated topic: " + name)
        for key, value in expected[name].items():
            actual = alarm.get(key)
            if key == "Dimensions":
                actual, value = sorted(actual or [], key=comparable), sorted(value, key=comparable)
            require(actual == value, "An alarm metric, threshold or evaluation setting has drifted: " + name + " / " + key)
        require(not alarm.get("Metrics") and not alarm.get("Unit"), "An alarm has an unreviewed metric expression or unit.")
        require(not alarm.get("ExtendedStatistic") if "Statistic" in expected[name] else not alarm.get("Statistic"), "An alarm statistic differs from its definition.")
        require(alarm.get("StateValue") in ("OK", "ALARM", "INSUFFICIENT_DATA"), "An alarm has no recognized state.")
        states.append({"name": name, "state": alarm["StateValue"]})
    attrs = sns.get_topic_attributes(TopicArn=topic)["Attributes"]
    require(attrs.get("TopicArn") == topic and attrs.get("Owner") == EXPECTED_ACCOUNT, "The notification topic belongs to another business account.")
    require(not attrs.get("KmsMasterKeyId"), "The topic has an unreviewed encryption key; verify its CloudWatch publishing permissions before proceeding.")
    policy = json.loads(attrs["Policy"])
    require(policy.get("Version") == "2012-10-17" and sorted(policy.get("Statement", []), key=comparable) == sorted(topic_policy(stage, topic)["Statement"], key=comparable),
            "The topic publishing policy differs from the reviewed environment policy.")
    confirmed = pending = 0
    for sub in pages(sns.list_subscriptions_by_topic, "Subscriptions", TopicArn=topic):
        require(sub.get("TopicArn") == topic and sub.get("Owner") == EXPECTED_ACCOUNT, "A subscription belongs to another account or topic.")
        arn = sub.get("SubscriptionArn", "")
        if re.fullmatch(re.escape(topic) + r":[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", arn):
            confirmed += 1
        else:
            require(arn.lower().replace(" ", "") in ("pendingconfirmation", "deleted"), "A subscription has an unrecognized state.")
            pending += 1
    name = f"agentu-{stage}-operations"
    board = cw.get_dashboard(DashboardName=name)
    require(board.get("DashboardName") == name and board.get("DashboardArn") == f"arn:aws:cloudwatch::{EXPECTED_ACCOUNT}:dashboard/{name}", "The operations dashboard belongs to another environment.")
    require(json.loads(board["DashboardBody"]) == dashboard(stage, values), "The operations dashboard's metric bindings have drifted.")
    rule_name = f"agentu-{stage}-worker"
    rule = events.describe_rule(Name=rule_name)
    require(rule.get("Arn") == f"arn:aws:events:{REGION}:{EXPECTED_ACCOUNT}:rule/{rule_name}" and rule.get("Name") == rule_name
            and rule.get("State") == "ENABLED" and rule.get("ScheduleExpression") == "rate(1 minute)" and not rule.get("EventPattern"), "The scheduled worker is disabled or its schedule has drifted.")
    targets = pages(events.list_targets_by_rule, "Targets", Rule=rule_name)
    require(len(targets) == 1, "The worker schedule has an unexpected target inventory.")
    target = targets[0]
    require(set(target) <= {"Id", "Arn", "Input", "RetryPolicy"}, "The worker target contains unreviewed routing settings.")
    require(target.get("Id") == "AgentuWorker" and target.get("Arn") == f"arn:aws:lambda:{REGION}:{EXPECTED_ACCOUNT}:function:{values['WorkerFunctionName']}"
            and json.loads(target.get("Input", "null")) == {"source": "agentu.worker"}
            and target.get("RetryPolicy") == {"MaximumEventAgeInSeconds": 300, "MaximumRetryAttempts": 2}
            and not any(target.get(k) for k in ("InputPath", "InputTransformer", "RoleArn")), "The scheduled worker target differs from the environment definition.")
    observations = metric_observations(cw, stage, values["ApiId"], at)
    attention = [a["name"] + ": " + a["state"] for a in states if a["state"] != "OK"]
    if not confirmed:
        attention.append("No confirmed business notification subscription.")
    if not all(o["complete"] for o in observations.values()):
        attention.append("Recent metric results are incomplete.")
    if not observations["heartbeats"]["recent_positive_sample"]:
        attention.append("No positive worker heartbeat in the last ten minutes.")
    if not observations["api_requests"]["recent_positive_sample"]:
        attention.append("No observed API traffic in the last ten minutes; run the hosted journeys.")
    if observations["api_server_errors"]["sum"] is None or not observations["api_server_errors"]["recent_sample"]:
        attention.append("Recent API error metrics are missing; zero errors has not been established.")
    elif observations["api_server_errors"]["sum"] > 0:
        attention.append("API server errors were observed in the last fifteen minutes.")
    return {"schema": "agentu.operations.inspection.v1", "account": EXPECTED_ACCOUNT, "region": REGION, "stage": stage,
            "checked_at": at.isoformat(), "configuration_verified": True, "alarms": sorted(states, key=lambda a: a["name"]),
            "subscriptions": {"confirmed": confirmed, "pending_or_deleted": pending}, "metric_window_minutes": 15,
            "observations": observations, "monitoring_observed_ok": not attention, "attention": attention,
            "alert_delivery_verified": False, "cost_controls_verified": False, "hosted_journeys_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--profile")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--require-healthy", action="store_true", help="Exit nonzero for alarms, missing traffic/heartbeats or unconfirmed routing; does not prove delivery")
    args = parser.parse_args()
    result = inspect(clients(args.profile, REGION), args.stage)
    encoded = json.dumps(result, indent=2) + "\n"
    if args.out:
        with args.out.open("x", encoding="utf-8") as output:
            output.write(encoded)
    print(encoded, end="")
    return 1 if args.require_healthy and not result["monitoring_observed_ok"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, TypeError, OSError, ClientError) as error:
        raise SystemExit(str(error))
