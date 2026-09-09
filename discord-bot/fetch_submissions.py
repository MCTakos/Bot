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
  HF_API_TOKEN              - Hugging Face API token (free), enables
                               automatic toxicity moderation of posts
  MODERATION_THRESHOLD      - 0-1 score above which a post is held back
                               (default 0.6)
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

# Open-source toxicity classifier, served via Hugging Face's free hosted
# Inference API — no local model download, no heavy dependencies.
HF_MODEL = "unitary/toxic-bert"
HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
DEFAULT_MODERATION_THRESHOLD = 0.6
HF_MAX_RETRIES = 3


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


def check_moderation(text: str, hf_token: str) -> tuple[bool, dict]:
    """
    Returns (flagged, scores). Fails "open" — if the moderation API errors
    out or is unreachable, the post is treated as not flagged, since a
    moderation outage shouldn't take the whole board down. Any failure is
    printed so it's visible in the Action's logs.
    """
    threshold = float(os.environ.get("MODERATION_THRESHOLD", DEFAULT_MODERATION_THRESHOLD))
    headers = {"Authorization": f"Bearer {hf_token}"}

    for attempt in range(HF_MAX_RETRIES):
        try:
            resp = requests.post(
                HF_API_URL,
                headers=headers,
                json={"inputs": text[:2000], "parameters": {"top_k": None}},
                timeout=30,
            )
        except requests.RequestException as exc:
            print(f"Moderation request failed ({exc}), skipping check for this post")
            return False, {}

        if resp.status_code == 503:
            # Model is cold-starting on Hugging Face's side; wait and retry.
            wait = min(resp.json().get("estimated_time", 10), 30)
            print(f"Moderation model loading, waiting {wait:.0f}s (attempt {attempt + 1})")
            time.sleep(wait)
            continue

        if resp.status_code == 429:
            print("Moderation API rate limited, waiting 5s")
            time.sleep(5)
            continue

        if not resp.ok:
            print(f"Moderation API returned {resp.status_code}, skipping check for this post")
            return False, {}

        result = resp.json()
        # Expected shape: [[{"label": "toxic", "score": 0.98}, ...]]
        scores_list = result[0] if result and isinstance(result[0], list) else result
        scores = {item["label"]: item["score"] for item in scores_list}
        flagged = any(score >= threshold for score in scores.values())
        return flagged, scores

    print("Moderation API did not respond after retries, skipping check for this post")
    return False, {}


def moderate_submissions(submissions: list[dict]) -> list[dict]:
    hf_token = get_env("HF_API_TOKEN", required=False)
    if not hf_token:
        print("HF_API_TOKEN not set, skipping moderation")
        return submissions

    clean = []
    for post in submissions:
        is_flagged, scores = check_moderation(post["content"], hf_token)
        if is_flagged:
            print(f"Flagged and dropped post {post['id']} by @{post['author']}: {scores}")
        else:
            clean.append(post)
        time.sleep(0.5)  # be polite to the free tier

    return clean


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

    clean = moderate_submissions(submissions)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    print(f"Wrote {len(clean)} submissions to {OUTPUT_PATH}")

    sync_worker([s["id"] for s in clean])


if __name__ == "__main__":
    main()
