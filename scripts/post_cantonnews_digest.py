"""
post_cantonnews_digest.py
==========================
Posts a weekly digest of Canton news (Monday → Sunday, Europe/Paris) to a
configured Telegram group, once a day around noon Europe/Paris time.

Why "every day" for a "weekly" digest: it re-posts the running tally of
*this* week's articles each day (Mon: just Monday's articles, ...,
Sun: the full week), then the window resets Monday. If that's not what's
wanted, drop the "this week" filter for an "only run on Mondays" one —
noted in main() below.

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
    CANTONNEWS_DIGEST_CHAT_ID    — target group chat_id (int, may be negative)

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


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PARIS).strftime("%d/%m")
    except Exception:
        return "—"


def build_digest_text(week_articles: list[dict], monday: datetime, sunday_end: datetime) -> str:
    header = f"🗞 *Canton News — Week of {monday.strftime('%d/%m')}*"
    if not week_articles:
        body = "_No new Canton news this week so far._"
    else:
        lines = []
        for a in week_articles:
            title = a.get("title", "—")
            date  = fmt_date(a.get("published_at"))
            url   = a.get("url", SITE_URL)
            lines.append(f'"{title}" ({date}) : [Read more]({url})')
        body = "\n".join(lines)

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

    resp = httpx.post(f"{TG_API}/sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }, timeout=15)

    if resp.status_code != 200:
        print(f"❌ Telegram API error {resp.status_code}: {resp.text}")
        sys.exit(1)

    print(f"✅ Digest posted ({len(week_articles)} articles this week).")


if __name__ == "__main__":
    main()
