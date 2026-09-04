"""
scrape_arc.py
================
Scrape arc.io/ecosystem → arcentities.json + logos/

Usage:
    python scrape_arc.py               # full run
    python scrape_arc.py --diff        # only new entities vs existing JSON

Output:
    arc-ecosystem/arcentities.json
    arc-ecosystem/logos/<slug>.<ext>

Design notes vs scrape_canton.py
---------------------------------
Arc's ecosystem page (Webflow + Finsweet CMS Load / List Filter) already
carries every entity's full data in the DOM on page load — name, logo,
description, category AND subcategory — hidden behind class "u-hide".
Finsweet auto-loads all "pages" into the DOM by itself (the "Load more"
button hides itself once everything is loaded); we just wait for that to
finish. There is therefore no separate detail-page pass here: Arc's own
/ecosystem/<slug> pages are empty stub templates with no real content
(verified manually before writing this script), unlike Canton's detail
pages which carry the long-form description. One page load = one pass.

Category taxonomy (confirmed against the live site, not guessed):
  6 parent groups, each exposed as a Finsweet "fs-list-field" on every
  entity card: issuers, infrastructures, developer, tradings, payments,
  financial. Each entity is tagged with BOTH the parent label and its
  subcategory value(s), flattened into a single `tags` list — matching
  the flat, non-hierarchical tag design used for Canton.

A handful of entities currently carry no category at all on Arc's own
site (a real gap in their data, not a scraping issue) — they just end up
with tags: [] and show up under "All" only, same as Canton would handle
an untagged entity.
"""

import asyncio
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL   = "https://www.arc.io/ecosystem"
OUT_DIR    = Path("arc-ecosystem")
LOGOS_DIR  = OUT_DIR / "logos"
JSON_PATH  = OUT_DIR / "arcentities.json"
DIFF_MODE  = "--diff" in sys.argv

# How long to let Finsweet CMS Load auto-paginate every entity into the DOM (ms)
LIST_WAIT_MS = 8_000

# Delay between logo downloads (seconds) — be polite
DOWNLOAD_DELAY = 0.4

# Parent-category label for each Finsweet "nest" field found on arc.io/ecosystem
NEST_PARENT = {
    "issuers":         "Issuers",
    "infrastructures": "Infrastructure",
    "developer":       "Developer Tools",
    "tradings":        "Trading",
    "payments":        "Payments",
    "financial":       "Financial Services",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    """'BNP Paribas' → 'bnp-paribas' — fallback only, real Arc slugs (from
    the item's own /ecosystem/<slug> link) are preferred when available."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return s.strip("-")


def download_logo(url: str, slug: str) -> str | None:
    """Download logo, return local relative path or None on failure."""
    if not url or "placeholder" in url:
        return None
    ext = url.split("?")[0].split(".")[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "svg", "webp", "avif"):
        ext = "png"
    dest = LOGOS_DIR / f"{slug}.{ext}"
    if dest.exists():
        return str(dest.relative_to(OUT_DIR)).replace("/", "\\")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            dest.write_bytes(r.read())
        print(f"    ↓ logo saved: {dest.name}")
        return str(dest.relative_to(OUT_DIR)).replace("/", "\\")
    except Exception as e:
        print(f"    ⚠ logo failed ({slug}): {e}")
        return None


# JS run inside the page — mirrors the manual DOM inspection done against
# the live site (arc.io/ecosystem) before writing this scraper.
EXTRACT_JS = """
(NEST_PARENT) => {
  const items = [...document.querySelectorAll('.ecosystem-item')];
  return items.map(item => {
    const name = item.getAttribute('data-check') || '';

    const logo = item.querySelector('.ecosystem-item_logo');
    const logoUrl = logo ? logo.getAttribute('src') : '';

    const descEl = item.querySelector('.ecosystem-item_content p.u-hide');
    const description = descEl ? descEl.textContent.trim() : '';

    const detailLink = item.querySelector('a[fs-list-element="item-link"]');
    const detailHref = detailLink ? detailLink.getAttribute('href') : '';

    const extLink = item.querySelector('a.u-link-cover');
    const externalUrl = extLink ? extLink.getAttribute('href') : '';

    const tags = [];
    Object.keys(NEST_PARENT).forEach(field => {
      const els = item.querySelectorAll('[fs-list-field="' + field + '"]');
      if (els.length) {
        const parent = NEST_PARENT[field];
        if (!tags.includes(parent)) tags.push(parent);
        els.forEach(el => {
          const t = el.textContent.trim();
          if (t && !tags.includes(t)) tags.push(t);
        });
      }
    });

    return {
      name,
      logo_cdn: logoUrl,
      description,
      detail_href: detailHref,
      external_url: externalUrl,
      tags,
    };
  });
}
"""


# ── Step 1 : scrape the listing page (one pass — everything lives here) ───────

async def scrape_listing(page) -> list[dict]:
    print("\n📄 Loading arc.io/ecosystem…")
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
    # Let Finsweet CMS Load auto-paginate every entity into the DOM.
    # The "Load more" button hides itself once nothing is left to load —
    # no click/URL pagination needed, we just have to wait it out.
    await page.wait_for_timeout(LIST_WAIT_MS)

    raw = await page.evaluate(EXTRACT_JS, NEST_PARENT)
    print(f"   Found {len(raw)} raw items")

    entities = []
    seen = set()
    for item in raw:
        name = (item.get("name") or "").strip()
        if not name:
            continue

        detail_href = (item.get("detail_href") or "").strip()
        if detail_href.startswith("/ecosystem/"):
            slug = detail_href.split("/ecosystem/", 1)[1].strip("/")
        else:
            slug = slugify(name)
        if not slug or slug in seen:
            continue
        seen.add(slug)

        entities.append({
            "name":         name,
            "slug":         slug,
            "logo_cdn":     item.get("logo_cdn") or "",
            "description":  item.get("description") or "",
            "external_url": item.get("external_url") or "",
            "tags":         item.get("tags") or [],
        })

    print(f"   ✓ {len(entities)} unique entities parsed")
    return entities


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    OUT_DIR.mkdir(exist_ok=True)
    LOGOS_DIR.mkdir(exist_ok=True)

    # Load existing data if diff mode
    existing = {}
    if DIFF_MODE and JSON_PATH.exists():
        data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        existing = {e["slug"]: e for e in data}
        print(f"🔄 Diff mode — {len(existing)} existing entities loaded")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # ── 1. Get listing (name + logo + description + tags, all in one pass) ──
        entities = await scrape_listing(page)

        if not entities:
            print("\n❌ No entities found — the page structure may have changed.")
            print("   Open arc.io/ecosystem in DevTools and check the .ecosystem-item selector.")
            await browser.close()
            return

        # Filter to new only if diff mode
        if DIFF_MODE:
            new_entities = [e for e in entities if e["slug"] not in existing]
            print(f"   → {len(new_entities)} new entities to process")
            entities_to_process = new_entities
        else:
            entities_to_process = entities

        # ── 2. Download logos for new/changed entities only ─────────────────────
        total = len(entities_to_process)
        for i, entity in enumerate(entities_to_process, 1):
            print(f"[{i}/{total}] {entity['name']} ({entity['slug']})")
            entity["logo_local"] = download_logo(entity["logo_cdn"], entity["slug"])
            time.sleep(DOWNLOAD_DELAY)

        await browser.close()

    # ── 3. Merge + save JSON ──────────────────────────────────────────────────
    if DIFF_MODE and existing:
        merged = {**existing}
        for e in entities_to_process:
            merged[e["slug"]] = e
        final = list(merged.values())
    else:
        final = entities

    final.sort(key=lambda x: x["name"].lower())

    JSON_PATH.write_text(
        json.dumps(final, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"\n✅ Done — {len(final)} entities saved to {JSON_PATH}")
    print(f"   Logos in: {LOGOS_DIR}")

    # ── 4. Detect missing entities (in existing JSON but not on site anymore) ──
    if DIFF_MODE and existing:
        scraped_slugs = {e["slug"] for e in entities}
        missing = [e for slug, e in existing.items() if slug not in scraped_slugs]
        HANDLE_DIR = Path("HANDLE-THIS-BEFORE-PULL")
        HANDLE_DIR.mkdir(exist_ok=True)
        MISSING_PATH = HANDLE_DIR / "arc_missing_entities.txt"

        if missing:
            lines = [
                "Arc Ecosystem — Entities not found on arc.io/ecosystem",
                f"Checked: {__import__('datetime').date.today()}",
                f"Count: {len(missing)}",
                "",
                "These entities exist in arcentities.json but were NOT found during scrape.",
                "Please check manually — remove from arcentities.json if confirmed gone.",
                "",
            ]
            for e in sorted(missing, key=lambda x: x["name"].lower()):
                lines.append(f"- {e['name']} (slug: {e['slug']})")

            MISSING_PATH.write_text("\n".join(lines), encoding="utf-8")

            print(f"\n⚠️  {len(missing)} entities not found on site — see {MISSING_PATH.name}:")
            for e in missing:
                print(f"   - {e['name']} ({e['slug']})")
        else:
            # Clean up file if no missing entities
            if MISSING_PATH.exists():
                MISSING_PATH.unlink()
            print("\n✓ No missing entities — all existing entries found on site")


if __name__ == "__main__":
    asyncio.run(main())
