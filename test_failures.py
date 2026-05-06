#!/usr/bin/env python3
"""
Failure-case regression tests for Contact Data Agent.
All external calls are mocked — no real emails, no real API calls.

Run: python test_failures.py
Exit code 0 = all pass, 1 = at least one failure.
"""

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_results: list[bool] = []


def check(label: str, passed: bool, reason: str = "") -> bool:
    status = "PASS" if passed else "FAIL"
    detail = f" — {reason}" if reason else ""
    print(f"[{status}] {label}{detail}")
    _results.append(passed)
    return passed


# ---------------------------------------------------------------------------
# Case 1 — Groq rate limit: retries once after 5 s, exits with clear error
# ---------------------------------------------------------------------------

def test_1_groq_rate_limit() -> None:
    from modules import evaluator as ev

    # Use a fake subclass so `except RateLimitError` in evaluator.py catches it
    # when we patch the name in that module's namespace.
    class _FakeRateLimit(Exception):
        pass

    call_count = [0]

    def _fake_create(*_a, **_kw):
        call_count[0] += 1
        if call_count[0] == 1:
            raise _FakeRateLimit("rate limit on first attempt")
        raise Exception("rate limit persists on retry")

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = _fake_create
    sleep_args: list[float] = []

    with patch("modules.evaluator.RateLimitError", _FakeRateLimit), \
         patch("modules.evaluator.Groq", return_value=mock_client), \
         patch("modules.evaluator.time.sleep", side_effect=lambda s: sleep_args.append(s)):
        try:
            ev._call_groq("test prompt")
            check("Case 1: Groq rate limit retries once then exits", False,
                  "expected SystemExit but got no exception")
            return
        except SystemExit as e:
            msg = str(e)

    retried_twice = call_count[0] == 2
    slept_five = sleep_args == [5]
    has_clear_msg = "retry" in msg.lower() or "rate" in msg.lower()

    check(
        "Case 1: Groq rate limit retries once then exits with clear error",
        retried_twice and slept_five and has_clear_msg,
        f"calls={call_count[0]} sleep_args={sleep_args} msg={msg!r}",
    )


# ---------------------------------------------------------------------------
# Case 2 — Missing input file: fails immediately naming the missing file
# ---------------------------------------------------------------------------

def test_2_missing_input_file() -> None:
    import importlib
    import send as send_mod

    importlib.reload(send_mod)  # reset argparse state between test runs
    fake_path = "/tmp/__cf_agent_nonexistent_input__.json"

    with patch("sys.argv", ["send.py", "--input", fake_path]):
        try:
            send_mod.main()
            check("Case 2: Missing input file exits naming the file", False,
                  "expected SystemExit")
        except SystemExit as e:
            msg = str(e)
            check(
                "Case 2: Missing input file exits naming the file",
                fake_path in msg,
                f"msg={msg!r}",
            )


# ---------------------------------------------------------------------------
# Case 3 — Gmail API failure: logs "failed" status + falls back to local save
# ---------------------------------------------------------------------------

def test_3_gmail_api_failure() -> None:
    from modules import sender

    draft = {
        "org": "TestOrg",
        "to": "test@example.com",
        "subject": "Hello from agent",
        "body": "This is the email body.",
        "goal": "find CEO",
        "iteration": 1,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "data", "sent"))
        os.makedirs(os.path.join(tmpdir, "data", "drafts"))

        mock_service = MagicMock()
        # Auth succeeds; the actual .send().execute() raises
        (mock_service.users.return_value
                     .messages.return_value
                     .send.return_value
                     .execute.side_effect) = Exception("Gmail HTTP 500")

        with patch("modules.sender._get_gmail_service", return_value=mock_service), \
             patch("modules.sender._base_dir", return_value=tmpdir):
            result = sender.send_email(draft)

        log_path = os.path.join(tmpdir, "data", "sent", "testorg_1.json")
        log_status_failed = False
        if os.path.exists(log_path):
            with open(log_path) as f:
                log_status_failed = json.load(f).get("status") == "failed"

        fallback_path = os.path.join(tmpdir, "data", "drafts", "testorg_1_fallback.txt")
        fallback_has_body = False
        if os.path.exists(fallback_path):
            with open(fallback_path) as f:
                fallback_has_body = "This is the email body." in f.read()

        check(
            "Case 3: Gmail failure logs 'failed' status and saves locally",
            log_status_failed and fallback_has_body and result is None,
            f"log_status_failed={log_status_failed} "
            f"fallback_has_body={fallback_has_body} "
            f"returns_none={result is None}",
        )


# ---------------------------------------------------------------------------
# Case 4 — Empty or unparseable reply: confidence "low", all fields found: false
# ---------------------------------------------------------------------------

def test_4_empty_reply() -> None:
    from modules import parser as pr

    # --- Subcase A: empty reply body skips Groq and returns low-confidence ---
    mock_service = MagicMock()
    (mock_service.users.return_value
                 .messages.return_value
                 .get.return_value
                 .execute.return_value) = {
        "payload": {"mimeType": "text/plain", "body": {"data": ""}}
    }

    with patch("modules.parser._get_gmail_service", return_value=mock_service), \
         patch("modules.parser._find_reply", return_value="fake_reply_id"):
        result_a = pr.parse_reply("TestOrg", "test@example.com", "find CEO", ["sent_id"])

    sub_a = (
        result_a is not None
        and result_a.get("confidence") == "low"
        and result_a.get("collected") == []
    )

    # --- Subcase B: Groq returns invalid JSON → low-confidence fallback ---
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "NOT VALID JSON !!!"
    mock_client.chat.completions.create.return_value = mock_resp

    with patch("modules.parser.Groq", return_value=mock_client):
        result_b = pr._call_groq("some reply text", "find CEO")

    sub_b = (
        result_b is not None
        and result_b.get("confidence") == "low"
    )

    check(
        "Case 4: Empty/unparseable reply returns confidence='low', collected=[]",
        sub_a and sub_b,
        f"subA(empty_body)={sub_a} subB(bad_json)={sub_b}",
    )


# ---------------------------------------------------------------------------
# Case 5 — Malformed input JSON: fails immediately naming the missing field
# ---------------------------------------------------------------------------

def test_5_malformed_input_json() -> None:
    import importlib
    import send as send_mod

    importlib.reload(send_mod)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        # "goal" field deliberately omitted
        json.dump({"org": "TestOrg", "email": "test@example.com"}, f)
        tmp_path = f.name

    try:
        with patch("sys.argv", ["send.py", "--input", tmp_path]):
            try:
                send_mod.main()
                check("Case 5: Malformed JSON exits naming the missing field", False,
                      "expected SystemExit")
            except SystemExit as e:
                msg = str(e)
                check(
                    "Case 5: Malformed JSON exits naming the missing field",
                    "goal" in msg,
                    f"msg={msg!r}",
                )
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Case 6 — Run file missing when parse.py runs: exits with "run send.py first"
# ---------------------------------------------------------------------------

def test_6_missing_run_file() -> None:
    import importlib
    import parse as parse_mod

    importlib.reload(parse_mod)

    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "data", "runs"))

        input_path = os.path.join(tmpdir, "input.json")
        with open(input_path, "w") as f:
            json.dump({"org": "NoRunOrg", "email": "x@x.com", "goal": "find CEO"}, f)

        # run file is intentionally absent
        with patch("sys.argv", ["parse.py", "--input", input_path]), \
             patch("parse._base_dir", return_value=tmpdir):
            try:
                parse_mod.main()
                check("Case 6: Missing run file exits with 'run send.py first'", False,
                      "expected SystemExit")
            except SystemExit as e:
                msg = str(e)
                check(
                    "Case 6: Missing run file exits with 'run send.py first'",
                    "send.py" in msg.lower(),
                    f"msg={msg!r}",
                )


# ---------------------------------------------------------------------------
# Case 7 — Max iterations: evaluator forces "complete", output status "incomplete"
# ---------------------------------------------------------------------------

def test_7_max_iterations() -> None:
    # Part A: evaluator short-circuits at max iterations without calling Groq
    from modules.evaluator import evaluate
    ev_result = evaluate("find CEO", [], iteration=3, max_iterations=3)
    part_a = ev_result["decision"] == "complete"

    # Part B: parse.py writes status="incomplete" when iteration == max_iterations
    import importlib
    import parse as parse_mod

    importlib.reload(parse_mod)

    with tempfile.TemporaryDirectory() as tmpdir:
        org = "MaxIterOrg"
        slug = "maxiterorg"

        os.makedirs(os.path.join(tmpdir, "data", "runs"))
        os.makedirs(os.path.join(tmpdir, "data", "parsed"))

        input_path = os.path.join(tmpdir, "input.json")
        with open(input_path, "w") as f:
            json.dump({"org": org, "email": "test@test.com", "goal": "find CEO"}, f)

        run_path = os.path.join(tmpdir, "data", "runs", f"{slug}.json")
        with open(run_path, "w") as f:
            json.dump({
                "org": org, "email": "test@test.com", "goal": "find CEO",
                "status": "awaiting_reply", "iteration": 3, "max_iterations": 3,
                "org_context": {}, "sent_message_ids": ["fake_id"],
                "collected": [], "gaps": [],
            }, f)

        # parse_reply mocked so no Gmail call; evaluate runs real (short-circuits cleanly)
        with patch("sys.argv", ["parse.py", "--input", input_path]), \
             patch("parse._base_dir", return_value=tmpdir), \
             patch("parse.parse_reply",
                   return_value={"confidence": "low", "collected": [], "raw_reply": ""}):
            parse_mod.main()

        parsed_path = os.path.join(tmpdir, "data", "parsed", f"{slug}.json")
        file_exists = os.path.exists(parsed_path)
        status_incomplete = False
        if file_exists:
            with open(parsed_path) as f:
                status_incomplete = json.load(f).get("status") == "incomplete"

        part_b = file_exists and status_incomplete

        check(
            "Case 7: Max iterations → evaluator forces complete, output saved as 'incomplete'",
            part_a and part_b,
            f"evaluator_forces_complete={part_a} "
            f"file_exists={file_exists} "
            f"status_incomplete={status_incomplete}",
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Contact Data Agent — failure case tests")
    print("=" * 60)

    test_1_groq_rate_limit()
    test_2_missing_input_file()
    test_3_gmail_api_failure()
    test_4_empty_reply()
    test_5_malformed_input_json()
    test_6_missing_run_file()
    test_7_max_iterations()

    print("=" * 60)
    passed = sum(_results)
    total = len(_results)
    print(f"{passed}/{total} passed")
    sys.exit(0 if all(_results) else 1)
