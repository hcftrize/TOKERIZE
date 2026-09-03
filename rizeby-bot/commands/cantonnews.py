"""
Commands: /cantonnews [keyword], /cnews [keyword]
Latest Canton ecosystem news from cantonnews.org, scraped into
canton-ecosystem/news.json by scripts/scrape_cantonnews.py
(daily GitHub Action, auto-synced to main — see reindex-cantonnews.yml).
Scraped with cantonnews.org's written permission.

Search: /cantonnews t-rize (or a plain-text reply to a /cantonnews /
/cantonnews-search message) searches title + description across the full
dataset — same simple substring-match approach as /cclock's search, no
external fuzzy-matching dependency needed for this.

Note: `category` is scraped and kept in news.json (used to group the
weekly digest — see scripts/post_cantonnews_digest.py) but deliberately
NOT shown in this paginated list, to keep it readable — just title, time,
description, link.
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


def _format_article_block(a: dict) -> str:
    """*Title* · Xh ago\n\nDescription... [Read more](url)"""
    when  = _relative_time(a.get("published_at"))
    title = a.get("title", "—")
    desc  = (a.get("description") or "").strip()
    url   = a.get("url", SITE_URL)

    body = f"{desc} [Read more]({url})" if desc else f"[Read more]({url})"
    return f"*{title}* · {when}\n\n{body}"


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
        lines.append(_format_article_block(a))
        lines.append("")

    lines += [
        "─────────────────────",
        "Reply *next*, *page N*, or a *keyword* to search",
        SITE_URL,
    ]
    return "\n".join(lines)


async def cmd_cantonnews_search(query: str, page: int = 0) -> str:
    data = await get_cantonnews()
    articles = data.get("articles", data) if isinstance(data, dict) else data
    if not articles:
        return "Could not load Canton news."

    q = query.lower().strip()

    def matches(a: dict) -> bool:
        fields = [a.get("title", ""), a.get("description", "")]
        return any(q in f.lower() for f in fields if f)

    results = [a for a in articles if matches(a)]

    if not results:
        return f"🔍 No results for *{query}* in Canton News."

    per_page = 5
    total = len(results)
    total_pages = max(1, (total - 1) // per_page + 1)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    page_results = results[start:start + per_page]

    lines = [
        f"🔍 *Search: {query}*",
        f"_Page {page + 1}/{total_pages} · {total} result(s)_",
        "",
    ]
    for a in page_results:
        lines.append(_format_article_block(a))
        lines.append("")

    lines += [
        "─────────────────────",
        "Reply *next*, *page N*, or a new *keyword* to search again",
        SITE_URL,
    ]
    return "\n".join(lines)
