import argparse
import json
import logging
import os
import sys

from modules.drafter import draft_email
from modules.searcher import search_org
from modules.sender import send_email

logging.basicConfig(level=logging.WARNING)

MAX_ITERATIONS = 3


def _slug(org: str) -> str:
    return org.lower().replace(" ", "_")


def _base_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _run_path(slug: str) -> str:
    return os.path.join(_base_dir(), "data", "runs", f"{slug}.json")


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
        print(f"[{org}] — searching for org context...")
        org_context = search_org(org, goal)
        run = {
            "org": org,
            "email": email,
            "goal": goal,
            "status": "awaiting_reply",
            "iteration": 1,
            "max_iterations": MAX_ITERATIONS,
            "org_context": org_context,
            "sent_message_ids": [],
            "collected": [],
            "gaps": [],
        }
    else:
        with open(run_path) as f:
            run = json.load(f)

    iteration = run["iteration"]

    draft = draft_email(
        org, email, goal,
        run["org_context"],
        iteration,
        run["collected"],
        run["gaps"],
    )
    print(f"[{org}] — iteration {iteration} — draft generated")

    gmail_message_id = send_email(draft)
    print(f"[{org}] — email sent to {email}")

    if gmail_message_id:
        run["sent_message_ids"].append(gmail_message_id)

    os.makedirs(os.path.dirname(run_path), exist_ok=True)
    with open(run_path, "w") as f:
        json.dump(run, f, indent=2)

    print(f"[{org}] — run state saved to data/runs/{slug}.json")


if __name__ == "__main__":
    main()
