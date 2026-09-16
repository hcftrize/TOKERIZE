"""
Commands: /dexstat, /dex
DEX-side stats and live trade feed for RIZE, aggregated across its two
Aerodrome pools on Base. Powered by the free GeckoTerminal public API
(utils/geckoterminal.py).

Price is deliberately NOT shown here — /price already covers that.

IMPORTANT SCOPE LIMIT (confirmed against GeckoTerminal's docs): the free
/trades endpoint only returns the latest ~300 trades within a ROLLING 24H
WINDOW per pool — there is no pagination/cursor to reach older history.
/dex's "reply next" therefore paginates through everything available
(up to ~600 raw trades across both pools, filtered down), but can never
reach yesterday or further back. This is a hard ceiling of the free API,
not a bug — see the closing note it prints once you hit the end.

/dexstat            — aggregated pool stats (liquidity, volume,
                       buy/sell counts, FDV/MCap), P1/P2 breakdown, Refresh button
/dex                — last organic buy/sell trades (both pools combined),
                       liquidity add/remove events are NOT shown here (kept
                       for a future /dexliq)
/dex <type>         — type = buy | sell | all
/dex <type> <min>   — single amount = MINIMUM threshold only
/dex <type> <min> <max>
/dex <type> nc <max>  — 'nc' skips that bound
  Amounts: '500k rize' / '500krize' / '800usd' / '800 usd' all accepted.
  Bare numbers with no unit default to RIZE.
"""
from utils.geckoterminal import (
    get_pools_multi, get_pool_trades, pool_attrs, trade_attrs,
    get_trade_wallet, get_trade_kind, get_trade_rize_amount, get_trade_usd,
    get_trade_timestamp, POOL_1, POOL_2,
)
from utils.formatters import fmt_usd, fmt_rize, parse_dex_amount

VALID_TYPES = ("buy", "sell", "all")
PER_PAGE = 5


# ── /dexstat ─────────────────────────────────────────────────────────────

def _reserve(a: dict) -> float:
    try:
        return float(a.get("reserve_in_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def _label_for(a: dict) -> str:
    addr = (a.get("address") or "").lower()
    if addr == POOL_1.lower():
        return "P1"
    if addr == POOL_2.lower():
        return "P2"
    return "P?"


async def cmd_dexstat(args: list) -> tuple:
    pools = await get_pools_multi()
    if not pools:
        return "❌ Could not fetch DEX pool data right now.", {}

    infos = [pool_attrs(p) for p in pools]
    infos = [a for a in infos if a]
    if not infos:
        return "❌ Could not fetch DEX pool data right now.", {}

    # Reference pool = most liquid → used for FDV/MCap (token-level, not
    # pool-level — summing across pools would double-count, unlike
    # liquidity/volume/buy-sell counts below). Price itself is intentionally
    # not read here at all — /price already covers that.
    ref = max(infos, key=_reserve)
    fdv   = float(ref.get("fdv_usd") or 0)
    mcap  = float(ref.get("market_cap_usd") or 0) or fdv

    total_liq = sum(_reserve(a) for a in infos)

    windows = ["m5", "h1", "h6", "h24"]
    vol_totals, buy_totals, sell_totals = {}, {}, {}
    for w in windows:
        v, b, s = 0.0, 0, 0
        for a in infos:
            try:
                v += float((a.get("volume_usd") or {}).get(w) or 0)
            except (TypeError, ValueError):
                pass
            tx = (a.get("transactions") or {}).get(w) or {}
            try:
                b += int(tx.get("buys") or 0)
            except (TypeError, ValueError):
                pass
            try:
                s += int(tx.get("sells") or 0)
            except (TypeError, ValueError):
                pass
        vol_totals[w], buy_totals[w], sell_totals[w] = v, b, s

    pool_lines = []
    for a in infos:
        label = _label_for(a)
        name = a.get("name", "?")
        pool_lines.append(f"  {label} ({name}): {fmt_usd(_reserve(a))}")

    ages = [a.get("pool_created_at") for a in infos if a.get("pool_created_at")]
    age_str = min(ages)[:10] if ages else "—"

    lines = [
        "*RIZE — DEX Stats* _(Aerodrome · Base)_",
        "_Aggregated across 2 pools_",
        "",
        f"💧 Liquidity: *{fmt_usd(total_liq)}*",
    ] + pool_lines + [
        "",
        f"📊 Volume 24h: {fmt_usd(vol_totals['h24'])}  ·  6h: {fmt_usd(vol_totals['h6'])}  ·  1h: {fmt_usd(vol_totals['h1'])}",
        f"Buys 24h: {buy_totals['h24']}  ·  Sells 24h: {sell_totals['h24']}",
        f"Buys 1h: {buy_totals['h1']}  ·  Sells 1h: {sell_totals['h1']}",
        "",
        f"FDV: {fmt_usd(fdv)}  ·  MCap: {fmt_usd(mcap)}",
        f"Oldest pool since: {age_str}",
    ]

    markup = {"inline_keyboard": [[
        {"text": "🔄 Refresh", "callback_data": "dexstat_refresh"}
    ]]}
    return "\n".join(lines), markup


# ── /dex ─────────────────────────────────────────────────────────────────

def _merge_amount_tokens(tokens: list[str]) -> list[str]:
    """Join a ['500k','rize'] arg pair into '500krize' — 'nc' passes through."""
    merged = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.lower() == "nc":
            merged.append(tok)
            i += 1
            continue
        if i + 1 < len(tokens) and tokens[i + 1].lower() in ("rize", "usd"):
            merged.append(tok + tokens[i + 1])
            i += 2
        else:
            merged.append(tok)
            i += 1
    return merged


def _parse_range(rest: list[str]):
    """Returns (min_bound, max_bound), each None or (value, unit).
    One token given = MINIMUM only. 'nc' explicitly skips a bound."""
    merged = _merge_amount_tokens(rest)
    min_b = max_b = None
    if len(merged) >= 1 and merged[0].lower() != "nc":
        min_b = parse_dex_amount(merged[0])
    if len(merged) >= 2 and merged[1].lower() != "nc":
        max_b = parse_dex_amount(merged[1])
    return min_b, max_b


def _passes_filter(trade_rize: float, trade_usd: float, min_b, max_b) -> bool:
    if min_b:
        val, unit = min_b
        if (trade_rize if unit == "rize" else trade_usd) < val:
            return False
    if max_b:
        val, unit = max_b
        if (trade_rize if unit == "rize" else trade_usd) > val:
            return False
    return True


def _fmt_range_sub(min_b, max_b) -> str:
    def one(b):
        val, unit = b
        return fmt_rize(val) if unit == "rize" else fmt_usd(val)
    if min_b and max_b:
        return f"_Filter: {one(min_b)} – {one(max_b)}_"
    if min_b:
        return f"_Filter: min {one(min_b)}_"
    if max_b:
        return f"_Filter: max {one(max_b)}_"
    return ""


def _fmt_ts(epoch: float) -> str:
    if not epoch:
        return "—"
    import datetime
    dt = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _fmt_trade(t: dict, pool_label: str, kind: str) -> list[str]:
    attrs = trade_attrs(t)
    rize_amt = get_trade_rize_amount(attrs, kind)
    usd_amt = get_trade_usd(attrs)
    wallet = get_trade_wallet(attrs)
    ts = get_trade_timestamp(attrs)
    emoji = "🟢" if kind == "buy" else "🔴"
    verb = "Buy" if kind == "buy" else "Sell"
    out = [
        f"{emoji} {verb} — {fmt_rize(rize_amt)} ({fmt_usd(usd_amt)}) · {pool_label}",
        f"  {_fmt_ts(ts)}",
    ]
    if wallet:
        out.append(f"  `{wallet}`")
    else:
        out.append("  _wallet unavailable_")
    out.append("")
    return out


def _usage_text() -> str:
    return (
        "Usage:\n"
        "`/dex` — last organic buy/sell trades\n"
        "`/dex buy` · `/dex sell` · `/dex all`\n"
        "`/dex buy 500k rize 1M rize` — range\n"
        "`/dex sell 800usd` — minimum only\n"
        "`/dex all nc 2M rize` — maximum only (`nc` skips a bound)"
    )


async def cmd_dex(args: list, page: int = 0) -> str:
    if not args:
        type_, rest = "all", []
    else:
        maybe_type = args[0].lower()
        if maybe_type in VALID_TYPES:
            type_, rest = maybe_type, args[1:]
        else:
            return _usage_text()

    min_b, max_b = _parse_range(rest)

    pools_trades = []
    for label, addr in (("P1", POOL_1), ("P2", POOL_2)):
        for t in await get_pool_trades(addr):
            pools_trades.append((label, addr, t))

    if not pools_trades:
        return "❌ Could not fetch recent trades right now."

    filtered = []
    for label, addr, t in pools_trades:
        attrs = trade_attrs(t)
        kind = get_trade_kind(attrs, addr)
        if kind not in ("buy", "sell"):
            continue  # organic swaps only — drops anything add/remove-shaped
        if type_ != "all" and kind != type_:
            continue
        rize_amt = get_trade_rize_amount(attrs, kind)
        usd_amt = get_trade_usd(attrs)
        if not _passes_filter(rize_amt, usd_amt, min_b, max_b):
            continue
        filtered.append((get_trade_timestamp(attrs), label, kind, t))

    filtered.sort(key=lambda x: x[0], reverse=True)

    SCOPE_NOTE = "_GeckoTerminal's free API only covers the last ~24h (300 trades/pool max) — older trades aren't reachable here without on-chain indexing._"

    if not filtered:
        hint = " Try a wider range or `/dex` with no filter." if (min_b or max_b) else ""
        return "No matching trades found in the last ~24h." + hint + "\n" + SCOPE_NOTE

    total = len(filtered)
    start = page * PER_PAGE
    page_items = filtered[start:start + PER_PAGE]
    total_pages = (total - 1) // PER_PAGE + 1

    if not page_items:
        return "No more trades to display.\n" + SCOPE_NOTE

    header = "🔄 *RIZE — Live Trades*" + (f" · {type_.upper()}" if type_ != "all" else "")
    lines = [header]
    range_sub = _fmt_range_sub(min_b, max_b)
    if range_sub:
        lines.append(range_sub)
    lines += [f"_Page {page + 1}/{total_pages} · Aerodrome (Base) · organic · last ~24h_", ""]

    for _, label, kind, t in page_items:
        lines += _fmt_trade(t, label, kind)

    if start + PER_PAGE < total:
        lines.append("_Reply *next* for more._")
    else:
        lines.append(SCOPE_NOTE)

    return "\n".join(lines)
