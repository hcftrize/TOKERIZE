"""
post_cantonnews_digest.py
==========================
Posts a weekly digest of Canton news (Monday → Sunday, UTC) to one or more
Telegram topics, once a day.

Weeks are computed in UTC on purpose (not Europe/Paris): every cron-job.org
trigger for this project is set in plain UTC, and keeping the script's own
week/day boundary in UTC too means "today" and "is it Monday" inside the
script always agree with whatever time the trigger actually fired at —
no separate DST-aware reasoning needed to avoid a run near midnight
landing on the wrong side of the week boundary.

Why "every day" for a "weekly" digest: it re-posts the running tally of
*this* week's articles each day (Mon: just Monday's articles, ...,
Sun: the full week), then the window resets Monday. If that's not what's
wanted, drop the "this week" filter for an "only run on Mondays" one —
noted in main() below.

Completeness: every article in news.json whose published_at falls inside
the current Mon-Sun window is included — no per-category cap, no
truncation. The one thing that COULD silently drop news is Telegram's
4096-character message limit: a busy week can easily produce a message
longer than that, and Telegram just rejects the whole send rather than
truncating it. build_digest_texts() below splits into multiple messages
(labeled "Part i/N") whenever needed, splitting only at article/category
boundaries, so nothing gets cut off or dropped — it just spans more than
one message on a busy week.

Empty-week fallback: on a Monday/Tuesday there's often nothing published
yet for the new week. Rather than post "no news", if the current week is
empty the script falls back to showing last week's articles instead, with
a note explaining that's what's happening.

Multiple destinations: all 3 targets are topics inside the *same* group
(@TOKERIZE), just different message_thread_id — e.g. t.me/TOKERIZE/1/44972
is topic 1, t.me/TOKERIZE/3429/44896 is topic 3429. So one chat_id, a list
of thread_ids, same digest message(s) posted once per thread_id.

Scheduling: this script has no time-of-day logic of its own beyond the
UTC week/day boundary described above — it just posts whenever it's run.
The daily trigger lives outside GitHub Actions, same as the rest of the
TOKERIZE workflows: an external scheduler (cron-job.org) calls the GitHub
API's workflow_dispatch endpoint for post-cantonnews-digest.yml, in UTC.
Running it by hand any other time (testing, wanting to re-post) just
posts again — that's intentional. This also makes an extra same-day
catch-up run (e.g. late Sunday, to pick up news published after the
regular run) safe to schedule at any UTC time up to 23:59 UTC without
it accidentally being treated as "already Monday".

Self-cleaning history: to avoid one Telegram message per day piling up for
the same week, each run deletes the *previous* day's digest message(s) in
a thread right before posting the new one — except when today is Monday,
because "yesterday" is then last Sunday's fully-sealed weekly recap, which
must stay forever. Net effect: only one "live" message per thread while
the week is in progress, and exactly one permanent message per week once
Sunday's recap lands. The message_id(s) to delete are captured straight
from Telegram's sendMessage response each time a message is posted (no
manual bookkeeping) and persisted in canton-ecosystem/digest-state.json,
which the workflow commits back to the repo after every run. A delete
failure (message already gone, past Telegram's edit window, missing
rights, ...) is logged but never blocks posting the new message.

No-op when nothing changed: before touching Telegram at all, each run
compares the digest text it just built against a hash of what's already
posted for that thread (also in digest-state.json). If they match — e.g.
a same-day catch-up run (Sunday evening) finds no new articles beyond
what the regular run already posted that day — it skips straight past
both the delete and the repost and moves on to the next thread. This is
what makes an extra catch-up run safe to schedule "just in case": on a
quiet day it does nothing at all instead of shuffling an identical
message to a new message_id for no reason.

Env vars required:
    TELEGRAM_TOKEN               — bot token (same one telegram.py uses)
    CANTONNEWS_DIGEST_CHAT_ID    — target chat: numeric id, or "@TOKERIZE"
                                    (works directly since the group is public
                                    — no need to resolve a numeric id)
    CANTONNEWS_DIGEST_THREAD_IDS — comma-separated topic/thread ids to post
                                    into, e.g. "45100". Leave unset (or
                                    empty) to post once with no thread_id
                                    (plain group / General topic only).

Usage:
    python scripts/post_cantonnews_digest.py
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

NEWS_PATH  = Path("canton-ecosystem/news.json")
STATE_PATH = Path("canton-ecosystem/digest-state.json")
SITE_URL   = "https://cantonnews.org/news"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CANTONNEWS_DIGEST_CHAT_ID", "")
TG_API         = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

_raw_thread_ids = os.environ.get("CANTONNEWS_DIGEST_THREAD_IDS", "").strip()
THREAD_IDS: list[int | None] = (
    [int(t.strip()) for t in _raw_thread_ids.split(",") if t.strip()]
    if _raw_thread_ids else [None]
)

# Telegram hard-rejects any message over 4096 characters. Chunk well under
# that so header/footer/part-label overhead never pushes a chunk over.
CHUNK_BUDGET = 3500


def week_bounds(now_utc: datetime) -> tuple[datetime, datetime]:
    """Monday 00:00:00 -> Sunday 23:59:59, both tz-aware in UTC."""
    monday = now_utc - timedelta(days=now_utc.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    sunday_end = monday + timedelta(days=7) - timedelta(seconds=1)
    return monday, sunday_end


def load_articles() -> list[dict]:
    if not NEWS_PATH.exists():
        return []
    raw = json.loads(NEWS_PATH.read_text(encoding="utf-8"))
    return raw.get("articles", raw) if isinstance(raw, dict) else raw


def articles_in_range(articles: list[dict], start: datetime, end: datetime) -> list[dict]:
    """All articles with published_at inside [start, end] (UTC), oldest first."""
    result = []
    for a in articles:
        iso = a.get("published_at")
        if not iso:
            continue
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue
        if start <= dt <= end:
            result.append(a)
    result.sort(key=lambda a: a.get("published_at") or "")
    return result


def load_state() -> dict:
    """
    {thread_key: {"date": "YYYY-MM-DD", "message_ids": [...]}} — thread_key is
    str(thread_id), or "default" when posting with no thread_id at all.
    Missing/corrupt file just means "nothing to delete yet" — never fatal.
    """
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Warning: could not read {STATE_PATH} — {e}")
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def delete_message(chat_id: str, message_id: int, thread_id: int | None) -> bool:
    """Best-effort delete — logs and returns False rather than raising, so a
    message that's already gone or outside Telegram's delete window never
    stops the new digest from posting."""
    resp = httpx.post(
        f"{TG_API}/deleteMessage",
        json={"chat_id": chat_id, "message_id": message_id},
        timeout=15,
    )
    if resp.status_code == 200:
        return True
    print(f"⚠️ Could not delete message {message_id} (thread {thread_id}): {resp.status_code} {resp.text}")
    return False


def _pack_paragraphs(paragraphs: list[str], budget: int) -> list[str]:
    """
    Greedily pack '\\n\\n'-joined paragraphs into chunks <= budget chars.
    Never splits a paragraph — each one is a category-header+first-article
    pair, or a single article line (see build_digest_texts), so a chunk
    boundary only ever falls between whole articles.
    """
    chunks: list[list[str]] = []
    current: list[str] = []
    cur_len = 0
    for p in paragraphs:
        add_len = len(p) + (2 if current else 0)
        if current and cur_len + add_len > budget:
            chunks.append(current)
            current, cur_len = [], 0
            add_len = len(p)
        current.append(p)
        cur_len += add_len
    if current:
        chunks.append(current)
    return ["\n\n".join(c) for c in chunks]


def build_digest_texts(week_articles: list[dict], monday: datetime, note: str | None = None) -> list[str]:
    """
    Condensed, titles-only digest grouped by category, so a whole week fits
    in as few messages as possible (usually one). A blank line after every
    title keeps it readable on mobile:

        *INSTITUTIONS* :

        Title one [Read more](url)

        Title two [Read more](url)

        *ECOSYSTEM* :

        Title three [Read more](url)

    Articles with no category (parsing miss, or the site card had none)
    land in an "OTHER" group rather than being dropped.

    Returns a LIST of message texts — normally length 1, but longer weeks
    split into several "(Part i/N)" messages rather than risk Telegram
    rejecting one oversized message outright (see module docstring).
    """
    header = f"🗞 *Canton News — Week of {monday.strftime('%d/%m')}*"
    if note:
        header += f"\n_{note}_"

    if not week_articles:
        return ["\n\n".join([header, "_No new Canton news this week so far._"])]

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for a in week_articles:
        cat = (a.get("category") or "OTHER").upper()
        if cat not in groups:
            groups[cat] = []
            order.append(cat)
        groups[cat].append(a)

    # Each paragraph is one "atom" that a chunk boundary may fall after but
    # never inside. The category header is bound to its first article so a
    # split never leaves a bare "*CATEGORY* :" line dangling at chunk end.
    def _article_line(a: dict) -> str:
        return f"{a.get('title', '—')} [Read more]({a.get('url', SITE_URL)})"

    paragraphs = []
    for cat in order:
        items = groups[cat]
        paragraphs.append(f"*{cat}* :\n\n{_article_line(items[0])}")
        for a in items[1:]:
            paragraphs.append(_article_line(a))

    footer = "Full history: /cantonnews"
    body_chunks = _pack_paragraphs(paragraphs, CHUNK_BUDGET)

    if len(body_chunks) == 1:
        return ["\n\n".join([header, body_chunks[0], footer])]

    total = len(body_chunks)
    messages = []
    for i, chunk in enumerate(body_chunks, start=1):
        parts = [f"{header} (Part {i}/{total})", chunk]
        if i == total:
            parts.append(footer)
        messages.append("\n\n".join(parts))
    return messages


def main():
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_TOKEN or CANTONNEWS_DIGEST_CHAT_ID — aborting.")
        sys.exit(1)

    now_utc = datetime.now(timezone.utc)
    monday, sunday_end = week_bounds(now_utc)
    articles = load_articles()

    week_articles = articles_in_range(articles, monday, sunday_end)
    note = None

    if not week_articles:
        # Nothing published yet this week (common on Mon/Tue) — show last
        # week's news instead of an empty digest.
        prev_monday = monday - timedelta(days=7)
        prev_sunday_end = sunday_end - timedelta(days=7)
        prev_articles = articles_in_range(articles, prev_monday, prev_sunday_end)
        if prev_articles:
            week_articles = prev_articles
            note = (f"No Canton news found yet for this week — here's last week's "
                    f"({prev_monday.strftime('%d/%m')}–{prev_sunday_end.strftime('%d/%m')}):")

    messages = build_digest_texts(week_articles, monday, note=note)
    # Fingerprint of this run's content — same for every thread, since they
    # all get the identical digest text. Used below to skip entirely when a
    # run (e.g. the Sunday catch-up) finds nothing has changed.
    content_hash = hashlib.sha256("\x00".join(messages).encode("utf-8")).hexdigest()

    today_str = now_utc.strftime("%Y-%m-%d")
    is_monday = now_utc.weekday() == 0  # Mon=0, UTC — see module docstring: never
                                          # delete "yesterday" on a Monday, since
                                          # that's last Sunday's sealed recap.
    state = load_state()

    failures = 0
    for thread_id in THREAD_IDS:
        key = str(thread_id) if thread_id is not None else "default"
        prev = state.get(key) or {}

        if prev.get("content_hash") == content_hash and prev.get("message_ids"):
            print(f"⏭️  No change for thread {thread_id!r} since the last post — skipping (no delete, no repost).")
            continue

        if not is_monday:
            for mid in prev.get("message_ids", []):
                delete_message(CHAT_ID, mid, thread_id)

        sent_ids = []
        for text in messages:
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
                continue

            suffix = f" ({len(messages)} part(s))" if len(messages) > 1 else ""
            print(f"✅ Posted to thread {thread_id!r}{suffix}.")
            msg_id = (resp.json() or {}).get("result", {}).get("message_id")
            if msg_id is not None:
                sent_ids.append(msg_id)

        # Only overwrite this thread's stored state if at least one message
        # actually sent — a total failure should leave yesterday's (still-live)
        # message_ids in place for the next run to retry deleting.
        if sent_ids:
            state[key] = {"date": today_str, "message_ids": sent_ids, "content_hash": content_hash}

    save_state(state)

    if failures:
        sys.exit(1)

    print(f"✅ Digest posted to {len(THREAD_IDS)} topic(s) "
          f"({len(week_articles)} articles, {len(messages)} message part(s)).")


if __name__ == "__main__":
    main()
