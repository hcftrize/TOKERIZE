#!/usr/bin/env python3
"""
generate_news.py — Static pre-renderer for the Tokerize /news section.

Shared rendering engine for ALL news sources. Each source is a "collection":
a catalog JSON file (list of {slug, title, excerpt, date, image, content} —
content is trusted, author-supplied HTML) and an output subfolder. For every
entry in every collection, this bakes a full standalone HTML page at
news/<folder>/<slug>.html using templates/article-template.html.

Collections today:
  - news/tokerizearticles.json  -> news/tokerize/   (hand-written editorial,
                                                       e.g. the monthly recap)
  - news/cantonarticles.json    -> news/canton/      (auto-built weekly from
                                                       canton-ecosystem/news.json
                                                       by generate_cantonnews_articles.py)
  - news/trizearticles.json     -> news/t-rize/      (not built yet — skipped
                                                       gracefully until it exists)

Each entry's catalog JSON is itself a build output for canton/t-rize (built
by their own dedicated script) or hand-edited for tokerize — this script
doesn't care which; it just bakes whatever JSON a collection currently has
into HTML, the same way for every source.

Unlike the old article.html (which left title/meta/content empty in the raw
HTML and filled them in with client-side JS after a fetch), this produces a
page where everything — <title>, meta description, Open Graph / Twitter
card tags, canonical URL, schema.org JSON-LD, and the article content
itself — is already present in the HTML that's served. That's what makes it
crawlable by search engines without needing JS execution, and renderable as
a correct link preview by Telegram/Twitter/Discord, which never execute JS.

Run from the repo root:
    python scripts/generate_news.py

Intended to run automatically via GitHub Actions on every push that touches
a catalog JSON — but safe to run locally too; it's idempotent (regenerating
from the same catalog JSON always produces the same output).
"""
import json
import html
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEWS_DIR = ROOT / "news"
TEMPLATE_PATH = ROOT / "templates" / "article-template.html"
SITE_BASE_URL = "https://tokerize.top"

# (catalog JSON path, output subfolder under news/) — add a line here the
# day trizearticles.json exists; nothing else about this script changes.
COLLECTIONS = [
    (NEWS_DIR / "tokerizearticles.json", "tokerize"),
    (NEWS_DIR / "cantonarticles.json", "canton"),
    (NEWS_DIR / "trizearticles.json", "t-rize"),
]

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_date(date_str: str) -> datetime | None:
    """Articles are dated like 'April 8, 2026'. Returns None if unparsable
    (the page still gets generated — published_time/schema date are just
    omitted rather than failing the whole build over a formatting typo)."""
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def build_schema_json(article: dict, canonical_url: str, iso_date: str) -> str:
    schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article.get("title", ""),
        "description": article.get("excerpt", ""),
        "datePublished": iso_date or article.get("date", ""),
        "image": article.get("image", ""),
        "mainEntityOfPage": canonical_url,
        "publisher": {
            "@type": "Organization",
            "name": "Tokerize",
            "url": SITE_BASE_URL,
        },
    }
    raw = json.dumps(schema, ensure_ascii=False)
    # Defensive: a "</script" substring inside any field would otherwise
    # break out of the <script type="application/ld+json"> block early.
    return raw.replace("</script", "<\\/script")


def render_article(article: dict, template: str, folder: str) -> tuple[str, str] | None:
    slug = (article.get("slug") or "").strip()
    if not slug or not SLUG_RE.match(slug):
        print(f"  ⚠️  skipping entry with invalid/missing slug: {slug!r}")
        return None

    title   = article.get("title", "")
    excerpt = article.get("excerpt", "")
    date    = article.get("date", "")
    image   = article.get("image", "")
    content = article.get("content", "")

    canonical_url = f"{SITE_BASE_URL}/news/{folder}/{slug}"
    parsed_date = parse_date(date)
    iso_date = parsed_date.strftime("%Y-%m-%dT00:00:00+00:00") if parsed_date else ""

    cover_img_tag = ""
    if image:
        cover_img_tag = (
            f'<img class="article-cover" src="{html.escape(image, quote=True)}" '
            f'alt="{html.escape(title, quote=True)}"/>'
        )

    out = template
    out = out.replace("__TITLE__", html.escape(title))
    out = out.replace("__TITLE_ATTR__", html.escape(title, quote=True))
    out = out.replace("__EXCERPT_ATTR__", html.escape(excerpt, quote=True))
    out = out.replace("__IMAGE_ATTR__", html.escape(image, quote=True))
    out = out.replace("__CANONICAL_URL__", html.escape(canonical_url, quote=True))
    out = out.replace("__ISO_DATE__", html.escape(iso_date, quote=True))
    out = out.replace("__DATE__", html.escape(date))
    out = out.replace("__COVER_IMG_TAG__", cover_img_tag)
    out = out.replace("__SCHEMA_JSON__", build_schema_json(article, canonical_url, iso_date))
    # content is trusted, author-supplied HTML (same as the old article.html
    # design) — inserted as-is, not escaped, so <p>/<h2>/<img> etc. render.
    out = out.replace("__CONTENT__", content)

    return slug, out


def process_collection(catalog_path: Path, folder: str, template: str) -> tuple[int, int]:
    """Renders one collection's catalog into news/<folder>/*.html, cleaning
    up stale pages within that same subfolder only. Returns (written, removed).
    A missing catalog file is not an error — it just means that source
    hasn't been built yet (e.g. trizearticles.json before the scraper
    exists) — skipped quietly."""
    if not catalog_path.exists():
        print(f"  (skip) {catalog_path.relative_to(ROOT)} not found — nothing to generate for news/{folder}/.")
        return 0, 0

    articles = json.loads(catalog_path.read_text(encoding="utf-8"))
    out_dir = NEWS_DIR / folder
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    current_slugs = set()
    for article in articles:
        result = render_article(article, template, folder)
        if result is None:
            continue
        slug, html_out = result
        current_slugs.add(slug)
        out_path = out_dir / f"{slug}.html"
        out_path.write_text(html_out, encoding="utf-8")
        written.append(slug)
        print(f"  ✓ news/{folder}/{slug}.html")

    # news/<folder>/ is a build output fully derived from this one catalog —
    # any previously-generated *.html page in it whose slug is no longer
    # present (entry removed, or its slug was renamed) is orphaned and must
    # be deleted here, otherwise it silently stays live on the site forever.
    # Scoped to this folder only, so processing one collection never touches
    # another's pages. Non-.html files are left untouched.
    removed = []
    for existing in sorted(out_dir.glob("*.html")):
        if existing.stem not in current_slugs:
            existing.unlink()
            removed.append(f"{folder}/{existing.name}")
            print(f"  🗑️  removed stale news/{folder}/{existing.name}")

    print(f"  -> {len(written)} page(s) from {len(articles)} entr{'y' if len(articles) == 1 else 'ies'} in {catalog_path.name}.")
    return len(written), len(removed)


def main():
    if not TEMPLATE_PATH.exists():
        print(f"❌ {TEMPLATE_PATH} not found.")
        sys.exit(1)

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    NEWS_DIR.mkdir(exist_ok=True)

    total_written = 0
    total_removed = 0
    for catalog_path, folder in COLLECTIONS:
        w, r = process_collection(catalog_path, folder, template)
        total_written += w
        total_removed += r

    print(f"\nGenerated {total_written} article page(s) total across {len(COLLECTIONS)} collection(s).")
    if total_removed:
        print(f"Removed {total_removed} stale page(s) total.")


if __name__ == "__main__":
    main()
