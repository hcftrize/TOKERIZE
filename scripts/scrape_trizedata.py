#!/usr/bin/env python3
"""
scrape_trizedata.py
=====================
Scrapes THREE T-RIZE sources in one run → trizedata/news.json:

  1. Learning Center ("All Resources" filter)
     https://www.t-rize.io/learn/learning-center?filter=all
     News / Article / Use Case items published by T-RIZE.

  2. T-RIZE Talks
     https://www.t-rize.io/t-rize-talks
     Short team-spotlight interviews — one per week-ish. Folded into the
     same file/array as a "TALKS" category rather than a separate JSON:
     the team is small and talks are a light, occasional feature, not
     worth a whole parallel pipeline (same spirit as cantonnews.org's
     news.json, just sources merged into one output instead of several).

  3. Upcoming Events (upcoming + the "Past Events" toggle)
     https://www.t-rize.io/upcoming-events
     Conferences T-RIZE is/was attending — folded in as "EVENTS" category
     entries so the monthly recap article can list which events fall in
     that month. Not individually clickable in our own articles (per
     product decision — these cards actually link out to each event's own
     external site, which we capture as a bonus `external_url` field, but
     the recap doesn't need to turn that into a "Read more").

Why one script / one workflow for all three pages: they all live on
t-rize.io, all need a real rendered browser (t-rize.io 403s any plain HTTP
fetch — same as cantonnews.org), and scraping them back-to-back in one
Playwright session is simpler to operate (one script, one cron-job.org
entry) than maintaining several near-identical scrapers for a small site
like this.

Output shape — one flat `articles` array, same {title, category,
description, url, published_at, image} core shape for every item
regardless of source (events add two extra fields, see below):

  - Learning Center item:
      title/category  -> from the listing card (category = the small
                          badge: "News" / "Article" / "Use Case", upper-
                          cased).
      description/
      published_at/
      image            -> from the article's own page meta tags
                          (og:description, article:published_time,
                          og:image) — the listing card itself shows no
                          date or description at all, unlike
                          cantonnews.org's cards.
  - T-RIZE Talks item:
      title            -> synthesized as "T-RIZE Talks: <Name>" (matches
                          the site's own <title> convention for these
                          pages).
      category         -> always "TALKS".
      published_at     -> from the listing card directly (it shows the
                          date right there, unlike Learning Center) — no
                          detail-page fetch needed just for this.
      description      -> "<Role> — <og:description intro>", i.e. the
                          person's role folded in front of the site's own
                          one-line blurb about that talk.
      image            -> og:image from the detail page (a dedicated
                          portrait, e.g. /share/talks/<slug>.jpg).
  - Event item:
      title            -> the event name (e.g. "TOKEN2049").
      category         -> always "EVENTS".
      url              -> a synthetic, non-navigable key of the form
                          "event:<slug>:<start_date>" — these cards have
                          no T-RIZE article behind them, so there's
                          nothing of ours to link to (see external_url).
                          This pseudo-URL is still what the diffing below
                          keys off of, same as a real URL for the other
                          two sources.
      description      -> the event's location (e.g. "Singapore").
      published_at/
      start_date       -> the event's first day, ISO (YYYY-MM-DD).
      end_date         -> the event's last day, ISO — same as start_date
                          for a single-day event. Both are needed (not
                          just one date) because an event like SIBOS can
                          span a month boundary (Sep 28 - Oct 1) and the
                          monthly recap generator needs to know every
                          month an event touches, not just where it starts.
      image            -> always null (no per-event portrait/cover here).
      external_url     -> the event's own official site, e.g.
                          https://www.token2049.com/singapore — bonus
                          data, scraped for free since the card happens to
                          link there, but not required for the recap.
      Events are deduplicated by (name, start_date): the page renders a
      separate promo card per team member attending the same event (one
      photo each), so e.g. "SIBOS Miami" appears twice in the raw DOM for
      one real event — only kept once.
      No detail-page fetch for events at all — every field comes straight
      off the listing card (including the "Past Events" ones, revealed by
      clicking that section's toggle before reading). So unlike Learning
      Center/Talks, an already-known event is simply overwritten with
      today's scrape every run rather than keeping a cached description —
      there's no extra network cost either way, and it means a typo fix
      on the site (date, location) shows up immediately instead of being
      stuck from the first time we saw it.

Diffing (identical philosophy to scrape_cantonnews.py), keyed by URL
(or the synthetic pseudo-URL for events):
  - new URL     -> fetch its detail page once for description/image (and
                   published_at too, for Learning Center items only;
                   Talks already got it for free from the listing card;
                   Events never need a detail fetch at all, see above).
  - known URL   -> title/category (and published_at for Talks) refreshed
                   from the current listing pass; description/image/
                   published_at-for-news kept as previously captured, no
                   re-fetch. Events are always fully refreshed (cheap).
  - known URL, missing from the live listings -> dropped (no ghost
                   entries, matches what the site itself shows).

Safety valve: if a full re-scrape finds far fewer total items than are
already on record, that almost certainly means a page didn't load fully
or its layout changed — NOT that most of the content vanished overnight.
In that case the script aborts without writing anything, same as
scrape_cantonnews.py (this scraper also auto-promotes dev -> main with no
human review step, so it must never write a corrupted file).

Usage:
    python scripts/scrape_trizedata.py

Output:
    trizedata/news.json
"""
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

BASE_URL        = "https://www.t-rize.io"
LEARN_URL       = BASE_URL + "/learn/learning-center?filter=all"
TALKS_URL       = BASE_URL + "/t-rize-talks"
EVENTS_URL      = BASE_URL + "/upcoming-events"
OUT_DIR         = Path("trizedata")
JSON_PATH       = OUT_DIR / "news.json"

# Be polite between detail-page fetches
DETAIL_DELAY = 0.8
# Delay after clicking "Next page" on Learning Center, to let the grid
# re-render before we read the next batch of cards.
PAGINATION_DELAY_MS = 1200
# Hard stop on the Learning Center pagination loop in case the "disabled"
# detection below ever fails to trip (e.g. the site changes how it marks
# the last page) — we'd rather stop early one day than loop forever.
MAX_LEARNING_CENTER_PAGES = 50

# If a fresh scrape finds fewer than this fraction of the previously-known
# total item count (both sources combined), treat it as a broken/partial
# scrape and abort instead of writing (see safety-valve note above).
MIN_RATIO_OF_EXISTING = 0.5


def normalize_url(href: str) -> str:
    """Absolute, query/fragment-free URL."""
    url = href if href.startswith("http") else BASE_URL + "/" + href.lstrip("/")
    return url.split("?")[0].split("#")[0].rstrip("/")


def _dismiss_cookie_banner(page) -> None:
    """Best-effort: decline the cookie banner so it can't intercept a click
    on the pagination arrows. Safe to call even if it's absent."""
    return page.locator("text=Decline").first.click(timeout=3_000)


# ─────────────────────────── Learning Center ───────────────────────────────

def _parse_learning_card_text(raw_text: str) -> dict | None:
    """
    Split a Learning Center card's inner_text() into {category, title}.

    Observed shape is exactly two lines: a short badge ("News", "Article",
    "Use Case", ...) followed by the title. We tell the badge apart from
    the title by length/word-count — every badge seen so far is 1-3 words,
    while a real title is always a full phrase. This also naturally skips
    the non-article "Tokenization 101" promo tile, which has no href
    matching /news/ in the first place and so never reaches this function.
    """
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    if not lines:
        return None

    if len(lines) >= 2 and len(lines[0].split()) <= 3:
        category = lines[0].upper()
        title = " ".join(lines[1:]).strip()
    else:
        category = "OTHER"
        title = " ".join(lines).strip()

    if len(title) < 5:
        return None
    return {"category": category, "title": title}


async def scrape_learning_center(page) -> list[dict]:
    """Returns [{title, category, url}] for every real article/use-case/
    news card under the "All Resources" filter, walking every page via the
    Next-page arrow until it's disabled (or MAX_LEARNING_CENTER_PAGES is
    hit, see module docstring)."""
    await page.goto(LEARN_URL, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(1_500)
    try:
        await _dismiss_cookie_banner(page)
    except PWTimeout:
        pass  # banner wasn't there / already dismissed — fine either way

    next_btn = page.get_by_role("button", name=re.compile("next page", re.I))

    items, seen = [], set()
    for page_num in range(1, MAX_LEARNING_CENTER_PAGES + 1):
        cards = await page.query_selector_all("a[href^='/news/']")
        page_new = 0
        for a in cards:
            href = await a.get_attribute("href") or ""
            url = normalize_url(href)
            if url in seen:
                continue
            raw_text = await a.inner_text() or ""
            parsed = _parse_learning_card_text(raw_text)
            if not parsed:
                continue
            seen.add(url)
            items.append({**parsed, "url": url})
            page_new += 1
        print(f"  learning-center page {page_num}: {page_new} new card(s) "
              f"({len(items)} total so far)")

        if await next_btn.count() == 0 or await next_btn.is_disabled():
            break
        await next_btn.click()
        await page.wait_for_timeout(PAGINATION_DELAY_MS)
    else:
        print(f"  ⚠️ hit MAX_LEARNING_CENTER_PAGES ({MAX_LEARNING_CENTER_PAGES}) "
              f"— stopping anyway; raise the cap if the site genuinely has more pages.")

    return items


# ─────────────────────────────── Talks ──────────────────────────────────────

DATE_RE = re.compile(r"[A-Z][a-z]+ \d{1,2}, \d{4}")


def _parse_talk_card_text(raw_text: str) -> dict | None:
    """
    Split a T-RIZE Talks card's inner_text() into {published_at, name, role}.
    Observed shape is exactly three lines: "<Month D, YYYY>", "<Name>",
    "<Role>". published_at is converted to ISO (date-only, matching what
    the site's own article:published_time meta tag gives for these pages).
    """
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    if len(lines) < 3 or not DATE_RE.match(lines[0]):
        return None
    try:
        published_at = datetime.strptime(lines[0], "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None
    name, role = lines[1], " ".join(lines[2:])
    return {"published_at": published_at, "name": name, "role": role}


async def scrape_talks(page) -> list[dict]:
    """Returns [{title, category, url, published_at, role}] for every talk
    on the single, non-paginated /t-rize-talks page."""
    await page.goto(TALKS_URL, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(1_500)
    try:
        await _dismiss_cookie_banner(page)
    except PWTimeout:
        pass

    items, seen = [], set()
    cards = await page.query_selector_all("a[href^='/t-rize-talks/']")
    for a in cards:
        href = await a.get_attribute("href") or ""
        url = normalize_url(href)
        if url in seen or url == normalize_url("/t-rize-talks"):
            continue
        raw_text = await a.inner_text() or ""
        parsed = _parse_talk_card_text(raw_text)
        if not parsed:
            continue
        seen.add(url)
        items.append({
            "title": f"T-RIZE Talks: {parsed['name']}",
            "category": "TALKS",
            "url": url,
            "published_at": parsed["published_at"],
            "role": parsed["role"],
        })
    print(f"  t-rize-talks: {len(items)} talk(s) found")
    return items


# ─────────────────────────────── Events ──────────────────────────────────────

EVENT_DATE_RE = re.compile(
    r"^(?P<start_month>[A-Z][a-z]+) (?P<start_day>\d{1,2})"
    r"(?:\s*-\s*(?:(?P<end_month>[A-Z][a-z]+) )?(?P<end_day>\d{1,2}))?"
    r",\s*(?P<year>\d{4})$"
)
_MONTH_NUM = {datetime(2000, m, 1).strftime("%B"): m for m in range(1, 13)}


def _parse_event_date_range(text: str) -> tuple[str, str] | tuple[None, None]:
    """
    Parses a T-RIZE events-page date string into (start_date, end_date) as
    ISO "YYYY-MM-DD" strings. Handles the three shapes actually seen on the
    site:
      "October 7 - 8, 2026"            -> same month, day range
      "September 28 - October 1, 2026" -> crosses a month boundary
      "September 1 - 2, 2026"          -> same month, day range

    A single-day event (no " - " at all) would come through as just
    "<Month> <Day>, <Year>" and yields start_date == end_date — not
    observed on the site yet, but handled defensively.

    If the end month's number is smaller than the start month's (e.g. a
    "December 30 - January 2, 2027" style range, which would mean the
    trailing year belongs to the END date), the start date is pushed back
    to year-1 — this hasn't been observed on the site either, but a
    silently-wrong year for a December event would be an easy miss
    otherwise.
    """
    m = EVENT_DATE_RE.match(text)
    if not m:
        return None, None
    year = int(m["year"])
    start_month, start_day = m["start_month"], m["start_day"]
    end_month = m["end_month"] or start_month
    end_day = m["end_day"] or start_day

    if start_month not in _MONTH_NUM or end_month not in _MONTH_NUM:
        return None, None

    start_year = year
    if _MONTH_NUM[end_month] < _MONTH_NUM[start_month]:
        start_year = year - 1  # range wraps across a calendar year-end

    try:
        start = datetime.strptime(f"{start_month} {start_day} {start_year}", "%B %d %Y")
        end = datetime.strptime(f"{end_month} {end_day} {year}", "%B %d %Y")
    except ValueError:
        return None, None
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _parse_event_card_text(raw_text: str) -> dict | None:
    """
    Split an events-page card's inner_text() into {name, location,
    start_date, end_date}. Observed shape is "<Name>", "<Date range>",
    "<Location>" as the first three non-empty lines (upcoming cards add a
    4th "Let's meet in <City>" CTA line, past ones don't — ignored either
    way). The middle line is what we actually key off: if it doesn't match
    a recognizable date range, this isn't an event card.
    """
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    if len(lines) < 3:
        return None
    name, date_text, location = lines[0], lines[1], lines[2]
    start_date, end_date = _parse_event_date_range(date_text)
    if not start_date:
        return None
    return {"name": name, "location": location, "start_date": start_date, "end_date": end_date}


async def scrape_events(page) -> list[dict]:
    """Returns [{title, category, url, description, published_at,
    start_date, end_date, image, external_url}] for every upcoming AND
    past event on the single /upcoming-events page (the "Past Events"
    section is collapsed behind a toggle by default — expanded here before
    reading). Cards aren't `<a href="/...">` links into our own site like
    the other two sources (each links out to the event's own external
    site instead), so unlike scrape_learning_center/scrape_talks this
    walks every `<a>` on the page and keeps only the ones whose text
    matches the 3-line name/date/location shape — see
    _parse_event_card_text."""
    await page.goto(EVENTS_URL, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(1_500)
    try:
        await _dismiss_cookie_banner(page)
    except PWTimeout:
        pass

    try:
        past_toggle = page.get_by_role("button", name=re.compile("past events", re.I))
        if await past_toggle.count() > 0:
            await past_toggle.first.click()
            await page.wait_for_timeout(800)
    except Exception as e:
        print(f"   couldn't expand the Past Events section: {e}")

    items, seen = [], set()
    cards = await page.query_selector_all("a")
    for a in cards:
        raw_text = await a.inner_text() or ""
        parsed = _parse_event_card_text(raw_text)
        if not parsed:
            continue
        key = (parsed["name"], parsed["start_date"])
        if key in seen:
            continue
        seen.add(key)
        slug = re.sub(r"[^a-z0-9]+", "-", parsed["name"].lower()).strip("-")
        items.append({
            "title": parsed["name"],
            "category": "EVENTS",
            "url": f"event:{slug}:{parsed['start_date']}",
            "description": parsed["location"],
            "published_at": parsed["start_date"],
            "start_date": parsed["start_date"],
            "end_date": parsed["end_date"],
            "image": None,
            "external_url": await a.get_attribute("href"),
        })
    print(f"  upcoming-events: {len(items)} unique event(s) found (upcoming + past)")
    return items


# ───────────────────────────── Detail pages ─────────────────────────────────

async def scrape_detail_meta(page, url: str) -> dict:
    """Fetch one article/talk page and read its og:description,
    article:published_time and og:image meta tags."""
    out = {"description": None, "published_at": None, "image": None}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        for prop, key in (
            ("og:description", "description"),
            ("article:published_time", "published_at"),
            ("og:image", "image"),
        ):
            el = await page.query_selector(f"meta[property='{prop}']")
            if el:
                out[key] = await el.get_attribute("content")
    except PWTimeout:
        print(f"   timeout fetching detail meta: {url}")
    except Exception as e:
        print(f"   error fetching detail meta ({url}): {e}")
    return out


# ────────────────────────────────── Main ────────────────────────────────────

async def main():
    OUT_DIR.mkdir(exist_ok=True)

    existing = []
    if JSON_PATH.exists():
        raw = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        existing = raw.get("articles", raw) if isinstance(raw, dict) else raw
    existing_by_url = {a["url"]: a for a in existing}
    print(f"Loaded {len(existing)} existing item(s) from {JSON_PATH}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36 TrizeDataBot/1.0"
        )
        page = await context.new_page()

        print("Scraping Learning Center (All Resources)...")
        learning_items = await scrape_learning_center(page)

        print("Scraping T-RIZE Talks...")
        talk_items = await scrape_talks(page)

        print("Scraping Upcoming Events (+ Past Events)...")
        event_items = await scrape_events(page)

        live_items = learning_items + talk_items + event_items
        if not live_items:
            print("\n❌ No items found at all — aborting, not touching news.json.")
            print("   One of the three pages' selectors likely needs adjusting — check DevTools.")
            await browser.close()
            sys.exit(1)

        if existing and len(live_items) < len(existing) * MIN_RATIO_OF_EXISTING:
            print(f"\n❌ Only found {len(live_items)} item(s) vs {len(existing)} on record "
                  f"— bigger drop than expected (a page may not have fully loaded, or its "
                  f"layout changed). Aborting without writing news.json.")
            await browser.close()
            sys.exit(1)

        final, new_count = [], 0
        seen_urls = {it["url"] for it in live_items}
        for it in live_items:
            if it["category"] == "EVENTS":
                # Everything already came straight off the listing card —
                # no detail page to fetch, no cached value worth keeping
                # over a fresh one. Always write today's scrape as-is.
                final.append({
                    "title": it["title"],
                    "category": "EVENTS",
                    "description": it["description"],
                    "url": it["url"],
                    "published_at": it["published_at"],
                    "start_date": it["start_date"],
                    "end_date": it["end_date"],
                    "image": None,
                    "external_url": it.get("external_url"),
                })
                if it["url"] not in existing_by_url:
                    new_count += 1
                continue

            prior = existing_by_url.get(it["url"])
            if prior:
                final.append({
                    "title": it["title"],
                    "category": it["category"],
                    "description": prior.get("description"),
                    "url": it["url"],
                    "published_at": it.get("published_at") or prior.get("published_at"),
                    "image": prior.get("image"),
                })
            else:
                meta = await scrape_detail_meta(page, it["url"])
                description = meta.get("description")
                if it["category"] == "TALKS" and it.get("role") and description:
                    description = f"{it['role']} — {description}"
                final.append({
                    "title": it["title"],
                    "category": it["category"],
                    "description": description,
                    "url": it["url"],
                    "published_at": it.get("published_at") or meta.get("published_at"),
                    "image": meta.get("image"),
                })
                new_count += 1
                await asyncio.sleep(DETAIL_DELAY)

        await browser.close()

    removed = [a for a in existing if a["url"] not in seen_urls]
    final.sort(key=lambda a: a.get("published_at") or "", reverse=True)

    JSON_PATH.write_text(
        json.dumps({
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(final),
            "articles": final,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\n✅ {len(final)} item(s) saved → {JSON_PATH}")
    print(f"   {new_count} new · {len(removed)} removed")
    if removed:
        for a in removed:
            print(f"   - removed: {a.get('title')}")


if __name__ == "__main__":
    asyncio.run(main())
