#!/usr/bin/env python3
"""
scrape_trizedata.py
=====================
Scrapes BOTH T-RIZE sources in one run → trizedata/news.json:

  1. Learning Center ("All Resources" filter)
     https://www.t-rize.io/learn/learning-center?filter=all
     News / Article / Use Case items published by T-RIZE.

  2. T-RIZE Talks
     https://www.t-rize.io/t-rize-talks
     Short team-spotlight interviews — one per week-ish. Folded into the
     same file/array as a "TALKS" category rather than a separate JSON:
     the team is small and talks are a light, occasional feature, not
     worth a whole parallel pipeline (same spirit as cantonnews.org's
     news.json, just two sources merged into one output instead of two).

Why one script / one workflow for two pages: both live on t-rize.io, both
need a real rendered browser (t-rize.io 403s any plain HTTP fetch — same
as cantonnews.org), and scraping them back-to-back in one Playwright
session is simpler to operate (one script, one cron-job.org entry) than
maintaining two near-identical scrapers for a small site like this.

Output shape — one flat `articles` array, same {title, category,
description, url, published_at, image} shape for every item regardless
of source:

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

Diffing (identical philosophy to scrape_cantonnews.py), keyed by URL:
  - new URL     -> fetch its detail page once for description/image (and
                   published_at too, for Learning Center items only;
                   Talks already got it for free from the listing card).
  - known URL   -> title/category (and published_at for Talks) refreshed
                   from the current listing pass; description/image/
                   published_at-for-news kept as previously captured, no
                   re-fetch.
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

        live_items = learning_items + talk_items
        if not live_items:
            print("\n❌ No items found at all — aborting, not touching news.json.")
            print("   One or both page selectors likely need adjusting — check DevTools.")
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
