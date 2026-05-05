import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

from modules.drafter import draft_email
from modules.evaluator import evaluate
from modules.parser import parse_reply
from modules.sender import send_email

logging.basicConfig(level=logging.WARNING)


def _slug(org: str) -> str:
    return org.lower().replace(" ", "_")


def _base_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _run_path(slug: str) -> str:
    return os.path.join(_base_dir(), "data", "runs", f"{slug}.json")


def _merge_collected(existing: list, new: list) -> list:
    merged = {item["field"]: item for item in existing}
    for item in new:
        merged[item["field"]] = item
    return list(merged.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()

    try:
        with open(args.input) as f:
            inp = json.load(f)
    except FileNotFoundError:
        sys.exit(f"Input file not found: {args.input}")
    except json.JSONDecodeError as e:
        sys.exit(f"Invalid JSON in {args.input}: {e}")

    for field in ("org", "email", "goal"):
        if not inp.get(field):
            sys.exit(f"Input file missing required field: '{field}'")

    org = inp["org"]
    email = inp["email"]
    goal = inp["goal"]
    slug = _slug(org)
    run_path = _run_path(slug)

    if not os.path.exists(run_path):
        sys.exit(
            f"No run file found for '{org}'.\n"
            f"Run send.py first:  python send.py --input {args.input}"
        )

    with open(run_path) as f:
        run = json.load(f)

    result = parse_reply(org, email, goal, run["sent_message_ids"])

    if result is None:
        print(f"[{org}] — no reply yet. Run parse.py again when reply arrives.")
        return

    run["collected"] = _merge_collected(run["collected"], result.get("collected", []))

    eval_result = evaluate(goal, run["collected"], run["iteration"], run["max_iterations"])
    decision = eval_result["decision"]

    if decision == "complete" or run["iteration"] >= run["max_iterations"]:
        final_status = "complete" if decision == "complete" else "incomplete"

        parsed_dir = os.path.join(_base_dir(), "data", "parsed")
        os.makedirs(parsed_dir, exist_ok=True)
        parsed = {
            "org": org,
            "email": email,
            "goal": goal,
            "status": final_status,
            "iterations": run["iteration"],
            "collected": run["collected"],
            "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        parsed_path = os.path.join(parsed_dir, f"{slug}.json")
        with open(parsed_path, "w") as f:
            json.dump(parsed, f, indent=2)

        run["status"] = final_status
        with open(run_path, "w") as f:
            json.dump(run, f, indent=2)

        print(f"[{org}] — goal satisfied. Output saved to data/parsed/{slug}.json")

    else:
        run["iteration"] += 1
        run["gaps"] = eval_result["gaps"]
        with open(run_path, "w") as f:
            json.dump(run, f, indent=2)

        draft = draft_email(
            org, email, goal,
            run["org_context"],
            run["iteration"],
            run["collected"],
            run["gaps"],
        )
        gmail_message_id = send_email(draft)

        if gmail_message_id:
            run["sent_message_ids"].append(gmail_message_id)
        with open(run_path, "w") as f:
            json.dump(run, f, indent=2)

        print(
            f"[{org}] — follow-up sent (iteration {run['iteration']}). "
            f"Run parse.py again when they reply."
        )


if __name__ == "__main__":
    main()
