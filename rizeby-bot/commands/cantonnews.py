"""
Commands: /cantonnews
Latest Canton ecosystem news from cantonnews.org, scraped into
canton-ecosystem/news.json by scripts/scrape_cantonnews.py
(daily GitHub Action, auto-synced to main — see reindex-cantonnews.yml).
Scraped with cantonnews.org's written permission.
"""
from datetime import datetime, timezone

from utils.github_data import get_cantonnews

SITE_URL = "https://cantonnews.org/news"


def _relative_time(iso: str | None) -> str:
    """
    Live 'Xh ago' / 'Xd ago' / absolute date, computed fresh from the real
    published_at every time this is called — never stored, so it's never
    stale (unlike cantonnews.org's own listing-page badge, which is a
    snapshot frozen at whatever moment their page rendered).
    """
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return "—"
    now = datetime.now(dt.tzinfo or timezone.utc)
    secs = (now - dt).total_seconds()
    if secs < 0:
        secs = 0
    if secs < 3600:
        return f"{max(1, int(secs // 60))}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    days = int(secs // 86400)
    if days < 7:
        return f"{days}d ago"
    return dt.strftime("%d/%m/%Y")


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
        category = (a.get("category") or "").upper()
        when     = _relative_time(a.get("published_at"))
        title    = a.get("title", "—")
        desc     = (a.get("description") or "").strip()
        url      = a.get("url", SITE_URL)

        lines.append(f"{category} · {when}" if category else when)
        lines.append(f"*{title}*")
        lines.append("")
        lines.append(f"{desc} [Read more]({url})" if desc else f"[Read more]({url})")
        lines.append("")

    lines += [
        "─────────────────────",
        "Reply *next* or *page N* for more",
        SITE_URL,
    ]
    return "\n".join(lines)
