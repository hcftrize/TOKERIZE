"""
Commands: /cclock, /cclock sv, /cclock fa, /cclock assetissuer, /cclock all
CC Locking data from GitHub JSONs (sv-locking.json, fa-locking.json, locking-history.json)
"""
import httpx
from utils.formatters import fmt_num, fmt_usd

RAW_BASE = "https://raw.githubusercontent.com/hcftrize/TOKERIZE/main/rize-data-hub"
SV_URL   = f"{RAW_BASE}/sv-locking.json"
FA_URL   = f"{RAW_BASE}/fa-locking.json"
HIST_URL = f"{RAW_BASE}/locking-history.json"

PAGE_SIZE = 10


def _fmt_cc(n: float) -> str:
    """Format CC amount compactly."""
    if n is None: return "—"
    if n >= 1e9: return f"{n/1e9:.2f}B CC"
    if n >= 1e6: return f"{n/1e6:.2f}M CC"
    if n >= 1e3: return f"{n/1e3:.1f}K CC"
    return f"{n:.0f} CC"

def _fmt_usd_compact(n) -> str:
    if n is None: return "—"
    if n >= 1e9: return f"~${n/1e9:.2f}B"
    if n >= 1e6: return f"~${n/1e6:.2f}M"
    if n >= 1e3: return f"~${n/1e3:.1f}K"
    return f"~${n:.0f}"


async def _fetch(url: str) -> dict | list | None:
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


async def cmd_cclock_summary() -> str:
    """
    /cclock — Summary KPIs from locking-history + sv/fa snapshots.
    """
    sv_data, fa_data, hist = await _fetch(SV_URL), await _fetch(FA_URL), await _fetch(HIST_URL)

    # ── Totals from snapshots ──────────────────────────────────────────────────
    sv_total = sv_data.get("total_sv_locked", 0) if sv_data else 0
    fa_total = fa_data.get("summary", {}).get("total_locked_app_level", 0) if fa_data else 0
    combined = sv_total + fa_total

    sv_count = len([s for s in (sv_data.get("svs", []) if sv_data else [])
                    if s.get("category") not in ("escrow_exempt", "ghost")]) if sv_data else 0
    fa_count = len([a for a in (fa_data.get("apps", []) if fa_data else [])
                    if a.get("is_active")]) if fa_data else 0

    # ── Latest history entry ───────────────────────────────────────────────────
    latest = None
    if hist and isinstance(hist, list):
        entries = [e for e in hist if e.get("pct_total") is not None]
        if entries:
            latest = entries[-1]

    pct_total = f"{latest['pct_total']:.2f}%" if latest else "—"
    pct_sv    = f"{latest['pct_sv']:.2f}%"    if latest else "—"
    pct_fa    = f"{latest['pct_fa']:.2f}%"    if latest else "—"
    usd_total = _fmt_usd_compact(latest.get("usd_total")) if latest else "—"
    usd_sv    = _fmt_usd_compact(latest.get("usd_sv"))    if latest else "—"
    usd_fa    = _fmt_usd_compact(latest.get("usd_fa"))    if latest else "—"
    updated   = (sv_data.get("updated_at", "")[:10] if sv_data else "—")

    lines = [
        "🔒 *CC Locking Overview*",
        f"_Data as of {updated}_",
        "",
        f"*Total CC Locked*",
        f"  {_fmt_cc(combined)}",
        f"  {usd_total} · {pct_total} of supply",
        "",
        f"*Locked by SVs* _{sv_count} active_",
        f"  {_fmt_cc(sv_total)}",
        f"  {usd_sv} · {pct_sv} of supply",
        "",
        f"*Locked by FAs* _{fa_count} active_",
        f"  {_fmt_cc(fa_total)}",
        f"  {usd_fa} · {pct_fa} of supply",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "_Locking Directory:_",
        "`/cclock sv` — Super Validators",
        "`/cclock fa` — Featured Apps",
        "`/cclock assetissuer` — Asset Issuers",
        "`/cclock all` — Full directory",
    ]
    return "\n".join(lines)


def _build_sv_rows(sv_data: dict) -> list:
    """Build sorted SV rows for the directory (no sub-SVs, no UUID names)."""
    rows = []
    for sv in sv_data.get("svs", []):
        cat = sv.get("category", "")
        if cat in ("standalone_sub", "hosted_sub"):
            continue
        rows.append(sv)
    rows.sort(key=lambda s: s.get("locked_balance", 0) or 0, reverse=True)
    return rows


def _build_fa_rows(fa_data: dict, asset_issuer_only: bool = False) -> list:
    """Build sorted FA rows for the directory."""
    rows = []
    for app in fa_data.get("apps", []):
        if asset_issuer_only and not app.get("asset_issuer"):
            continue
        rows.append(app)
    rows.sort(key=lambda a: a.get("locked_balance", 0) or 0, reverse=True)
    return rows


def _fmt_sv_entry(sv: dict, rank: int) -> str:
    name       = sv.get("name", "—")
    locked     = sv.get("locked_balance", 0) or 0
    tier       = sv.get("tier") or "—"
    compliance = sv.get("compliance_pct")
    cat        = sv.get("category", "")

    tier_emoji = {"Tier 1": "🟢", "Tier 2": "🟡", "No Tier": "🔴"}.get(str(tier), "⚪")
    if cat == "escrow_exempt":
        tier_emoji = "⚫"

    comp_str = f" · {compliance:.1f}%" if compliance is not None else ""
    return f"{rank}. *{name}*\n   {tier_emoji} {tier}{comp_str} · {_fmt_cc(locked)}"


def _fmt_fa_entry(app: dict, rank: int) -> str:
    name      = app.get("app_name", "—")
    inst      = app.get("institution", "")
    locked    = app.get("locked_balance", 0) or 0
    status    = app.get("status_label", "—")
    is_ai     = app.get("asset_issuer", False)

    role_badge = "Asset Issuer" if is_ai else "App Provider"
    status_emoji = {"Meets Requirement": "✅", "Below Requirement": "🔴",
                    "Data Review": "🟡", "Pause / Revoke": "⛔"}.get(status, "⚪")

    label = f"*{name}*" + (f" _{inst}_" if inst else "")
    return f"{rank}. {label}\n   {status_emoji} {role_badge} · {_fmt_cc(locked)}"


async def cmd_cclock_list(mode: str, page: int) -> str:
    """
    Paginated locking directory.
    mode: 'all' | 'sv' | 'fa' | 'assetissuer'
    """
    sv_data = await _fetch(SV_URL)
    fa_data = await _fetch(FA_URL)

    if not sv_data and not fa_data:
        return "❌ Could not load locking data."

    # ── Build rows based on mode ───────────────────────────────────────────────
    if mode == "sv":
        rows   = [("sv", r) for r in _build_sv_rows(sv_data or {})]
        title  = "🔒 *CC Locking — Super Validators*"
        hint   = "_Ranked by CC locked · Tier 1 🟢 Tier 2 🟡 No Tier 🔴_"
    elif mode == "fa":
        rows   = [("fa", r) for r in _build_fa_rows(fa_data or {})]
        title  = "🔒 *CC Locking — Featured Apps*"
        hint   = "_Ranked by CC locked · ✅ Meets · 🔴 Below · 🟡 Review_"
    elif mode == "assetissuer":
        rows   = [("fa", r) for r in _build_fa_rows(fa_data or {}, asset_issuer_only=True)]
        title  = "🔒 *CC Locking — Asset Issuers*"
        hint   = "_Ranked by CC locked · 25M CC requirement per issuer_"
    else:  # all
        sv_rows = [("sv", r) for r in _build_sv_rows(sv_data or {})]
        fa_rows = [("fa", r) for r in _build_fa_rows(fa_data or {})]
        # Merge and sort by locked desc
        combined = sorted(sv_rows + fa_rows,
                          key=lambda x: (x[1].get("locked_balance") or x[1].get("locked_balance") or 0),
                          reverse=True)
        # Re-key properly
        rows   = [(t, r) for t, r in combined]
        title  = "🔒 *CC Locking — Full Directory*"
        hint   = "_SVs + FAs · Ranked by CC locked_"

    total       = len(rows)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = max(0, min(page, total_pages - 1))
    start       = page * PAGE_SIZE
    page_rows   = rows[start:start + PAGE_SIZE]

    lines = [
        title,
        hint,
        f"_Page {page+1}/{total_pages} · {total} entries_",
        "",
    ]

    for i, (kind, entry) in enumerate(page_rows, start=start+1):
        if kind == "sv":
            lines.append(_fmt_sv_entry(entry, i))
        else:
            lines.append(_fmt_fa_entry(entry, i))
        lines.append("")  # spacing between entries

    lines += [
        f"_Reply *next* or *page N* to navigate_",
        "",
        "`/cclock sv` · `fa` · `assetissuer` · `all`",
    ]
    return "\n".join(lines)


async def cmd_cclock_alert(mode: str, page: int) -> str:
    """
    /cclock sv alert | fa alert | assetissuer alert
    Lists only entries with compliance issues (Below Requirement, Data Review, No Tier, Tier 2).
    """
    sv_data = await _fetch(SV_URL)
    fa_data = await _fetch(FA_URL)

    if mode == "sv":
        all_rows = _build_sv_rows(sv_data or {})
        # Alert = Not Tier 1 and not Escrow
        rows = [("sv", r) for r in all_rows
                if r.get("category") not in ("escrow_exempt", "ghost")
                and r.get("tier") not in ("Tier 1", None)
                or (r.get("tier") == "Tier 1" and (r.get("compliance_pct") or 100) < 70)]
        # Simpler: keep SVs with tier != Tier 1 and not escrow
        rows = [("sv", r) for r in all_rows
                if r.get("tier") not in ("Tier 1",) and r.get("category") not in ("escrow_exempt", "ghost")]
        title = "🔒 *CC Locking — SV Alerts*"
        hint  = "_SVs below Tier 1 · ranked by CC locked_"
    elif mode == "assetissuer":
        all_rows = _build_fa_rows(fa_data or {}, asset_issuer_only=True)
        rows = [("fa", r) for r in all_rows
                if r.get("status_label") not in ("Meets Requirement",) and r.get("is_active")]
        title = "🔒 *CC Locking — Asset Issuer Alerts*"
        hint  = "_Asset Issuers not meeting 25M CC requirement_"
    else:  # fa
        all_rows = _build_fa_rows(fa_data or {})
        rows = [("fa", r) for r in all_rows
                if r.get("status_label") not in ("Meets Requirement",) and r.get("is_active")]
        title = "🔒 *CC Locking — FA Alerts*"
        hint  = "_FAs not meeting CC locking requirement_"

    if not rows:
        return f"✅ No alerts — all entries meet their locking requirements."

    total       = len(rows)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = max(0, min(page, total_pages - 1))
    start       = page * PAGE_SIZE
    page_rows   = rows[start:start + PAGE_SIZE]

    lines = [
        title, hint,
        f"_Page {page+1}/{total_pages} · {total} alerts_",
        "",
    ]
    for i, (kind, entry) in enumerate(page_rows, start=start+1):
        if kind == "sv":
            lines.append(_fmt_sv_entry(entry, i))
        else:
            lines.append(_fmt_fa_entry(entry, i))
        lines.append("")

    lines += [
        "_Reply *next* or *page N* to navigate_",
        "",
        "`/cclock sv` · `fa` · `assetissuer` · `all`",
    ]
    return "\n".join(lines)


async def cmd_cclock_search(mode: str, query: str, page: int) -> str:
    """
    Reply search within a cclock list.
    Searches name, institution, app_name across the current mode.
    """
    sv_data = await _fetch(SV_URL)
    fa_data = await _fetch(FA_URL)
    q = query.lower().strip()

    if mode == "sv":
        all_rows = [("sv", r) for r in _build_sv_rows(sv_data or {})]
    elif mode == "fa":
        all_rows = [("fa", r) for r in _build_fa_rows(fa_data or {})]
    elif mode == "assetissuer":
        all_rows = [("fa", r) for r in _build_fa_rows(fa_data or {}, asset_issuer_only=True)]
    else:  # all
        sv_rows = [("sv", r) for r in _build_sv_rows(sv_data or {})]
        fa_rows = [("fa", r) for r in _build_fa_rows(fa_data or {})]
        all_rows = sorted(sv_rows + fa_rows,
                          key=lambda x: x[1].get("locked_balance") or 0, reverse=True)

    # Search: match query in name, institution, app_name
    def matches(kind, entry):
        fields = [
            entry.get("name", ""),
            entry.get("app_name", ""),
            entry.get("institution", ""),
        ]
        return any(q in f.lower() for f in fields if f)

    rows = [(k, r) for k, r in all_rows if matches(k, r)]

    if not rows:
        return f"🔍 No results for *{query}* in `cclock {mode}`."

    total       = len(rows)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = max(0, min(page, total_pages - 1))
    start       = page * PAGE_SIZE
    page_rows   = rows[start:start + PAGE_SIZE]

    lines = [
        f"🔍 *Search: {query}* in `cclock {mode}`",
        f"_Page {page+1}/{total_pages} · {total} result(s)_",
        "",
    ]
    for i, (kind, entry) in enumerate(page_rows, start=start+1):
        if kind == "sv":
            lines.append(_fmt_sv_entry(entry, i))
        else:
            lines.append(_fmt_fa_entry(entry, i))
        lines.append("")

    lines += [
        "_Reply *next* or *page N* to navigate_",
        "",
        "`/cclock sv` · `fa` · `assetissuer` · `all`",
    ]
    return "\n".join(lines)
