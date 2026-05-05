import base64
import json
import logging
import os
from datetime import datetime, timezone
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def _base_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _org_slug(org: str) -> str:
    return org.lower().replace(" ", "_")


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


def _build_raw_message(to: str, subject: str, body: str) -> dict:
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {"raw": raw}


def _save_log(log: dict, slug: str, iteration: int) -> None:
    base = _base_dir()
    sent_dir = os.path.join(base, "data", "sent")
    os.makedirs(sent_dir, exist_ok=True)
    path = os.path.join(sent_dir, f"{slug}_{iteration}.json")
    with open(path, "w") as f:
        json.dump(log, f, indent=2)


def send_email(draft: dict) -> str | None:
    org = draft["org"]
    to = draft["to"]
    subject = draft["subject"]
    body = draft["body"]
    iteration = draft["iteration"]
    slug = _org_slug(org)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # --- Step 1: build service (auth) ---
    try:
        service = _get_gmail_service()
    except Exception as e:
        logger.error("Gmail service unavailable, saving locally: %s", e)
        base = _base_dir()
        fallback_dir = os.path.join(base, "data", "drafts")
        os.makedirs(fallback_dir, exist_ok=True)
        fallback_path = os.path.join(fallback_dir, f"{slug}_{iteration}_fallback.txt")
        with open(fallback_path, "w") as f:
            f.write(f"To: {to}\nSubject: {subject}\n\n{body}")
        _save_log(
            {"org": org, "to": to, "subject": subject, "iteration": iteration,
             "sent_at": now, "status": "saved_locally"},
            slug, iteration,
        )
        return None

    # --- Step 2: send ---
    try:
        raw_message = _build_raw_message(to, subject, body)
        result = service.users().messages().send(userId="me", body=raw_message).execute()
        gmail_message_id = result["id"]
        _save_log(
            {"org": org, "to": to, "subject": subject, "gmail_message_id": gmail_message_id,
             "iteration": iteration, "sent_at": now, "status": "sent"},
            slug, iteration,
        )
        return gmail_message_id
    except Exception as e:
        logger.error("Gmail send failed: %s", e)
        _save_log(
            {"org": org, "to": to, "subject": subject, "iteration": iteration,
             "sent_at": now, "status": "failed", "error": str(e)},
            slug, iteration,
        )
        raise


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    BASE = _base_dir()
    draft_path = os.path.join(BASE, "data", "drafts", "test_org_1.json")

    if not os.path.exists(draft_path):
        sys.exit(f"Draft not found: {draft_path}\nCreate it before running this test.")

    with open(draft_path) as f:
        draft = json.load(f)

    print(f"Sending to: {draft['to']}")
    print(f"Subject:    {draft['subject']}")

    try:
        gmail_id = send_email(draft)
        sent_path = os.path.join(BASE, "data", "sent", f"test_org_1.json")
        log_exists = os.path.exists(sent_path)
        status_ok = False
        if log_exists:
            with open(sent_path) as f:
                log = json.load(f)
            status_ok = log.get("status") == "sent"

        print(f"\ngmail_message_id: {gmail_id}")
        print(f"Sent log created: {log_exists}")
        print(f"Status is 'sent': {status_ok}")
        print("\nPASS" if (gmail_id and log_exists and status_ok) else "\nFAIL")
    except Exception as e:
        print(f"\nException: {e}")
        print("\nFAIL")
