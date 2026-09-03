"""
scrape_cantonnews.py
=====================
Scrape cantonnews.org/news → canton-ecosystem/news.json
Scraped with cantonnews.org's written permission.

Every run:
  1. Walks all listing pages (https://cantonnews.org/news?page=N) to get the
     full CURRENT set of {title, url} on the site.
  2. Diffs against the existing news.json (keyed by article URL, which is
     stable even if a title gets edited):
       - new URL              → fetch its article page once for the precise
                                 `article:published_time` meta tag, add it.
       - known URL, new title → title updated in place, published_at kept.
       - known URL, missing   → dropped entirely (matches what cantonnews.org
                                 itself shows — no "ghost" entries).
  3. Writes canton-ecosystem/news.json, sorted newest-first.

Safety valve: if a full re-scrape suddenly finds far fewer articles than are
already on record, that almost certainly means the site didn't load fully or
its layout changed — NOT that 100+ articles got deleted overnight. In that
case the script aborts without writing anything, rather than pushing a
corrupted news.json straight to main (this scraper auto-promotes dev -> main
with no human review step, unlike scrape_canton.py).

Usage:
    python scrape_cantonnews.py

Output:
    canton-ecosystem/news.json
"""
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

BASE_URL  = "https://cantonnews.org"
LIST_URL  = BASE_URL + "/news"
OUT_DIR   = Path("canton-ecosystem")
JSON_PATH = OUT_DIR / "news.json"

# Be polite between requests
LIST_DELAY   = 0.8
DETAIL_DELAY = 0.8

# If a fresh scrape finds fewer than this fraction of the previously-known
# article count, treat it as a broken/partial scrape and abort instead of
# writing (see safety-valve note above).
MIN_RATIO_OF_EXISTING = 0.5

# Link hrefs to ignore when parsing a listing page card — pagination links,
# social/share links, anchors, etc. (not real article links).
SKIP_HREF_SNIPPETS = (
    "page=", "mailto:", "#", "twitter.com", "x.com/", "t.me/",
    "discord", "linkedin.com", "facebook.com",
)


def normalize_article_url(href: str) -> str:
    """Turn a possibly-relative href into an absolute, query/fragment-free URL."""
    if href.startswith("http"):
        url = href
    else:
        url = BASE_URL + "/" + href.lstrip("/")
    return url.split("?")[0].split("#")[0].rstrip("/")


async def get_total_pages(page) -> int:
    """Read the highest ?page=N link from the pagination controls on page 1."""
    hrefs = await page.eval_on_selector_all(
        "a[href*='page=']", "els => els.map(e => e.getAttribute('href'))"
    )
    max_page = 1
    for href in hrefs or []:
        m = re.search(r"[?&]page=(\d+)", href or "")
        if m:
            max_page = max(max_page, int(m.group(1)))
    return max_page


async def scrape_listing_page(page, n: int) -> list[dict]:
    """
    Returns [{title, url}] for listing page n, newest-first as shown on site.

    NOTE: selector is a broad heuristic (any link with substantial text,
    minus known non-article patterns). If this ever returns 0 results or a
    pile of nav junk, open cantonnews.org/news in DevTools and swap in the
    real article-card selector — same caveat scrape_canton.py documents for
    cantonecosystem.com's markup.
    """
    url = LIST_URL if n == 1 else f"{LIST_URL}?page={n}"
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(1_500)

    links = await page.query_selector_all("a[href]")
    items, seen = [], set()
    for a in links:
        href = await a.get_attribute("href") or ""
        href_l = href.lower()
        if not href or any(s in href_l for s in SKIP_HREF_SNIPPETS):
            continue
        article_url = normalize_article_url(href)
        if "cantonnews.org" not in article_url or article_url == LIST_URL.rstrip("/"):
            continue
        title = (await a.inner_text() or "").strip()
        if len(title) < 12:  # filters out nav labels, icons, "Read more" stubs
            continue
        if article_url in seen:
            continue
        seen.add(article_url)
        items.append({"title": title, "url": article_url})
    return items


async def scrape_published_at(page, url: str) -> str | None:
    """Fetch one article page and read its article:published_time meta tag."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        el = await page.query_selector("meta[property='article:published_time']")
        if el:
            return await el.get_attribute("content")
    except PWTimeout:
        print(f"   timeout fetching published_time: {url}")
    except Exception as e:
        print(f"   error fetching published_time ({url}): {e}")
    return None


async def main():
    OUT_DIR.mkdir(exist_ok=True)

    existing = []
    if JSON_PATH.exists():
        raw = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        existing = raw.get("articles", raw) if isinstance(raw, dict) else raw
    existing_by_url = {a["url"]: a for a in existing}
    print(f"Loaded {len(existing)} existing articles from {JSON_PATH}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36 RizebyNewsBot/1.0"
        )
        page = await context.new_page()

        await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=30_000)
        total_pages = await get_total_pages(page)
        print(f"Found {total_pages} listing page(s)")

        live_items = []
        for n in range(1, total_pages + 1):
            page_items = await scrape_listing_page(page, n)
            print(f"  page {n}: {len(page_items)} articles")
            live_items.extend(page_items)
            await asyncio.sleep(LIST_DELAY)

        # De-dup by URL, keep first occurrence (site order = newest first)
        live_unique, seen_urls = [], set()
        for it in live_items:
            if it["url"] not in seen_urls:
                seen_urls.add(it["url"])
                live_unique.append(it)

        if not live_unique:
            print("\n❌ No articles found at all — aborting, not touching news.json.")
            print("   The listing page selector likely needs adjusting — check DevTools.")
            await browser.close()
            sys.exit(1)

        if existing and len(live_unique) < len(existing) * MIN_RATIO_OF_EXISTING:
            print(f"\n❌ Only found {len(live_unique)} articles vs {len(existing)} on record "
                  f"— bigger drop than expected (site may not have fully loaded, or its "
                  f"layout changed). Aborting without writing news.json.")
            await browser.close()
            sys.exit(1)

        final, new_count = [], 0
        for it in live_unique:
            prior = existing_by_url.get(it["url"])
            if prior:
                # Title may have been edited on the site — refresh it, but keep
                # the published_at we already captured (no need to re-fetch).
                final.append({
                    "title": it["title"],
                    "url": it["url"],
                    "published_at": prior.get("published_at"),
                })
            else:
                published_at = await scrape_published_at(page, it["url"])
                final.append({
                    "title": it["title"],
                    "url": it["url"],
                    "published_at": published_at,
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

    print(f"\n✅ {len(final)} articles saved → {JSON_PATH}")
    print(f"   {new_count} new · {len(removed)} removed")
    if removed:
        for a in removed:
            print(f"   - removed: {a.get('title')}")


if __name__ == "__main__":
    asyncio.run(main())
