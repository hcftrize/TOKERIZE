#!/usr/bin/env python3
"""
generate_news.py — Static pre-renderer for the Tokerize /news section.

Reads articles.json (the single source of truth — same schema as before:
slug, title, excerpt, date, image, content) and, for every entry, bakes a
full standalone HTML page at news/<slug>.html using templates/article-template.html.

Unlike the old article.html (which left title/meta/content empty in the raw
HTML and filled them in with client-side JS after a fetch), this produces a
page where everything — <title>, meta description, Open Graph / Twitter
card tags, canonical URL, schema.org JSON-LD, and the article content
itself — is already present in the HTML that's served. That's what makes it
crawlable by search engines without needing JS execution, and renderable as
a correct link preview by Telegram/Twitter/Discord, which never execute JS.

Run from the repo root:
    python scripts/generate_news.py

Intended to run automatically via .github/workflows/generate-news.yml on
every push that touches articles.json — but safe to run locally too; it's
idempotent (regenerating from the same articles.json always produces the
same output).
"""
import json
import html
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_JSON = ROOT / "articles.json"
TEMPLATE_PATH = ROOT / "templates" / "article-template.html"
OUTPUT_DIR = ROOT / "news"
SITE_BASE_URL = "https://tokerize.top"

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


def render_article(article: dict, template: str) -> tuple[str, str] | None:
    slug = (article.get("slug") or "").strip()
    if not slug or not SLUG_RE.match(slug):
        print(f"  ⚠️  skipping entry with invalid/missing slug: {slug!r}")
        return None

    title   = article.get("title", "")
    excerpt = article.get("excerpt", "")
    date    = article.get("date", "")
    image   = article.get("image", "")
    content = article.get("content", "")

    canonical_url = f"{SITE_BASE_URL}/news/{slug}"
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


def main():
    if not ARTICLES_JSON.exists():
        print(f"❌ {ARTICLES_JSON} not found.")
        sys.exit(1)
    if not TEMPLATE_PATH.exists():
        print(f"❌ {TEMPLATE_PATH} not found.")
        sys.exit(1)

    articles = json.loads(ARTICLES_JSON.read_text(encoding="utf-8"))
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    OUTPUT_DIR.mkdir(exist_ok=True)

    written = []
    current_slugs = set()
    for article in articles:
        result = render_article(article, template)
        if result is None:
            continue
        slug, html_out = result
        current_slugs.add(slug)
        out_path = OUTPUT_DIR / f"{slug}.html"
        out_path.write_text(html_out, encoding="utf-8")
        written.append(slug)
        print(f"  ✓ news/{slug}.html")

    # news/ is a build output fully derived from articles.json — any
    # previously-generated *.html page whose slug is no longer present
    # (article removed, or its slug was renamed) is orphaned and must be
    # deleted here, otherwise it silently stays live on the site forever.
    # Non-.html files (placeholders, an images/ subfolder, etc.) are left
    # untouched.
    removed = []
    for existing in sorted(OUTPUT_DIR.glob("*.html")):
        if existing.stem not in current_slugs:
            existing.unlink()
            removed.append(existing.name)
            print(f"  🗑️  removed stale news/{existing.name}")

    print(f"\nGenerated {len(written)} article page(s) from {len(articles)} entr{'y' if len(articles)==1 else 'ies'} in articles.json.")
    if removed:
        print(f"Removed {len(removed)} stale page(s) no longer referenced in articles.json: {', '.join(removed)}")


if __name__ == "__main__":
    main()
