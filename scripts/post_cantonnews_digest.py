"""
post_cantonnews_digest.py
==========================
Posts a weekly digest of Canton news (Monday → Sunday, Europe/Paris) to one
or more Telegram topics, once a day around noon Europe/Paris time.

Why "every day" for a "weekly" digest: it re-posts the running tally of
*this* week's articles each day (Mon: just Monday's articles, ...,
Sun: the full week), then the window resets Monday. If that's not what's
wanted, drop the "this week" filter for an "only run on Mondays" one —
noted in main() below.

Multiple destinations: all 3 targets are topics inside the *same* group
(@TOKERIZE), just different message_thread_id — e.g. t.me/TOKERIZE/1/44972
is topic 1, t.me/TOKERIZE/3429/44896 is topic 3429. So one chat_id, a list
of thread_ids, same digest text posted once per thread_id.

Scheduling: this script has no time-of-day logic of its own, and no
memory of whether it already ran today — it just posts whenever it's run.
The daily 12:00 Europe/Paris trigger (already DST-safe, since it's a
local-time schedule) lives outside GitHub Actions, same as the rest of
the TOKERIZE workflows: an external scheduler (cron-job.org) calls the
GitHub API's workflow_dispatch endpoint for post-cantonnews-digest.yml
once a day. Running it by hand any other time (testing, wanting to
re-post) just posts again — that's intentional.

Env vars required:
    TELEGRAM_TOKEN               — bot token (same one telegram.py uses)
    CANTONNEWS_DIGEST_CHAT_ID    — target chat: numeric id, or "@TOKERIZE"
                                    (works directly since the group is public
                                    — no need to resolve a numeric id)
    CANTONNEWS_DIGEST_THREAD_IDS — comma-separated topic/thread ids to post
                                    into, e.g. "1,2,3429". Leave unset (or
                                    empty) to post once with no thread_id
                                    (plain group / General topic only).

Usage:
    python scripts/post_cantonnews_digest.py
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

PARIS = ZoneInfo("Europe/Paris")

NEWS_PATH  = Path("canton-ecosystem/news.json")
SITE_URL   = "https://cantonnews.org/news"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CANTONNEWS_DIGEST_CHAT_ID", "")
TG_API         = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

_raw_thread_ids = os.environ.get("CANTONNEWS_DIGEST_THREAD_IDS", "").strip()
THREAD_IDS: list[int | None] = (
    [int(t.strip()) for t in _raw_thread_ids.split(",") if t.strip()]
    if _raw_thread_ids else [None]
)


def week_bounds(now_paris: datetime) -> tuple[datetime, datetime]:
    """Monday 00:00:00 -> Sunday 23:59:59, both tz-aware in Europe/Paris."""
    monday = now_paris - timedelta(days=now_paris.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    sunday_end = monday + timedelta(days=7) - timedelta(seconds=1)
    return monday, sunday_end


def load_articles() -> list[dict]:
    if not NEWS_PATH.exists():
        return []
    raw = json.loads(NEWS_PATH.read_text(encoding="utf-8"))
    return raw.get("articles", raw) if isinstance(raw, dict) else raw


def build_digest_text(week_articles: list[dict], monday: datetime, sunday_end: datetime) -> str:
    """
    Condensed, titles-only digest grouped by category, so a whole week fits
    in one message:

        *INSTITUTIONS* :
        Title one [Read more](url)
        Title two [Read more](url)

        *ECOSYSTEM* :
        Title three [Read more](url)

    Articles with no category (parsing miss, or the site card had none)
    land in an "OTHER" group rather than being dropped.
    """
    header = f"🗞 *Canton News — Week of {monday.strftime('%d/%m')}*"
    if not week_articles:
        body = "_No new Canton news this week so far._"
    else:
        groups: dict[str, list[dict]] = {}
        order: list[str] = []
        for a in week_articles:
            cat = (a.get("category") or "OTHER").upper()
            if cat not in groups:
                groups[cat] = []
                order.append(cat)
            groups[cat].append(a)

        blocks = []
        for cat in order:
            block_lines = [f"*{cat}* :"]
            for a in groups[cat]:
                title = a.get("title", "—")
                url   = a.get("url", SITE_URL)
                block_lines.append(f"{title} [Read more]({url})")
            blocks.append("\n".join(block_lines))
        body = "\n\n".join(blocks)

    return "\n\n".join([
        header,
        body,
        f"For the full history, use /cantonnews · {SITE_URL}",
    ])


def main():
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_TOKEN or CANTONNEWS_DIGEST_CHAT_ID — aborting.")
        sys.exit(1)

    now_paris = datetime.now(PARIS)
    monday, sunday_end = week_bounds(now_paris)
    articles = load_articles()

    week_articles = []
    for a in articles:
        iso = a.get("published_at")
        if not iso:
            continue
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PARIS)
        except Exception:
            continue
        if monday <= dt <= sunday_end:
            week_articles.append(a)

    # Oldest -> newest within the week, nicer to read as a digest
    week_articles.sort(key=lambda a: a.get("published_at") or "")

    text = build_digest_text(week_articles, monday, sunday_end)

    failures = 0
    for thread_id in THREAD_IDS:
        payload = {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id

        resp = httpx.post(f"{TG_API}/sendMessage", json=payload, timeout=15)

        if resp.status_code != 200:
            print(f"❌ Telegram API error for thread {thread_id}: {resp.status_code} {resp.text}")
            failures += 1
        else:
            print(f"✅ Posted to thread {thread_id!r}.")

    if failures:
        sys.exit(1)

    print(f"✅ Digest posted to {len(THREAD_IDS)} topic(s) ({len(week_articles)} articles this week).")


if __name__ == "__main__":
    main()
