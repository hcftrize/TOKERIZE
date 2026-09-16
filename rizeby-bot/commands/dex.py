"""
Commands: /dexstat, /dex
DEX-side stats and live trade feed for RIZE, aggregated across its two
Aerodrome pools on Base.

/dexstat is powered by GeckoTerminal (liquidity, FDV/MCap, volume, buy/sell
counts — all confirmed live) plus DexPaprika (cumulative buy/sell $ per
window — GeckoTerminal's pool aggregate doesn't expose that split).

/dex is powered entirely by DexPaprika (utils/dexpaprika.py): unlike
GeckoTerminal's /trades endpoint (hard-capped at ~300 trades / rolling 24h,
no pagination beyond that), DexPaprika's /transactions endpoint paginates
for real — confirmed reachable all the way back to these pools' actual
on-chain creation. "Reply next" therefore keeps scanning genuinely further
back in history each time, not just through one fixed 24h batch.

Price is deliberately NOT shown in /dexstat — /price already covers that.
Each trade in /dex DOES show its own execution price (see _fmt_trade).

WALLET CAVEAT: shown wallets are best-effort (tx.from, resolved via a free
Base RPC call) — correct for the large majority of plain EOA-initiated
trades, but can show a bundler/router instead of the true trader for swaps
routed through a smart wallet + aggregator. This is a limitation confirmed
to affect every provider checked (GeckoTerminal, DexPaprika, DEXTools' paid
API, and professional infra like Allium) — not something unique to this bot,
and not solvable without a much bigger on-chain log-parsing project. See
utils/dexpaprika.py's docstring for the full cross-check that established this.

/dexstat            — aggregated pool stats (liquidity, volume, buy/sell
                       counts + $, FDV/MCap), P1/P2 breakdown, Refresh button
/dex                — last organic buy/sell trades (both pools combined),
                       liquidity add/remove events are NOT shown here (kept
                       for a future /dexliq)
/dex <type>         — type = buy | sell | all
/dex <min>          — shortcut: same as `/dex all <min>` (no type = all)
/dex <type> <min>   — single amount = MINIMUM threshold only
/dex <type> <min> <max>
/dex <type> nc <max>  — 'nc' skips that bound
  Amounts: '500k rize' / '500krize' / '800usd' / '800 usd' all accepted.
  Bare numbers with no unit default to RIZE.
"""
import asyncio

from utils.geckoterminal import get_pools_multi, pool_attrs, POOL_1, POOL_2
from utils.dexpaprika import (
    get_pool_detail, get_pool_transactions_page, derive_trade, resolve_tx_wallet,
)
from utils.formatters import fmt_usd, fmt_rize, fmt_price, parse_dex_amount

VALID_TYPES = ("buy", "sell", "all")
PER_PAGE = 5

# How hard /dex scans DexPaprika when a filter is narrow (e.g. a high RIZE
# minimum) and matches are sparse. Each round fetches PAGES_PER_ROUND pages
# per pool IN PARALLEL (limit=100 each); scanning stops as soon as enough
# matches are found. Worst case (no matches ever found): MAX_ROUNDS *
# PAGES_PER_ROUND * 100 raw trades scanned per pool per command call — kept
# conservative to stay well within DexPaprika's free keyless rate limit
# (15 req/min) even if a user rapid-fires several narrow /dex queries.
DP_PAGE_LIMIT = 100
PAGES_PER_ROUND = 2
MAX_ROUNDS = 4


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

    # Cumulative buy/sell $ — GeckoTerminal's pool aggregate doesn't split
    # volume by direction, so this piece comes from DexPaprika instead.
    dp_details = await asyncio.gather(
        get_pool_detail(POOL_1), get_pool_detail(POOL_2), return_exceptions=True
    )
    buy_usd_24h = sell_usd_24h = buy_usd_1h = sell_usd_1h = 0.0
    dp_ok = False
    for d in dp_details:
        if not isinstance(d, dict):
            continue
        w24 = d.get("24h") or {}
        w1 = d.get("1h") or {}
        try:
            buy_usd_24h += float(w24.get("buy_usd") or 0)
            sell_usd_24h += float(w24.get("sell_usd") or 0)
            buy_usd_1h += float(w1.get("buy_usd") or 0)
            sell_usd_1h += float(w1.get("sell_usd") or 0)
            dp_ok = True
        except (TypeError, ValueError):
            pass

    if dp_ok:
        buys24_line = f"Buys 24h: {buy_totals['h24']} ({fmt_usd(buy_usd_24h)})  ·  Sells 24h: {sell_totals['h24']} ({fmt_usd(sell_usd_24h)})"
        buys1h_line = f"Buys 1h: {buy_totals['h1']} ({fmt_usd(buy_usd_1h)})  ·  Sells 1h: {sell_totals['h1']} ({fmt_usd(sell_usd_1h)})"
    else:
        buys24_line = f"Buys 24h: {buy_totals['h24']}  ·  Sells 24h: {sell_totals['h24']}"
        buys1h_line = f"Buys 1h: {buy_totals['h1']}  ·  Sells 1h: {sell_totals['h1']}"

    lines = [
        "*RIZE — DEX Stats* _(Aerodrome · Base)_",
        "_Aggregated across 2 pools_",
        "",
        f"💧 Liquidity: *{fmt_usd(total_liq)}*",
    ] + pool_lines + [
        "",
        f"📊 Volume 24h: {fmt_usd(vol_totals['h24'])}  ·  6h: {fmt_usd(vol_totals['h6'])}  ·  1h: {fmt_usd(vol_totals['h1'])}",
        buys24_line,
        buys1h_line,
        "",
        f"FDV: {fmt_usd(fdv)}  ·  MCap: {fmt_usd(mcap)}",
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


def _fmt_trade_price(v: float) -> str:
    """'0.002475 $' — smart-precision number (reuses fmt_price's logic)
    with the $ suffix the user asked for, placed right after Buy/Sell."""
    s = fmt_price(v)  # e.g. "$0.002475"
    return (s[1:] if s.startswith("$") else s) + " $"


def _fmt_trade(trade: dict, wallet: str | None) -> list[str]:
    kind = trade["kind"]
    emoji = "🟢" if kind == "buy" else "🔴"
    verb = "Buy" if kind == "buy" else "Sell"
    price_str = _fmt_trade_price(trade["price_usd"])
    out = [
        f"{emoji} {verb} @ {price_str} — {fmt_rize(trade['rize_amount'])} ({fmt_usd(trade['usd_value'])}) · {trade['pool']}",
        f"  {_fmt_ts(trade['epoch'])}",
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
        "`/dex 1M rize` — shortcut for `/dex all 1M rize` (minimum only)\n"
        "`/dex buy 500k rize 1M rize` — range\n"
        "`/dex sell 800usd` — minimum only\n"
        "`/dex all nc 2M rize` — maximum only (`nc` skips a bound)"
    )


async def _scan_pool(pool_address: str, label: str, target_count: int,
                      type_: str, min_b, max_b):
    """
    Scan one pool's transactions (newest-first) via DexPaprika, collecting
    organic buy/sell matches until target_count is reached, the pool's
    history is exhausted, or the safety cap (MAX_ROUNDS) is hit.
    Returns (matches: list[dict], exhausted: bool) — exhausted=True means
    we've genuinely reached the end of this pool's available history (not
    just the scan cap), so there's nothing more `next` could ever find here.
    """
    matches = []
    page = 1
    total_pages = None
    for _ in range(MAX_ROUNDS):
        page_nums = list(range(page, page + PAGES_PER_ROUND))
        if total_pages:
            page_nums = [p for p in page_nums if p <= total_pages]
        if not page_nums:
            return matches, True

        results = await asyncio.gather(*[
            get_pool_transactions_page(pool_address, p, DP_PAGE_LIMIT) for p in page_nums
        ])
        got_any = False
        for txns, tp in results:
            if tp:
                total_pages = tp
            if not txns:
                continue
            got_any = True
            for raw in txns:
                trade = derive_trade(raw)
                if not trade:
                    continue
                if type_ != "all" and trade["kind"] != type_:
                    continue
                if not _passes_filter(trade["rize_amount"], trade["usd_value"], min_b, max_b):
                    continue
                trade["pool"] = label
                matches.append(trade)

        page = page_nums[-1] + 1
        if len(matches) >= target_count:
            exhausted = total_pages is not None and page > total_pages
            return matches, exhausted
        if total_pages and page > total_pages:
            return matches, True
        if not got_any:
            return matches, True

    return matches, False  # hit the safety cap — more may exist, unconfirmed


async def cmd_dex(args: list, page: int = 0) -> str:
    if not args:
        type_, rest = "all", []
    else:
        maybe_type = args[0].lower()
        if maybe_type in VALID_TYPES:
            type_, rest = maybe_type, args[1:]
        else:
            # Shortcut: "/dex 1M RIZE" behaves like "/dex all 1M RIZE".
            type_, rest = "all", args

    min_b, max_b = _parse_range(rest)
    if rest and min_b is None and max_b is None:
        return _usage_text()

    # +1 beyond what this page needs, so we can tell whether there's a next page.
    target_count = (page + 1) * PER_PAGE + 1

    scan_results = await asyncio.gather(
        _scan_pool(POOL_1, "P1", target_count, type_, min_b, max_b),
        _scan_pool(POOL_2, "P2", target_count, type_, min_b, max_b),
    )
    all_matches = []
    fully_exhausted = True
    for matches, exhausted in scan_results:
        all_matches.extend(matches)
        fully_exhausted = fully_exhausted and exhausted

    all_matches.sort(key=lambda t: t["epoch"], reverse=True)

    hint = " Try a wider range or `/dex` with no filter." if (min_b or max_b) else ""

    if not all_matches:
        if fully_exhausted:
            return "No matching trades found in the full available history." + hint
        return ("No matches in the depth scanned so far." + hint +
                " Reply *next* to keep scanning further back.")

    start = page * PER_PAGE
    page_items = all_matches[start:start + PER_PAGE]
    have_more_buffered = len(all_matches) > start + PER_PAGE
    can_scan_deeper = not fully_exhausted

    if not page_items:
        if fully_exhausted:
            return "No more trades — you've reached the full available history for these pools."
        return "No more matches buffered yet. Reply *next* to keep scanning further back."

    wallets = await asyncio.gather(*[resolve_tx_wallet(t["tx_hash"]) for t in page_items])

    header = "🔄 *RIZE — Live Trades*" + (f" · {type_.upper()}" if type_ != "all" else "")
    lines = [header]
    range_sub = _fmt_range_sub(min_b, max_b)
    if range_sub:
        lines.append(range_sub)
    lines += [f"_Page {page + 1} · Aerodrome (Base) · organic swaps_", ""]

    for trade, wallet in zip(page_items, wallets):
        lines += _fmt_trade(trade, wallet)

    if have_more_buffered or can_scan_deeper:
        lines.append("_Reply *next* for more._")
    else:
        lines.append("_End of available history for these pools._")

    return "\n".join(lines)
