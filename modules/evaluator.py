import json
import logging
import os
import sys
import time

from dotenv import load_dotenv
from groq import Groq, RateLimitError

load_dotenv()

logger = logging.getLogger(__name__)


def _build_prompt(goal: str, collected: list) -> str:
    lines = [
        "You are evaluating whether a data-collection goal has been fully satisfied.",
        "",
        f"Goal: {goal}",
        "",
        "Collected data so far:",
    ]
    for item in collected:
        status = "FOUND" if item.get("found") else "MISSING"
        value = item.get("value") or "(none)"
        lines.append(f"- {item['field']}: {value} [{status}]")
    lines += [
        "",
        "Does the collected data fully satisfy the goal?",
        "",
        'Return a JSON object with exactly these keys:',
        '- "decision": "complete" if every field needed by the goal was found, '
        'otherwise "follow_up_needed"',
        '- "gaps": a JSON array of objects with "field" and "reason" keys — '
        'one entry per field still missing; empty array if decision is "complete"',
        '- "reasoning": one sentence explaining the decision',
        "",
        "Return only the JSON object. No explanation.",
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
                    "content": "You are a data-collection evaluator. Return only valid JSON with no extra text.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)

    try:
        return _call()
    except RateLimitError:
        logger.error("Groq rate limit hit, retrying after 5s")
        time.sleep(5)
        try:
            return _call()
        except Exception as retry_e:
            sys.exit(f"Groq rate limit retry failed: {retry_e}")
    except Exception as e:
        sys.exit(f"Groq call failed: {e}")


def evaluate(goal: str, collected: list, iteration: int, max_iterations: int) -> dict:
    if iteration >= max_iterations:
        return {
            "decision": "complete",
            "gaps": [],
            "reasoning": "Max iterations reached — stopping regardless of remaining gaps.",
        }

    prompt = _build_prompt(goal, collected)
    return _call_groq(prompt)


if __name__ == "__main__":
    import logging as _logging

    _logging.basicConfig(level=logging.INFO)

    def check(label, passed):
        print(f"[{'PASS' if passed else 'FAIL'}] {label}")
        return passed

    GOAL = "Find the partnerships decision-maker and their direct contact email"

    all_found = [
        {"field": "partnerships decision-maker", "value": "Maria Santos", "found": True},
        {"field": "direct contact email", "value": "maria@habitat.org.ph", "found": True},
    ]
    one_missing = [
        {"field": "partnerships decision-maker", "value": "Maria Santos", "found": True},
        {"field": "direct contact email", "value": None, "found": False},
    ]

    all_pass = True

    print("=" * 50)
    print("TEST 1 — all fields found → complete")
    print("=" * 50)
    r1 = evaluate(GOAL, all_found, iteration=1, max_iterations=3)
    print(f"  decision:  {r1['decision']}")
    print(f"  gaps:      {r1['gaps']}")
    print(f"  reasoning: {r1['reasoning']}")
    all_pass &= check("decision is 'complete'", r1["decision"] == "complete")
    all_pass &= check("gaps is empty", r1["gaps"] == [])

    print()
    print("=" * 50)
    print("TEST 2 — one field missing → follow_up_needed")
    print("=" * 50)
    r2 = evaluate(GOAL, one_missing, iteration=1, max_iterations=3)
    print(f"  decision:  {r2['decision']}")
    print(f"  gaps:      {r2['gaps']}")
    print(f"  reasoning: {r2['reasoning']}")
    gap_fields = [g["field"] for g in r2.get("gaps", [])]
    all_pass &= check("decision is 'follow_up_needed'", r2["decision"] == "follow_up_needed")
    all_pass &= check("gap identifies missing field", len(r2["gaps"]) >= 1)
    all_pass &= check(
        "gap field is 'direct contact email'",
        any("email" in f.lower() for f in gap_fields),
    )

    print()
    print("=" * 50)
    print("TEST 3 — max iterations reached → complete regardless")
    print("=" * 50)
    r3 = evaluate(GOAL, one_missing, iteration=3, max_iterations=3)
    print(f"  decision:  {r3['decision']}")
    print(f"  gaps:      {r3['gaps']}")
    print(f"  reasoning: {r3['reasoning']}")
    all_pass &= check("decision is 'complete' (forced)", r3["decision"] == "complete")
    all_pass &= check("gaps is empty (forced)", r3["gaps"] == [])

    print()
    print("=" * 50)
    print("PASS" if all_pass else "FAIL")
    print("=" * 50)
