import base64
import json
import logging
import os
import sys
import time

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from groq import Groq, RateLimitError

load_dotenv()

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def _base_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_gmail_service():
    base = _base_dir()
    token_path = os.path.join(base, "token.json")
    creds_path = os.path.join(base, "credentials.json")

    creds = None
    if os.path.exists(token_path):
        with open(token_path) as _f:
            _token_data = json.load(_f)
        granted = set(_token_data.get("scopes", []))
        if set(SCOPES).issubset(granted):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _find_reply(service, email: str, gmail_message_ids: list) -> str | None:
    for message_id in gmail_message_ids:
        msg = service.users().messages().get(
            userId="me", id=message_id, format="metadata"
        ).execute()
        thread_id = msg.get("threadId")
        if not thread_id:
            continue
        results = service.users().messages().list(
            userId="me", q=f"from:{email}"
        ).execute()
        for candidate in results.get("messages", []):
            if candidate["id"] == message_id:
                continue
            meta = service.users().messages().get(
                userId="me", id=candidate["id"], format="metadata"
            ).execute()
            if meta.get("threadId") == thread_id:
                return candidate["id"]
    return None


def _extract_body(message: dict) -> str:
    def _walk(payload: dict) -> str:
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            return ""
        for part in payload.get("parts", []):
            result = _walk(part)
            if result:
                return result
        return ""

    return _walk(message.get("payload", {}))


def _build_groq_prompt(reply_text: str, goal: str) -> str:
    lines = [
        "You are extracting contact information from an email reply.",
        "",
        f"Goal: {goal}",
        "Reply text:",
        "---",
        reply_text,
        "---",
        "",
        "Extract every field the goal asks for. Return a JSON object with exactly these keys:",
        '- "collected": array of objects, each with "field" (string), "value" (string or null), "found" (boolean)',
        '- "confidence": "high" if all fields found, "medium" if some found, "low" if none found',
        "",
        'Do NOT include "raw_reply" — return only the two keys above. No explanation.',
    ]
    return "\n".join(lines)


def _call_groq(reply_text: str, goal: str) -> dict:
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    prompt = _build_groq_prompt(reply_text, goal)

    def _call():
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "You are a contact data extraction assistant. Return only valid JSON with no extra text.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(response.choices[0].message.content)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Groq returned unparseable content — returning low-confidence fallback")
            return {"confidence": "low", "collected": []}

    try:
        return _call()
    except RateLimitError:
        logger.error("Groq rate limit hit, retrying after 5s")
        time.sleep(5)
        try:
            return _call()
        except Exception as e:
            sys.exit(f"Groq rate limit retry failed: {e}")
    except Exception as e:
        sys.exit(f"Groq call failed: {e}")


def parse_reply(
    org: str,
    email: str,
    goal: str,
    gmail_message_ids: list[str],
) -> dict | None:
    try:
        service = _get_gmail_service()
    except Exception as e:
        sys.exit(f"Gmail service unavailable: {e}")

    try:
        reply_id = _find_reply(service, email, gmail_message_ids)
    except Exception as e:
        sys.exit(f"Gmail API error while searching for reply: {e}")

    if reply_id is None:
        return None

    try:
        message = service.users().messages().get(
            userId="me", id=reply_id, format="full"
        ).execute()
    except Exception as e:
        sys.exit(f"Gmail API error fetching reply message {reply_id}: {e}")

    reply_text = _extract_body(message)
    if not reply_text.strip():
        logger.warning("Reply body is empty — returning low-confidence fallback")
        return {"confidence": "low", "collected": [], "raw_reply": ""}
    result = _call_groq(reply_text, goal)
    result["raw_reply"] = reply_text
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    BASE = _base_dir()

    def check(label, passed):
        print(f"[{'PASS' if passed else 'FAIL'}] {label}")
        return passed

    sent_path = os.path.join(BASE, "data", "sent", "test_org_1.json")
    input_path = os.path.join(BASE, "data", "inputs", "test_org.json")

    if not os.path.exists(sent_path):
        sys.exit(f"Sent log not found: {sent_path}")
    if not os.path.exists(input_path):
        sys.exit(f"Input not found: {input_path}")

    with open(sent_path) as f:
        sent = json.load(f)
    with open(input_path) as f:
        inp = json.load(f)

    org = sent["org"]
    email = sent["to"]
    goal = inp["goal"]
    gmail_message_ids = [sent["gmail_message_id"]]

    print(f"Org:          {org}")
    print(f"Reply from:   {email}")
    print(f"Sent msg ID:  {gmail_message_ids[0]}")
    print(f"Goal:         {goal}")
    print()

    result = parse_reply(org, email, goal, gmail_message_ids)

    all_pass = True

    all_pass &= check("reply found (result is not None)", result is not None)

    if result is not None:
        all_pass &= check("confidence is 'high'", result.get("confidence") == "high")

        collected = result.get("collected", [])
        fields_found = {item["field"]: item for item in collected}

        decision_maker = fields_found.get("partnerships decision-maker") or fields_found.get(
            next((k for k in fields_found if "decision" in k.lower()), ""), {}
        )
        direct_email = fields_found.get("direct contact email") or fields_found.get(
            next((k for k in fields_found if "email" in k.lower()), ""), {}
        )

        all_pass &= check(
            "partnerships decision-maker found",
            bool(decision_maker) and decision_maker.get("found"),
        )
        all_pass &= check(
            "direct contact email found",
            bool(direct_email) and direct_email.get("found"),
        )

        print()
        print("Extracted fields:")
        for item in collected:
            status = "FOUND" if item["found"] else "NOT FOUND"
            print(f"  [{status}] {item['field']}: {item['value']}")
        print()
        print(f"Raw reply: {result['raw_reply'][:100]}...")

    print()
    print("=" * 40)
    print("PASS" if all_pass else "FAIL")
    print("=" * 40)
