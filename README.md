# Cold-Emailinator

Automates researching nonprofits and collecting contact information via email outreach.

## Problem Statement

Agencies spend hours manually researching nonprofit contacts, sending cold emails, and following up to get basic information. This agent automates that entire workflow — from researching the org to sending outreach to parsing replies and following up automatically.

The agent does one job: collecting specific, non-public information from organisations via cold email outreach, with up to 3 back-and-forth messages per org.

| Valid Goals                                                           | Invalid Goals                                                           |
| --------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| "Find the partnerships decision-maker and their direct contact email" | "Convince them to partner with us" — action-based, not data collection  |
| "Find the CFO's name or the finance manager's name"                   | "Schedule a meeting" — requires calendar interaction                    |
| "Find the org's headquarters location and primary contact phone"      | "Get their API credentials" — sensitive, won't be shared                |
|                                                                       | "Find their entire organizational chart" — too broad for email exchange |
|                                                                       | "Upload our proposal to their system" — requires authentication         |

## Value

Replaces hours of manual research, email drafting, and follow-up with a single command. Emails are personalised using live web search and Tavily-enriched org context — not generic cold outreach. The agent automatically sends follow-ups when replies are incomplete and outputs everything as structured JSON.

## Why This Approach

Email outreach is the only reliable way to get data that isn't publicly available. Scraping only surfaces what's already public — asking directly gets what isn't. Before sending, the agent enriches its knowledge of the org using Tavily web search and RAG-style context injection, so emails reference real details about the org's work rather than generic templates. This increases the chance of a response.

## Trade-offs

- File-based pipeline over direct function calls — slower, but every intermediate state is inspectable and debuggable
- Groq over Claude/GPT — free tier, slightly less capable but sufficient for structured extraction
- No email verification — extracted emails are flagged as unverified
- Human-triggered parse step — deliberate, because org response time is days not seconds

## Methodology

**Send phase** (`send.py`):

1. Load input file containing org name, contact email, and data collection goal
2. Check if a run file exists at `data/runs/{slug}.json` (slug = lowercased org name with spaces→underscores)
3. If not found: call `search_org(org, goal)` from `modules/searcher.py`
   - `searcher.py` runs two Tavily web searches for the org
   - Extracts the most-cited domain as the website
   - Sends search result snippets to Groq with RAG-style prompt injection
   - Groq returns `{"website": str, "summary": str, "key_details": [str]}`
4. Create run state object and save to `data/runs/{slug}.json` containing: org, email, goal, iteration counter, max iterations (3), org_context, sent message IDs array, collected data array, gaps array
5. Call `draft_email(org, email, goal, org_context, iteration, collected, gaps)` from `modules/drafter.py`
   - Builds a structured prompt for Groq with org context and data collection goal
   - Groq returns `{"subject": str, "body": str}`
   - Saves draft to `data/drafts/{slug}_{iteration}.json`
6. Call `send_email(draft)` from `modules/sender.py`
   - Authenticates with Gmail API using OAuth2 (credentials.json + token.json)
   - Encodes email as MIME, base64-encodes it, sends via `users().messages().send()`
   - Returns Gmail message ID
   - Saves send log to `data/sent/{slug}_{iteration}.json`
7. Append returned message ID to run file's `sent_message_ids` array
8. Write updated run file back to `data/runs/{slug}.json`

**Parse phase** (`parse.py`):

1. Load input file and run file from `data/runs/{slug}.json`
2. Call `parse_reply(org, email, goal, gmail_message_ids)` from `modules/parser.py`
   - Queries Gmail for messages from the target email in the threads of the sent messages
   - Fetches the reply message and extracts plain-text body via `_extract_body()`
   - Sends reply text + goal to Groq
   - Groq returns `{"collected": [{field, value, found}], "confidence": str}`
   - Returns None if no reply found yet
3. If no reply, exit (loop will run parse.py again later)
4. Merge new collected fields with existing collected data in run file (newer overwrites older by field name)
5. Call `evaluate(goal, collected, iteration, max_iterations)` from `modules/evaluator.py`
   - Sends goal + collected fields to Groq
   - Groq returns `{"decision": "complete"|"follow_up_needed", "gaps": [{field, reason}], "reasoning": str}`
6. If decision is "complete" or iteration >= 3:
   - Write final output to `data/parsed/{slug}.json` with org, email, goal, status (complete/incomplete), iterations used, collected fields, completion timestamp
   - Update run file status and save
   - Exit
7. Otherwise:
   - Increment run file's iteration counter
   - Store gaps array in run file
   - Fetch the original iteration-1 draft from `data/drafts/{slug}_1.json` to extract subject line
   - Call `draft_email()` again with iteration > 1 (builds follow-up prompt referencing what was already answered)
   - Call `send_email(draft, in_reply_to_gmail_id=last_sent_id)` to thread the reply
     - `modules/sender.py` looks up the RFC message-id of the sent message and adds `In-Reply-To` + `References` headers to thread the reply in Gmail
   - Append new message ID to run file's `sent_message_ids`
   - Write updated run file and exit

Run file (`data/runs/{slug}.json`) persists all state across send.py and parse.py invocations.

## Tools & Tech

- **Python 3.12** — runtime
- **google-auth-oauthlib, google-auth-httplib2, googleapis-client** — Gmail API authentication and message send/receive via `users().messages().send()` and `users().messages().get()`
- **groq** — LLM (llama-3.3-70b) for email drafting, reply parsing, and goal evaluation; all calls use JSON response format
- **tavily** — web search API for org research; returns snippets embedded in Groq prompts (RAG pattern)
- **python-dotenv** — environment variable management for API keys
- **base64, email.mime** — MIME email encoding for Gmail raw message format

## Cost / Scale / Feasibility

- Groq free tier: 14,400 requests per day, ~30 tokens per second
- Tavily free tier: 1,000 searches per month — 2 searches per org = 500 orgs per month
- Gmail API: 1 billion quota units per day — sending one email costs 100 units = up to 10,000,000 sends per day

## Setup

1. Clone the repo

   ```bash
   git clone <repo-url>
   cd contact-data-agent
   ```

2. Install requirements

   ```bash
   pip install -r requirements.txt
   ```

3. Set up environment variables

   ```bash
   cp .env.example .env
   ```

   Fill in `.env` with:

   ```
   GROQ_API_KEY=your_groq_api_key
   TAVILY_API_KEY=your_tavily_api_key
   ```

4. Set up Gmail credentials
   - Go to [Google Cloud Console](https://console.cloud.google.com)
   - Create a new project
   - Enable Gmail API
   - Create OAuth 2.0 credentials (Desktop application)
   - Download as JSON and save as `credentials.json` in the project root

5. Validate setup
   ```bash
   python validate_setup.py
   ```
   All checks should pass before proceeding.

## Quick Start

Create an input file at `data/inputs/your_org.json`:

```json
{
  "org": "Habitat for Humanity Philippines",
  "email": "info@habitat.org.ph",
  "goal": "Find the partnerships decision-maker and their direct contact email"
}
```

Send initial outreach:

```bash
python send.py --input data/inputs/your_org.json
```

Wait for a reply. Once the org responds to the email, parse the response:

```bash
python parse.py --input data/inputs/your_org.json
```

If the goal isn't satisfied, parse.py will automatically draft and send a follow-up. Run parse.py again after they reply. Repeat until the goal is complete or 3 iterations are reached.

## Example Output

After the agent completes, you'll find the result at `data/parsed/habitat_for_humanity_philippines.json`:

```json
{
  "org": "Habitat for Humanity Philippines",
  "email": "info@habitat.org.ph",
  "goal": "Find the partnerships decision-maker and their direct contact email",
  "status": "complete",
  "iterations": 2,
  "collected": [
    {
      "field": "partnerships decision-maker",
      "value": "Maria Santos",
      "found": true
    },
    {
      "field": "direct contact email",
      "value": "maria.santos@habitat.org.ph",
      "found": true
    }
  ],
  "completed_at": "2026-05-06T14:32:18Z"
}
```

Intermediate files are also saved for inspection:

- `data/runs/habitat_for_humanity_philippines.json` — full state after each send and parse step
- `data/drafts/habitat_for_humanity_philippines_1.json` — initial draft
- `data/drafts/habitat_for_humanity_philippines_2.json` — follow-up draft (if sent)
- `data/sent/habitat_for_humanity_philippines_1.json` — log of what was actually sent to Gmail

## Limitations

- Depends on orgs actually replying — no reply means no data
- Capped at 3 messages per org to avoid spamming and to enforce a hard stop on the loop
- Goal parsing breaks on complex OR/AND conditions
- No email verification — extracted emails may be incorrect
- Single Gmail account only — no multi-sender support
- Reply detection matches by sender address — breaks if org replies from a different address
- Only collects information — cannot take actions like scheduling, form submission, or anything requiring authentication
