#!/usr/bin/env python3
"""
generate_trizenews_articles.py — builds news/trizearticles.json (the
"T-RIZE News" collection catalog) from trizedata/news.json.

trizedata/news.json is scraped by scrape_trizedata.py (Learning Center +
T-RIZE Talks + Upcoming/Past Events, see that script) — this script never
touches that file, it only reads it.

Granularity — deliberately DIFFERENT from Canton News (one article per
week): T-RIZE News bundles one article per CALENDAR MONTH from
MONTHLY_FROM_YEAR onward, and one article per CALENDAR YEAR for every
year before that.
  MONTHLY_FROM_YEAR = 2026 is a fixed constant, NOT derived from "today"
  or recomputed on each run. This is a one-time editorial choice, not a
  sliding window: T-RIZE's publishing volume in 2023-2025 was too sparse
  to justify a monthly article (some months had zero items — see the
  "skip" rule below), so those three years each get a single "T-RIZE
  NEWS — <year>" article instead. From 2026 onward there's consistently
  enough volume for monthly articles, so every month gets its own, and
  — crucially — this keeps being true indefinitely into the future: 2027,
  2028, etc. all still bucket by month, NOT by year, however long ago
  they eventually are. A period, once published, never changes shape
  later (an already-complete month never gets folded into a yearly
  rollup down the road) — that stability is the whole point of this
  being a fixed constant instead of a "< current_year" comparison.
  If 2026 (or any later year) ever turns out to need the same sparse-data
  treatment as 2023-2025, that's a deliberate, one-line change to this
  constant — never something the script decides on its own.
  A period (month or year) with nothing published in it AND no event
  overlapping it is skipped entirely — no empty article, same as Canton
  (this is why March jumps straight to June 2026: April/May had nothing).

Per-article content, deliberately identical in shape to Canton News:
  - intro/excerpt: a one-line "T-RIZE news and updates for <period>."
    sentence.
  - one <h2>CATEGORY</h2> section per category touched that period, in
    first-appearance order (oldest item first) — NEWS, ARTICLE, USE CASE
    and TALKS all render the same way Canton renders its categories:
      <p><strong>Title:</strong> description <a href="url" target="_blank"
      rel="noopener noreferrer">Read more</a>.</p>
  - EVENTS is always the LAST section (if the period has any), and reads
    differently on purpose: these cards have no T-RIZE article behind
    them (see scrape_trizedata.py), so there's no Read-more link — each
    line is instead
      <p><strong>Name</strong> — Sep 28–Oct 1, 2026, Miami, FL.</p>
    An event whose [start_date, end_date] span overlaps a period is
    included in EVERY period it touches, not just the one it starts in —
    a straddling event like SIBOS Miami (Sep 28 – Oct 1) appears in both
    September's and October's article once both exist.

Images, matching the convention already chosen for the asset folder:
  monthly -> /assets/news/t-rize/<2-digit year>m<month number>.jpg
             (e.g. 26m9.jpg for September 2026 — NOT zero-padded, same
             style as Canton's 26w16.jpg)
  yearly  -> /assets/news/t-rize/y<2-digit year>.jpg (e.g. y25.jpg)

This is a full rebuild every run, not an incremental append — same
rationale as generate_cantonnews_articles.py: the first run bootstraps
every missing period in one go, and every later monthly run just adds
whatever period(s) newly became complete since last time. No safety valve
in THIS script, also matching generate_cantonnews_articles.py: the
upstream scraper (scrape_trizedata.py) is what validates trizedata/news.json
is sane before this script ever sees it.

Run from the repo root:
    python scripts/generate_trizenews_articles.py

Then run scripts/generate_news.py to bake the catalog into static HTML
pages under news/t-rize/ — already wired up, trizearticles.json is one of
generate_news.py's COLLECTIONS.
"""
import html
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT          = Path(__file__).resolve().parent.parent
SOURCE_PATH   = ROOT / "trizedata" / "news.json"
CATALOG_PATH  = ROOT / "news" / "trizearticles.json"

MONTH_NAMES = [datetime(2000, m, 1).strftime("%B") for m in range(1, 13)]

# Fixed, one-time boundary — see "Granularity" in the module docstring.
# Years >= this get one article per month; years before it get one article
# for the whole year. Never compared against "today"; never moves.
MONTHLY_FROM_YEAR = 2026


def load_source_items() -> list[dict]:
    if not SOURCE_PATH.exists():
        print(f"❌ {SOURCE_PATH} not found.")
        return []
    raw = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    return raw.get("articles", raw) if isinstance(raw, dict) else raw


def parse_date(s: str | None):
    """Accepts either a bare 'YYYY-MM-DD' (Talks/Events) or a full ISO
    datetime, possibly with a timezone offset (Learning Center's
    article:published_time, e.g. '2026-07-21T10:00+01:00'). Returns a
    date, or None if unparseable/missing."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def period_key(d) -> tuple[int, int | None]:
    """(year, month) for a monthly period (year >= MONTHLY_FROM_YEAR), or
    (year, None) for a whole-year period (year < MONTHLY_FROM_YEAR) — see
    MONTHLY_FROM_YEAR above. Fixed per-year, never based on "today"."""
    return (d.year, d.month) if d.year >= MONTHLY_FROM_YEAR else (d.year, None)


def period_end_exclusive(year: int, month: int | None):
    """The first date NOT covered by this period — first day of next month
    for a monthly period, Jan 1 of next year for a yearly one. A period is
    safe to publish once real "today" has reached this date."""
    from datetime import date
    if month is None:
        return date(year + 1, 1, 1)
    ny, nm = next_month(year, month)
    return date(ny, nm, 1)


def period_is_complete(year: int, month: int | None, today) -> bool:
    return period_end_exclusive(year, month) <= today


def period_title_slug_image_excerpt(year: int, month: int | None):
    if month is None:
        title   = f"T-RIZE NEWS — {year}"
        slug    = f"t-rize-news-{year}"
        image   = f"/assets/news/t-rize/y{str(year)[2:]}.jpg"
        excerpt = f"T-RIZE news and updates for {year}."
    else:
        month_name = MONTH_NAMES[month - 1]
        title   = f"T-RIZE NEWS — {month_name.upper()} {year}"
        slug    = f"t-rize-news-{month_name.lower()}-{year}"
        image   = f"/assets/news/t-rize/{str(year)[2:]}m{month}.jpg"
        excerpt = f"T-RIZE news and updates for {month_name} {year}."
    return title, slug, image, excerpt


def publish_date_for(year: int, month: int | None) -> datetime:
    """The date this period's article 'publishes' on — the first day right
    after the period ends (mirrors Canton's "Monday right after the week
    ends" convention)."""
    if month is None:
        return datetime(year + 1, 1, 1)
    ny, nm = next_month(year, month)
    return datetime(ny, nm, 1)


def build_article_line(item: dict) -> str:
    # quote=False: lands in text content (<strong>/<p>), not an HTML
    # attribute — a literal apostrophe is valid and reads better than an
    # &#x27; entity (same call made in generate_cantonnews_articles.py).
    title = html.escape(item.get("title") or "—", quote=False)
    desc  = html.escape(item.get("description") or "", quote=False)
    url   = html.escape(item.get("url") or "", quote=True)
    desc_part = f" {desc}" if desc else ""
    return (f'<p><strong>{title}:</strong>{desc_part} '
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">Read more</a>.</p>')


def format_event_date_range(start_s: str | None, end_s: str | None) -> str:
    start = parse_date(start_s)
    end = parse_date(end_s) or start
    if not start:
        return "date TBC"
    if end == start:
        return f"{start.strftime('%B')} {start.day}, {start.year}"
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%B')} {start.day}–{end.day}, {end.year}"
    if start.year == end.year:
        return f"{start.strftime('%B')} {start.day} – {end.strftime('%B')} {end.day}, {end.year}"
    return f"{start.strftime('%B')} {start.day}, {start.year} – {end.strftime('%B')} {end.day}, {end.year}"


def build_event_line(item: dict) -> str:
    name = html.escape(item.get("title") or "—", quote=False)
    location = html.escape(item.get("description") or "", quote=False)
    date_range = format_event_date_range(item.get("start_date"), item.get("end_date"))
    loc_part = f", {location}" if location else ""
    return f"<p><strong>{name}</strong> — {date_range}{loc_part}.</p>"


def build_content(bucket: dict, year: int, month: int | None) -> tuple[str, list[str]]:
    """Returns (content_html, categories_in_order). EVENTS, if present, is
    always appended last regardless of when it first appears chronologically
    — a deliberate "here's what we attended" closing section, distinct from
    the dated News/Article/Use Case/Talks categories above it."""
    _, _, _, excerpt = period_title_slug_image_excerpt(year, month)
    items_sorted = sorted(bucket["items"], key=lambda t: t[0])  # oldest first

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for _, it in items_sorted:
        cat = it.get("category") or "OTHER"
        if cat not in groups:
            groups[cat] = []
            order.append(cat)
        groups[cat].append(it)

    parts = [f"<p>{excerpt}</p>"]
    for cat in order:
        parts.append(f"<h2>{html.escape(cat)}</h2>")
        for it in groups[cat]:
            parts.append(build_article_line(it))

    events = bucket["events"]
    if events:
        seen, uniq_events = set(), []
        for ev in sorted(events, key=lambda e: e.get("start_date") or ""):
            if ev["url"] in seen:
                continue
            seen.add(ev["url"])
            uniq_events.append(ev)
        parts.append("<h2>EVENTS</h2>")
        for ev in uniq_events:
            parts.append(build_event_line(ev))
        order.append("EVENTS")

    return "\n\n".join(parts), order


def main():
    items = load_source_items()
    if not items:
        print("No source items found — nothing to do.")
        return

    today = datetime.now(timezone.utc).date()

    buckets: dict[tuple[int, int | None], dict] = {}

    def bucket_for(key):
        return buckets.setdefault(key, {"items": [], "events": []})

    for it in items:
        if it.get("category") == "EVENTS":
            continue
        d = parse_date(it.get("published_at"))
        if d is None:
            continue
        bucket_for(period_key(d))["items"].append((d, it))

    for it in items:
        if it.get("category") != "EVENTS":
            continue
        start = parse_date(it.get("start_date"))
        end = parse_date(it.get("end_date")) or start
        if not start:
            continue
        cur_y, cur_m = start.year, start.month
        end_y, end_m = end.year, end.month
        while (cur_y, cur_m) <= (end_y, end_m):
            d = datetime(cur_y, cur_m, 1).date()
            bucket_for(period_key(d))["events"].append(it)
            cur_y, cur_m = next_month(cur_y, cur_m)

    catalog = []
    periods_built = 0
    for (year, month) in sorted(buckets.keys(), key=lambda k: (k[0], k[1] or 0)):
        bucket = buckets[(year, month)]
        label = f"{year}" if month is None else f"{MONTH_NAMES[month-1]} {year}"

        if not bucket["items"] and not bucket["events"]:
            print(f"  (skip) {label}: nothing published, no events")
            continue
        if not period_is_complete(year, month, today):
            print(f"  (skip, not complete yet) {label}")
            continue

        title, slug, image, excerpt = period_title_slug_image_excerpt(year, month)
        content, cats = build_content(bucket, year, month)
        publish_date = publish_date_for(year, month)

        catalog.append({
            "slug": slug,
            "title": title,
            "excerpt": excerpt,
            "date": publish_date.strftime("%B %d, %Y"),
            "image": image,
            "content": content,
        })
        periods_built += 1
        n_items = len(bucket["items"])
        n_events = len(set(e["url"] for e in bucket["events"]))
        print(f"  ✓ {label}: {n_items} item(s), {n_events} event(s), "
              f"categories: {', '.join(cats)}")

    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{CATALOG_PATH.relative_to(ROOT)} written: {periods_built} period(s).")


if __name__ == "__main__":
    main()
