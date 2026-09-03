"""
Commands: /cantonnews
Latest Canton ecosystem news from cantonnews.org, scraped into
canton-ecosystem/news.json by scripts/scrape_cantonnews.py
(daily GitHub Action, auto-synced to main — see reindex-cantonnews.yml).
Scraped with cantonnews.org's written permission.
"""
from datetime import datetime

from utils.github_data import get_cantonnews

SITE_URL = "https://cantonnews.org/news"


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except Exception:
        return "—"


async def cmd_cantonnews(args: list, page: int = 0) -> str:
    data = await get_cantonnews()
    articles = data.get("articles", data) if isinstance(data, dict) else data
    if not articles:
        return "Could not load Canton news."

    per_page = 5
    start = page * per_page
    page_articles = articles[start:start + per_page]
    total = len(articles)
    total_pages = (total - 1) // per_page + 1

    if not page_articles:
        return "No more articles to display."

    lines = [f"*Canton News* — Page {page + 1}/{total_pages}", ""]
    for a in page_articles:
        title = a.get("title", "—")
        date  = _fmt_date(a.get("published_at"))
        url   = a.get("url", SITE_URL)
        lines.append(f'"{title}" ({date}) : [Read more]({url})')
        lines.append("")

    lines += [
        "─────────────────────",
        "Reply *next* for more",
        SITE_URL,
    ]
    return "\n".join(lines)
