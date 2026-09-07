"""Business deployment targets. A deployment stage never authorizes real funds."""

EXPECTED_ACCOUNT = "032312375271"
REGION = "eu-west-2"
CONTRACT = "agentu.aws.environment.v1"
ENVIRONMENTS = {
    "sandbox": {"Label": "Sandbox", "LogRetentionDays": 30},
    "demo": {"Label": "Founder demo", "LogRetentionDays": 30},
    "staging": {"Label": "Staging", "LogRetentionDays": 90},
    "production": {"Label": "Production", "LogRetentionDays": 90},
}
STAGES = tuple(ENVIRONMENTS)


def environment(stage):
    if stage not in ENVIRONMENTS:
        raise ValueError("Choose an Agentu environment: " + ", ".join(STAGES))
    return ENVIRONMENTS[stage]
