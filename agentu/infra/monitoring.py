"""Metric definitions shared by infrastructure and the read-only inspector."""
from environments import EXPECTED_ACCOUNT, REGION


def alarm_specs(stage, values):
    def count(namespace, metric, dimensions, threshold=1):
        return {"Namespace": namespace, "MetricName": metric, "Dimensions": [{"Name": k, "Value": v} for k, v in dimensions.items()],
                "Statistic": "Sum", "Period": 300, "EvaluationPeriods": 1, "DatapointsToAlarm": 1,
                "Threshold": threshold, "ComparisonOperator": "GreaterThanOrEqualToThreshold", "TreatMissingData": "notBreaching"}
    specs = {
        "ApiErrors": ("api-errors", count("AWS/Lambda", "Errors", {"FunctionName": values["FunctionName"]}, 3)),
        "WorkerErrors": ("worker-errors", count("AWS/Lambda", "Errors", {"FunctionName": values["WorkerFunctionName"]})),
        "ApiServerErrors": ("http-server-errors", count("AWS/ApiGateway", "5xx", {"ApiId": values["ApiId"], "Stage": "$default"})),
    }
    for metric in ("FailedRuns", "ExpiryRetries", "Heartbeats"):
        props = count("Agentu/Worker", metric, {"Stage": stage})
        if metric == "Heartbeats":
            props.update(ComparisonOperator="LessThanThreshold", TreatMissingData="breaching")
        specs["Worker" + metric] = ("worker-" + metric.lower(), props)
    latency = count("AWS/ApiGateway", "Latency", {"ApiId": values["ApiId"], "Stage": "$default"}, 2000)
    del latency["Statistic"]
    latency.update(ExtendedStatistic="p95", ComparisonOperator="GreaterThanThreshold", EvaluationPeriods=3,
                   DatapointsToAlarm=2, EvaluateLowSampleCountPercentile="evaluate")
    specs["ApiLatency"] = ("http-latency", latency)
    for key, prefix in (("RecordsTable", "demo"), ("PlatformTable", "platform")):
        for direction in ("Read", "Write"):
            specs[key + direction + "Throttles"] = (prefix + "-" + direction.lower() + "-throttles",
                count("AWS/DynamoDB", direction + "ThrottleEvents", {"TableName": values[key]}))
    return specs


def topic_policy(stage, topic, account=EXPECTED_ACCOUNT, region=REGION):
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "StageAlarmsOnly", "Effect": "Allow", "Principal": {"Service": "cloudwatch.amazonaws.com"},
         "Action": "sns:Publish", "Resource": topic, "Condition": {
             "ArnLike": {"aws:SourceArn": f"arn:aws:cloudwatch:{region}:{account}:alarm:agentu-{stage}-*"},
             "StringEquals": {"aws:SourceAccount": account}}},
        {"Sid": "DenyInsecureClients", "Effect": "Deny", "Principal": "*", "Action": "sns:*", "Resource": topic,
         "Condition": {"Bool": {"aws:SecureTransport": "false", "aws:PrincipalIsAWSService": "false"}}}]}


def dashboard(stage, values, region=REGION, account=EXPECTED_ACCOUNT):
    specs = alarm_specs(stage, values)
    widgets = [{"type": "text", "x": 0, "y": 0, "width": 24, "height": 2, "properties": {
        "markdown": f"# Agentu {stage} operations\nMissing metrics are unknown. Alarm configuration does not verify notification delivery, customer journeys or recovery. Thresholds are an initial operating baseline, not service commitments."}}]
    for index, (suffix, props) in enumerate(specs.values()):
        metric = [props["Namespace"], props["MetricName"]]
        for dimension in props["Dimensions"]:
            metric.extend((dimension["Name"], dimension["Value"]))
        widgets.append({"type": "metric", "x": (index % 2) * 12, "y": 2 + (index // 2) * 6, "width": 12, "height": 6,
            "properties": {"view": "timeSeries", "region": region, "period": 300, "stat": props.get("Statistic", props.get("ExtendedStatistic")),
                "title": f"{stage}: {suffix.replace('-', ' ')}", "metrics": [metric], "liveData": False,
                "annotations": {"horizontal": [{"label": "Alarm threshold", "value": props["Threshold"]}]}}})
    return {"start": "-PT3H", "periodOverride": "inherit", "widgets": widgets}
