"""Release tested code to an existing stack, without infrastructure privileges."""
import argparse
import time
from build import build, OUT
from deploy import clients, publish
from environments import STAGES
from environment_check import verify_environment


def release(session, stage):
    verification = verify_environment(session, stage)
    build()
    functions = session.client("lambda")
    for kind in ("worker", "api"):
        verified = verification["functions"][kind]
        functions.update_function_code(FunctionName=verified["name"], ZipFile=(OUT / "lambda.zip").read_bytes(), RevisionId=verified["revision"])
        for _ in range(12):
            current = functions.get_function_configuration(FunctionName=verified["name"])
            if current.get("LastUpdateStatus") == "Successful":
                break
            if current.get("LastUpdateStatus") == "Failed":
                raise SystemExit("Lambda update failed: " + current.get("LastUpdateStatusReason", "unknown"))
            time.sleep(5)
        else:
            raise SystemExit("Lambda is still updating. Check its status before publishing.")
    publish(session, stage)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=STAGES)
    parser.add_argument("--profile")
    args = parser.parse_args()
    release(clients(args.profile, "eu-west-2"), args.stage)
