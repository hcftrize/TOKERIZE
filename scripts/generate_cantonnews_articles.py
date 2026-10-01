#!/usr/bin/env python3
"""
generate_cantonnews_articles.py — builds news/cantonarticles.json (the
"Canton News" collection catalog) from canton-ecosystem/news.json.

canton-ecosystem/news.json is scraped daily by scrape_cantonnews.py to feed
RizeBy's Telegram digest (see post_cantonnews_digest.py) — this script never
touches that file, it only reads it. Weeks are bucketed exactly the same way
post_cantonnews_digest.py already does (Monday 00:00:00 -> Sunday 23:59:59,
UTC), so the article content matches what the Telegram digest already shows
for the same week.

For every COMPLETE week (Sunday end already in the past) found in the data,
this produces one catalog entry:
  - slug:  canton-news-week-<iso_week>-<iso_year>
  - title: CANTON NEWS — WEEK <iso_week>, <iso_year>   (iso_week is the real
           ISO calendar week number, matching the pre-made cover filenames
           like 26w16.jpg for week 16 of 2026)
  - date:  the Monday the article "publishes" on — i.e. the Monday right
           after the covered week ends (that's when the cron actually runs)
  - image: /assets/news/canton/<2-digit year>w<week>.jpg
  - content: one intro paragraph (date range + categories touched that
           week), then one <h2>CATEGORY</h2> section per category in the
           same order post_cantonnews_digest.py uses (first appearance,
           oldest article first), each article rendered as
           <p><strong>Title:</strong> description <a href="url">Read more</a>.</p>

This is a full rebuild every run, not an incremental append: every complete
week from the first one found through the most recent one is recomputed from
canton-ecosystem/news.json and the whole news/cantonarticles.json file is
rewritten. That's what makes the very first run a one-shot "bootstrap" of
every missing week (currently ~24 of them), and every later Monday run just
naturally adds the one new week that became complete since last time — no
separate bootstrap mode, no incremental state file to keep in sync.
An in-progress (not yet complete) week is never included, so the article
for a given week only ever appears once that week is fully over.

Run from the repo root:
    python scripts/generate_cantonnews_articles.py

Then run scripts/generate_news.py to bake the catalog into static HTML
pages under news/canton/.
"""
import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEWS_SOURCE_PATH = ROOT / "canton-ecosystem" / "news.json"
CATALOG_PATH = ROOT / "news" / "cantonarticles.json"


def week_bounds(monday: datetime) -> tuple[datetime, datetime]:
    """Monday 00:00:00 -> Sunday 23:59:59, both tz-aware UTC. `monday` must
    already be truncated to midnight. Mirrors post_cantonnews_digest.py's
    week_bounds() exactly, so the two systems always agree on which
    articles belong to which week."""
    sunday_end = monday + timedelta(days=7) - timedelta(seconds=1)
    return monday, sunday_end


def monday_of(dt: datetime) -> datetime:
    m = dt - timedelta(days=dt.weekday())
    return m.replace(hour=0, minute=0, second=0, microsecond=0)


def load_source_articles() -> list[dict]:
    if not NEWS_SOURCE_PATH.exists():
        print(f"❌ {NEWS_SOURCE_PATH} not found.")
        return []
    raw = json.loads(NEWS_SOURCE_PATH.read_text(encoding="utf-8"))
    return raw.get("articles", raw) if isinstance(raw, dict) else raw


def parse_published_at(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def articles_in_range(articles: list[dict], start: datetime, end: datetime) -> list[dict]:
    """All articles with published_at inside [start, end] (UTC), oldest
    first — same convention as post_cantonnews_digest.py."""
    result = []
    for a in articles:
        dt = parse_published_at(a.get("published_at"))
        if dt is None:
            continue
        if start <= dt <= end:
            result.append(a)
    result.sort(key=lambda a: a.get("published_at") or "")
    return result


def build_article_line(a: dict) -> str:
    # quote=False: these land in text content (<strong>/<p>), not an HTML
    # attribute, so a literal apostrophe is valid and reads far better than
    # an &#x27; entity cluttering up scraped article text.
    title = html.escape(a.get("title") or "—", quote=False)
    desc = html.escape(a.get("description") or "", quote=False)
    url = html.escape(a.get("url") or "", quote=True)
    desc_part = f" {desc}" if desc else ""
    # Opens in a new tab so a reader can click through every item of interest
    # without losing their place in the weekly recap.
    return (f'<p><strong>{title}:</strong>{desc_part} '
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">Read more</a>.</p>')


def build_content(week_articles: list[dict], monday: datetime, sunday: datetime) -> tuple[str, list[str]]:
    """Returns (content_html, categories_in_order)."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for a in week_articles:
        cat = (a.get("category") or "OTHER").upper()
        if cat not in groups:
            groups[cat] = []
            order.append(cat)
        groups[cat].append(a)

    date_range = f"{monday.strftime('%B %d')}&ndash;{sunday.strftime('%d, %Y')}" if monday.month == sunday.month \
        else f"{monday.strftime('%B %d')} &ndash; {sunday.strftime('%B %d, %Y')}"

    parts = [
        f"<p>Canton Network news for the week of {date_range}.</p>"
    ]
    for cat in order:
        parts.append(f"<h2>{html.escape(cat)}</h2>")
        for a in groups[cat]:
            parts.append(build_article_line(a))

    return "\n\n".join(parts), order


def main():
    source_articles = load_source_articles()
    if not source_articles:
        print("No source articles found — nothing to do.")
        return

    dated = [(parse_published_at(a.get("published_at")), a) for a in source_articles]
    dated = [(dt, a) for dt, a in dated if dt is not None]
    if not dated:
        print("No source articles with a parseable published_at — nothing to do.")
        return

    earliest_monday = monday_of(min(dt for dt, _ in dated))
    now_utc = datetime.now(timezone.utc)
    current_week_monday = monday_of(now_utc)

    catalog = []
    week_monday = earliest_monday
    weeks_built = 0
    while week_monday < current_week_monday:  # strictly before this week -> complete
        week_sunday = week_monday + timedelta(days=7) - timedelta(seconds=1)
        week_articles = articles_in_range(source_articles, week_monday, week_sunday)

        if week_articles:
            iso_year, iso_week, _ = week_monday.isocalendar()
            slug = f"canton-news-week-{iso_week}-{iso_year}"
            title = f"CANTON NEWS — WEEK {iso_week}, {iso_year}"
            publish_date = week_monday + timedelta(days=7)  # the following Monday
            content, cats = build_content(week_articles, week_monday, week_sunday)
            excerpt = (f"Canton Network news for the week of "
                       f"{week_monday.strftime('%B %d')}–{week_sunday.strftime('%d, %Y')}.")
            year_suffix = str(iso_year)[2:]
            image = f"/assets/news/canton/{year_suffix}w{iso_week}.jpg"

            catalog.append({
                "slug": slug,
                "title": title,
                "excerpt": excerpt,
                "date": publish_date.strftime("%B %d, %Y"),
                "image": image,
                "content": content,
            })
            weeks_built += 1
            print(f"  ✓ week {iso_week} {iso_year} ({week_monday.date()}–{week_sunday.date()}): "
                  f"{len(week_articles)} article(s), categories: {', '.join(cats)}")
        else:
            print(f"  (skip) week of {week_monday.date()}–{week_sunday.date()}: no articles")

        week_monday += timedelta(days=7)

    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{CATALOG_PATH.relative_to(ROOT)} written: {weeks_built} week(s) "
          f"(from week {catalog[0]['slug'].split('-')[-2] if catalog else '-'} "
          f"onward, current week in progress excluded).")


if __name__ == "__main__":
    main()
