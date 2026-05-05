import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from groq import Groq, RateLimitError

load_dotenv()

logger = logging.getLogger(__name__)


def _org_slug(org: str) -> str:
    return org.lower().replace(" ", "_")


def _build_prompt(
    org: str,
    email: str,
    goal: str,
    org_context: dict,
    iteration: int,
    collected: list,
    gaps: list,
    original_subject: str = "",
) -> str:
    if iteration == 1:
        lines = [
            "You are writing a partnership outreach email on behalf of an agent.",
            "",
            f"Org: {org}",
            f"Goal: {goal}",
        ]
        if org_context.get("website"):
            lines.append(f"Their website: {org_context['website']}")
        lines += [
            "",
            "About this org:",
            org_context.get("summary", ""),
        ]
        key_details = org_context.get("key_details", [])
        if key_details:
            lines += ["", "Key details to reference:"]
            for detail in key_details:
                lines.append(f"- {detail}")
        lines += [
            "",
            f"Write a concise, personalised outreach email to {email} that:",
            "- Opens with a specific reference to what this org does (use the details above)",
            "- Clearly states the goal of this outreach",
            "- Is warm but professional",
            "- Is under 200 words",
            "",
            'Return a JSON object with exactly these keys:',
            '- "subject": a specific, non-generic subject line',
            '- "body": the full email body (plain text, no markdown)',
        ]
    else:
        lines = [
            "You are writing a follow-up partnership outreach email on behalf of an agent.",
            "",
            f"Org: {org}",
            f"Goal: {goal}",
            "What was already shared:",
        ]
        for item in collected:
            lines.append(f"- {item['field']}: {item['value']}")
        lines += ["", "What is still needed:"]
        for item in gaps:
            lines.append(f"- {item['field']} — {item['reason']}")
        lines += [
            "",
            f"Write a concise follow-up email to {email} that:",
            "- Acknowledges what was already shared (thank them specifically)",
            "- Asks only for what is still missing, with context for why it helps",
            "- Does not repeat any already-answered questions",
            "- Is under 150 words",
            "",
            'Return a JSON object with exactly these keys:',
            f'- "subject": use exactly this subject line: "Re: {original_subject}"',
            '- "body": the full email body (plain text, no markdown)',
        ]

    return "\n".join(lines)


def _call_groq(prompt: str) -> dict:
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    def _call():
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert partnership outreach writer. Return only valid JSON with no extra text.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)

    try:
        return _call()
    except RateLimitError as e:
        logger.error("Groq rate limit hit, retrying after 5s")
        time.sleep(5)
        try:
            return _call()
        except Exception as retry_e:
            sys.exit(f"Groq rate limit retry failed: {retry_e}")
    except Exception as e:
        sys.exit(f"Groq call failed: {e}")


def _save_draft(draft: dict, slug: str, iteration: int) -> None:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    drafts_dir = os.path.join(base, "data", "drafts")
    os.makedirs(drafts_dir, exist_ok=True)
    path = os.path.join(drafts_dir, f"{slug}_{iteration}.json")
    with open(path, "w") as f:
        json.dump(draft, f, indent=2)


def draft_email(
    org: str,
    email: str,
    goal: str,
    org_context: dict,
    iteration: int,
    collected: list,
    gaps: list,
) -> dict:
    slug = _org_slug(org)

    original_subject = ""
    if iteration > 1:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        iter1_path = os.path.join(base, "data", "drafts", f"{slug}_1.json")
        if not os.path.exists(iter1_path):
            sys.exit(f"No iteration-1 draft found for {slug} — run iteration 1 first")
        with open(iter1_path) as f:
            iter1_draft = json.load(f)
        original_subject = iter1_draft.get("subject", "")

    prompt = _build_prompt(
        org, email, goal, org_context, iteration, collected, gaps, original_subject
    )
    result = _call_groq(prompt)

    draft = {
        "org": org,
        "to": email,
        "subject": result["subject"],
        "body": result["body"],
        "goal": goal,
        "iteration": iteration,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    _save_draft(draft, slug, iteration)
    return draft


if __name__ == "__main__":
    import json as _json

    logging.basicConfig(level=logging.INFO)

    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def check(label, passed):
        print(f"[{'PASS' if passed else 'FAIL'}] {label}")
        return passed

    with open(os.path.join(BASE, "data/inputs/test_org.json")) as f:
        data = _json.load(f)

    org = data["org"]
    email = data["email"]
    goal = data.get("goal", "")

    stub_context = {
        "website": "habitatphilippines.org",
        "summary": (
            "Habitat for Humanity Philippines builds affordable housing for low-income "
            "families across the country, partnering with communities, government, and "
            "corporate sponsors."
        ),
        "key_details": [
            "partners with local government units",
            "active in Visayas and Mindanao",
            "accepts corporate partnerships",
        ],
    }

    all_pass = True

    print("=" * 50)
    print("TEST 1 — iteration=1 (initial outreach)")
    print("=" * 50)

    draft1 = draft_email(org, email, goal, stub_context, iteration=1, collected=[], gaps=[])

    print(f"Subject: {draft1['subject']}")
    print("Body (first 3 lines):")
    for line in draft1["body"].split("\n")[:3]:
        print(f"  {line}")
    print()

    slug = _org_slug(org)
    path1 = os.path.join(BASE, "data", "drafts", f"{slug}_1.json")
    file_exists = os.path.exists(path1)
    valid_json = False
    if file_exists:
        try:
            with open(path1) as f:
                _json.load(f)
            valid_json = True
        except Exception:
            pass

    all_pass &= check("iteration-1 draft file exists", file_exists)
    all_pass &= check("iteration-1 draft is valid JSON", valid_json)

    print()
    print("=" * 50)
    print("TEST 2 — iteration=2 (follow-up)")
    print("=" * 50)

    collected = [{"field": "decision-maker", "value": "Maria Santos", "found": True}]
    gaps = [{"field": "direct email", "reason": "not mentioned in reply"}]

    draft2 = draft_email(
        org, email, goal, stub_context, iteration=2, collected=collected, gaps=gaps
    )

    print(f"Subject: {draft2['subject']}")
    print("Body (first 3 lines):")
    for line in draft2["body"].split("\n")[:3]:
        print(f"  {line}")
    print()

    body_lower = draft2["body"].lower()
    all_pass &= check('iteration-2 subject starts with "Re:"', draft2["subject"].startswith("Re:"))
    all_pass &= check(
        'iteration-2 body references the gap ("direct email" or "email")',
        "direct email" in body_lower or "email" in body_lower,
    )

    print()
    print("=" * 50)
    print("PASS" if all_pass else "FAIL")
    print("=" * 50)
