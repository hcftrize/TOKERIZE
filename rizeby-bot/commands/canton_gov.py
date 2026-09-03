"""
Commands: /cip [number], /cantongov
CIPs from GitHub JSON.
Lighthouse: GET /governance → {vote_requests:[]} or array
Fields: id, reason_body (=title), status, accept_votes, reject_votes
"""
import httpx
from utils.github_data import get_cips

LIGHTHOUSE_BASE = "https://lighthouse.cantonloop.com/api"
CIP_GITHUB_URL  = "https://github.com/canton-foundation/cips"


def _md_safe(text) -> str:
    """
    Telegram's legacy 'Markdown' parse_mode has NO escape syntax — an odd
    number of *, _, or ` characters anywhere in the message text makes the
    Bot API reject the ENTIRE send with 400 Bad Request (silently, from
    the user's point of view: the bot just never replies — this is
    exactly what was breaking /cip 71, 61, 57, and 10 other CIPs whose
    descriptions use "*" as a plain GitHub-markdown bullet marker).
    Since this text comes straight from cips.json / the Lighthouse API —
    content we don't control and can't rely on staying "safe" —
    neutralize Telegram's formatting characters on every dynamic field
    rather than trying to keep them balanced. "*" becomes "•" so bullet
    lists still read fine.
    """
    if not text:
        return text
    return (
        str(text)
        .replace("*", "•")
        .replace("_", "-")
        .replace("`", "'")
    )


async def cmd_cip(args: list, page: int = 0) -> str:
    cips = await get_cips()
    if not cips:
        return "Could not load CIPs data."

    cips_sorted = sorted(cips, key=lambda c: c.get("number", 0), reverse=True)

    # Specific CIP lookup
    if args:
        query = args[0].lstrip("#").lstrip("0") or "0"
        match = next(
            (c for c in cips_sorted
             if str(c.get("number","")).lstrip("0") == query or
                c.get("id","").replace("CIP-","").lstrip("0") == query),
            None,
        )
        if not match:
            return f"CIP #{args[0]} not found. Type `/cip` to see latest CIPs."

        cip_id   = match.get("id", f"CIP-{match.get('number','?')}")
        title    = _md_safe(match.get("title", "—"))
        status   = _md_safe(match.get("status", "—"))
        category = _md_safe(match.get("type", "—"))
        created  = match.get("created", "—")
        desc     = _md_safe(match.get("description", "No description."))
        if len(desc) > 800:
            desc = desc[:800] + "…"

        return "\n".join([
            f"*{cip_id} — {title}*",
            f"Status: {status} | Category: {category}",
            f"Created: {created}",
            "",
            desc,
            "",
            f"Read more: {CIP_GITHUB_URL}",
        ])

    # Paginated list — 5 per page, text status
    per_page = 5
    start = page * per_page
    page_cips = cips_sorted[start:start + per_page]
    total = len(cips_sorted)
    total_pages = (total - 1) // per_page + 1

    if not page_cips:
        return "No more CIPs to display."

    lines = [f"*Latest Canton CIPs* — Page {page+1}/{total_pages}", ""]
    for c in page_cips:
        cip_id   = c.get("id", f"CIP-{c.get('number','?')}")
        title    = _md_safe(c.get("title", "—"))
        status   = _md_safe(c.get("status", "—"))
        category = _md_safe(c.get("type", "—"))
        lines += [f"*{cip_id}*", f"  {title}", f"  {status} · {category}", ""]

    lines += [
        "─────────────────────",
        "Type `/cip {number}` for details · Reply *next* for more",
        f"{CIP_GITHUB_URL}",
    ]
    return "\n".join(lines)


async def cmd_cantongov(args: list, page: int = 0) -> str:
    """
    Canton governance proposals from Lighthouse.
    GET /governance → {vote_requests:[...]} or array
    Fields: id, reason_body (title), status, accept_votes, reject_votes
    """
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(f"{LIGHTHOUSE_BASE}/governance")
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"Could not fetch Canton governance proposals. ({e})"

    # Handle both shapes: array or {vote_requests:[]}
    if isinstance(data, list):
        all_proposals = data
    elif isinstance(data, dict):
        all_proposals = (data.get("vote_requests") or data.get("results") or
                        data.get("items") or data.get("data") or [])
    else:
        all_proposals = []

    if not all_proposals:
        return "No governance proposals found."

    # Map fields — reason_body is the title
    proposals = []
    for r in all_proposals:
        proposals.append({
            "id":     r.get("id", "—"),
            "title":  _md_safe((r.get("reason_body") or "")[:100] + ("…" if len(r.get("reason_body","")) > 100 else "")),
            "status": r.get("status", "—"),
            "accept": int(r.get("accept_votes") or 0),
            "reject": int(r.get("reject_votes") or 0),
        })

    per_page = 5
    start    = page * per_page
    page_props = proposals[start:start + per_page]

    if not page_props:
        return "No more proposals."

    total = len(proposals)
    lines = [
        f"*Canton Governance* — Page {page + 1}/{(total-1)//per_page + 1}",
        f"_{start+1}–{min(start+per_page, total)} of {total} proposals_",
        "",
    ]

    for p in page_props:
        accept = p["accept"]
        reject = p["reject"]
        total_v = accept + reject
        if total_v > 0:
            acc_pct = accept / total_v * 100
            rej_pct = reject / total_v * 100
            vote_str = f" | ✅ {acc_pct:.0f}% ❌ {rej_pct:.0f}%"
        else:
            vote_str = ""

        # Status text
        status = p["status"]
        if isinstance(status, str):
            if status.lower() in ("in_progress", "inprogress"):
                status = "In Progress"
            else:
                status = status.capitalize()

        lines += [
            f"*{p['title']}*",
            f"  {status}{vote_str}",
            "",
        ]

    if start + per_page < total:
        lines += ["Reply *next* to see more proposals."]

    return "\n".join(lines)
