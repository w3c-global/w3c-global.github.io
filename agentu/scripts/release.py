"""Release tested code to an existing stack, without infrastructure privileges."""
import argparse
import time
from build import build, OUT
from deploy import clients, outputs, publish


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=["sandbox", "demo"])
    parser.add_argument("--profile")
    args = parser.parse_args()
    session = clients(args.profile, "eu-west-2")
    stack, values = outputs(session, args.stage)
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise SystemExit("Infrastructure is not ready for a release.")
    build()
    functions = session.client("lambda")
    existing = functions.get_function_configuration(FunctionName=values["FunctionName"])
    functions.update_function_code(FunctionName=values["FunctionName"], ZipFile=(OUT / "lambda.zip").read_bytes(), RevisionId=existing["RevisionId"])
    for _ in range(12):
        current = functions.get_function_configuration(FunctionName=values["FunctionName"])
        if current.get("LastUpdateStatus") == "Successful":
            break
        if current.get("LastUpdateStatus") == "Failed":
            raise SystemExit("Lambda update failed: " + current.get("LastUpdateStatusReason", "unknown"))
        time.sleep(5)
    else:
        raise SystemExit("Lambda is still updating. Check its status before publishing.")
    publish(session, args.stage)
