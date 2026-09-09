"""
Fetch every post from the #Submissions Discord channel, sort it into a
category based on a "C:Category" tag in the message, and write the result
to docs/data/submissions.json for the website to read.

Runs as a one-shot script (no gateway connection needed) so it's cheap to
run on a GitHub Actions schedule. Uses the Discord REST API directly.

Required environment variables:
  DISCORD_BOT_TOKEN         - bot token (needs "Read Message History" +
                               "View Channel" permission on the channel)
  SUBMISSIONS_CHANNEL_ID    - the numeric channel ID for #Submissions

Optional:
  WORKER_SYNC_URL           - e.g. https://your-worker.workers.dev/api/sync
  WORKER_SYNC_SECRET        - shared secret, must match the Worker's
                               SYNC_SECRET binding
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

DISCORD_API = "https://discord.com/api/v10"

# The categories your titles use, in the form "C:Main", "C:Details", etc.
# Keys are matched case-insensitively; values are the canonical name used
# in the output JSON and on the website.
CATEGORY_MAP = {
    "main": "Main",
    "details": "Details",
    "detail": "Details",
    "design": "Design",
    "user": "User",
    "ui": "UI",
}
CATEGORY_PATTERN = re.compile(r"\bC:\s*([A-Za-z]+)\b", re.IGNORECASE)

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "data" / "submissions.json"


def get_env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "")
    if required and not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def fetch_all_messages(channel_id: str, token: str) -> list[dict]:
    """Page backwards through channel history until there's nothing left."""
    headers = {"Authorization": f"Bot {token}"}
    messages: list[dict] = []
    before = None

    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before

        resp = requests.get(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers=headers,
            params=params,
            timeout=30,
        )

        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 1)
            print(f"Rate limited, sleeping {retry_after}s")
            time.sleep(retry_after)
            continue

        resp.raise_for_status()
        batch = resp.json()

        if not batch:
            break

        messages.extend(batch)
        before = batch[-1]["id"]

        if len(batch) < 100:
            break

    return messages


def extract_category(content: str) -> tuple[str, str]:
    """
    Returns (canonical_category, cleaned_title).
    cleaned_title is the first line of the message with the C:Tag removed,
    for nicer display on the website.
    """
    first_line = content.strip().splitlines()[0] if content.strip() else ""
    match = CATEGORY_PATTERN.search(content)

    if not match:
        return "Uncategorized", first_line

    raw = match.group(1).lower()
    category = CATEGORY_MAP.get(raw, "Uncategorized")

    cleaned_title = CATEGORY_PATTERN.sub("", first_line).strip(" -–—:").strip()
    if not cleaned_title:
        # tag was the whole first line; fall back to the next line if any
        rest = content.strip().splitlines()[1:]
        cleaned_title = rest[0].strip() if rest else "(untitled)"

    return category, cleaned_title


def to_submission(msg: dict) -> dict:
    category, title = extract_category(msg.get("content", ""))
    return {
        "id": msg["id"],
        "author": msg.get("author", {}).get("username", "unknown"),
        "title": title,
        "content": msg.get("content", ""),
        "category": category,
        "timestamp": msg.get("timestamp"),
        "attachments": [a["url"] for a in msg.get("attachments", [])],
        "url": f"https://discord.com/channels/@me/{msg.get('channel_id', '')}/{msg['id']}",
    }


def sync_worker(ids: list[str]) -> None:
    url = get_env("WORKER_SYNC_URL", required=False)
    secret = get_env("WORKER_SYNC_SECRET", required=False)
    if not url or not secret:
        print("WORKER_SYNC_URL / WORKER_SYNC_SECRET not set, skipping sync")
        return

    resp = requests.post(
        url,
        headers={"X-Sync-Secret": secret, "Content-Type": "application/json"},
        json={"ids": ids},
        timeout=30,
    )
    resp.raise_for_status()
    print(f"Synced {len(ids)} valid post IDs to the voting worker")


def main() -> None:
    token = get_env("DISCORD_BOT_TOKEN")
    channel_id = get_env("SUBMISSIONS_CHANNEL_ID")

    raw_messages = fetch_all_messages(channel_id, token)
    # Skip empty/system messages (e.g. pins notices)
    submissions = [
        to_submission(m) for m in raw_messages if m.get("content", "").strip()
    ]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(submissions, indent=2), encoding="utf-8")
    print(f"Wrote {len(submissions)} submissions to {OUTPUT_PATH}")

    sync_worker([s["id"] for s in submissions])


if __name__ == "__main__":
    main()
