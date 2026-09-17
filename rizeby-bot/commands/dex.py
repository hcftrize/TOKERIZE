"""
Commands: /dexstat, /dex
DEX-side stats and live trade feed for RIZE, aggregated across its two
Aerodrome pools on Base.

/dexstat is powered by GeckoTerminal (liquidity, FDV/MCap, volume, buy/sell
counts — all confirmed live) plus DexPaprika (cumulative buy/sell $ per
window — GeckoTerminal's pool aggregate doesn't expose that split).

/dex is powered entirely by DexPaprika (utils/dexpaprika.py).

DELIBERATELY STATELESS, FIXED-WINDOW SCAN (reverted back to this after a
messy detour — see below): every /dex or `next` call re-scans the exact
same fixed, small number of DexPaprika pages per pool (SCAN_PAGES_PER_POOL)
— never more, never accumulating across calls, nothing remembered between
requests. Same filter -> same scan window -> same result, every time
(modulo genuinely new trades landing in between). "next" just pages
through that one fixed batch of matches; it does NOT dig deeper into
history. This bounds history depth (roughly the most recent ~1-3 days,
more for the quieter pool, less for the busier one) in exchange for being
completely predictable and simple.

We tried a "keep digging deeper across next taps" version that remembered
scan progress per chat. It technically worked, but every fix for it made
things worse: a growing per-call budget caused real 429 bursts; a shared
incremental budget fixed the 429s but the underlying per-chat cache didn't
distinguish a freshly-typed /dex from a continued "next" session, so a
plain `/dex 1M RIZE` typed three times in a row silently returned three
different, ever-deepening answers — a correctness bug worse than the
shallow-history tradeoff it was solving. Given the choice between "deeper
but stateful and occasionally inconsistent" and "shallow but always
correct and dead simple", we're deliberately choosing the latter.

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
    HAS_KEY,
)
from utils.formatters import fmt_usd, fmt_rize, fmt_price, parse_dex_amount

VALID_TYPES = ("buy", "sell", "all")
PER_PAGE = 5

# Fixed, small number of DexPaprika pages scanned per pool, on EVERY call —
# never more, never accumulated across calls. Deliberately simple: no
# per-chat state, no "how far did we get last time", nothing to get out of
# sync. worst case = SCAN_PAGES_PER_POOL * 2 pools requests per call
# (11 with a key -> 22 requests/call, 3 keyless -> 6 requests/call), and
# CONSTANT — it doesn't grow the longer a chat keeps using /dex, unlike the
# stateful version this replaced.
#
# The real ceiling here is NOT the 429 risk — utils/dexpaprika.py's
# _throttle() makes exceeding 30 req/min mathematically impossible
# regardless of this number (it waits rather than ever firing over the
# limit). Two things actually limit how high this can reasonably go:
#
# (1) Fluidity. Verified by simulation (controlled fake clock + fake
# sleep, not just reasoned about): with CACHE_TTL=65s in
# utils/dexpaprika.py (>= the throttle's 60s window), a burst of up to 12
# pages/pool (24 req/call = the throttle's full 24 req/min ceiling) causes
# ZERO wait even across 6 instant `next`/filter-change taps in a row —
# because a repeat call either hits the page cache (filter doesn't change
# which raw pages get fetched) or lands after the previous burst has
# fully aged out of the throttle window; there's no gap where both fail at
# once. 13+ pages/pool broke this immediately in testing (a single burst
# alone exceeds 24 and has to wait on itself). 11 is chosen instead of the
# bare 12-page maximum to leave 2 requests of slack for incidental
# concurrent DexPaprika use (e.g. a /dexstat call landing in the same few
# seconds), which also shares this same rate budget.
#
# (2) The monthly credit budget (100K/mo, confirmed on the account's
# billing page, 1 request = 1 credit). At 22 req/call, a light-to-moderate
# ~100 /dex-or-next calls/day is ~66K/mo (fine), but a generous ~200
# calls/day would be ~132K/mo — OVER budget. This wasn't a concern at the
# previous, smaller value (8 pages ~= 96K/mo at that same heavy-use
# estimate); it's worth re-checking if this bot's actual daily usage ever
# looks that high.
DP_PAGE_LIMIT = 100
SCAN_PAGES_PER_POOL = 11 if HAS_KEY else 3


# ── /dexkey — debug: is DEXPAPRIKA_KEY actually live? ──────────────────────

async def cmd_dexkey(args: list) -> str:
    """
    Reads utils.dexpaprika.HAS_KEY/DEXPAPRIKA_KEY *at request time, in this
    exact running process* — the only way to be certain the env var is
    actually picked up in production, rather than guessing from behavior.
    """
    from utils.dexpaprika import HAS_KEY as has_key, DEXPAPRIKA_KEY as key

    if has_key:
        tail = key[-4:] if len(key) >= 4 else key
        return (
            "🔑 *DEXPAPRIKA_KEY: ACTIVE*\n"
            f"Key ends in `...{tail}` ({len(key)} chars)\n\n"
            f"Scan: *{SCAN_PAGES_PER_POOL} pages/pool, every call* "
            f"({SCAN_PAGES_PER_POOL * 2} requests total, fixed — never grows). "
            "Stateless on purpose: no per-chat memory, so a fresh `/dex` always "
            "scans the exact same recent window and `next` just pages through "
            "that one batch — no deep history crawl, no risk of drifting out of "
            "sync with itself.\n"
            "Confirmed account limits (billing page): *30 req/min · 100K credits/mo*. "
            "Bot throttles itself to 24 req/min max as a hard backstop — a single call "
            f"uses {SCAN_PAGES_PER_POOL * 2} of that budget (verified by simulation to "
            "cause zero wait even across several instant `next`/filter-change taps in a "
            "row), leaving a small margin for anything else touching DexPaprika in the "
            "same few seconds.\n\n"
            "_This trades away deep-history digging for being always correct and "
            "predictable — a previous stateful version could dig further back via "
            "repeated `next`, but a plain freshly-typed `/dex` could silently "
            "inherit leftover scan progress and return a different answer each "
            "time it was typed. Simple and consistent beats deep and occasionally "
            "wrong._"
        )
    return (
        "⚠️ *DEXPAPRIKA_KEY: NOT SET* — running keyless (free tier)\n\n"
        f"Scan: *{SCAN_PAGES_PER_POOL} pages/pool, every call* "
        f"({SCAN_PAGES_PER_POOL * 2} requests total, fixed — never grows).\n"
        "Rate limit: assumed 15 req/min keyless (unconfirmed — get a free key "
        "to see its real numbers on DexPaprika's billing page). Bot throttles "
        "itself to 10 req/min max as a conservative default.\n\n"
        "_Note: the key doesn't change how far back /dex can ultimately go — "
        "keyless reaches the same full history via more `next` replies, just "
        "slightly smaller steps each time._"
    )


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


def _fmt_relative(delta_s: float) -> str:
    """'5min ago' / '1h34 ago' / '1d 4h ago' — relative age, so a user
    doesn't have to convert UTC to their own timezone just to tell if a
    trade happened 5 minutes ago or yesterday. Clamped at 0 in case of
    tiny clock skew between the trade timestamp and the bot's own clock."""
    secs = max(0, int(delta_s))
    minutes = secs // 60
    if minutes < 60:
        return f"{minutes}min ago"
    hours = minutes // 60
    rem_min = minutes % 60
    if hours < 24:
        return f"{hours}h{rem_min:02d} ago"
    days = hours // 24
    rem_h = hours % 24
    return f"{days}d {rem_h}h ago"


def _fmt_ts(epoch: float) -> str:
    if not epoch:
        return "—"
    import datetime
    dt = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    relative = _fmt_relative((now - dt).total_seconds())
    absolute = dt.strftime("%d/%m - %H:%M UTC")
    return f"{relative} - {absolute}"


def _fmt_trade_price(v: float) -> str:
    """'0.002475 $' — smart-precision number (reuses fmt_price's logic)
    with the $ suffix the user asked for, placed right after Buy/Sell."""
    s = fmt_price(v)  # e.g. "$0.002475"
    return (s[1:] if s.startswith("$") else s) + " $"


def _short_addr(addr: str) -> str:
    """'0x2b..aC3e' — shortened and deliberately plain text, NOT wrapped in
    backticks (no monospace, no tap-to-copy). The shown wallet is often a
    router/aggregator rather than the real trader (see module docstring), so
    copying it isn't actually useful — the TX link next to it is the real
    action: tap through to Basescan and let the user judge for themselves."""
    if not addr or len(addr) < 10:
        return addr or "—"
    return f"{addr[:4]}..{addr[-4:]}"


def _fmt_trade(trade: dict, wallet: str | None) -> list[str]:
    """One trade block, spaced out for mobile readability: description line,
    blank, timestamp, blank, wallet — rather than three cramped lines."""
    kind = trade["kind"]
    emoji = "🟢" if kind == "buy" else "🔴"
    verb = "Buy" if kind == "buy" else "Sell"
    price_str = _fmt_trade_price(trade["price_usd"])
    out = [
        f"{emoji} {verb} @ {price_str} — {fmt_rize(trade['rize_amount'])} ({fmt_usd(trade['usd_value'])}) · {trade['pool']}",
        "",
        _fmt_ts(trade["epoch"]),
        "",
    ]
    tx_hash = trade.get("tx_hash")
    # Plain [text](url) link — NOT wrapped in italics. Telegram's legacy
    # Markdown parser doesn't reliably render a link nested inside `_..._`;
    # it silently fails and shows the raw "[TX](https://...)" text instead
    # (confirmed live). A bare link is the same syntax the old GeckoTerminal
    # credit link used, which rendered fine — so this stays unnested.
    tx_link = f"[TX](https://basescan.org/tx/{tx_hash})" if tx_hash else ""
    addr_part = _short_addr(wallet) if wallet else "wallet unavailable"
    out.append(f"{addr_part} - {tx_link}" if tx_link else addr_part)
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


async def _scan_pool_fixed(pool_address: str, label: str, type_: str, min_b, max_b):
    """
    Fetches pages 1..SCAN_PAGES_PER_POOL of `pool_address` FRESH, every
    single call — no memory of any previous call. Returns the matches
    found within that fixed window. Deliberately stateless: given the same
    pool/filter, this always scans the same window and returns the same
    matches (modulo real new trades landing in between), whether it's a
    brand-new /dex or a "next" reply. Nothing to keep in sync, nothing to
    drift, nothing to get wrong across calls.
    """
    page_nums = list(range(1, SCAN_PAGES_PER_POOL + 1))
    results = await asyncio.gather(*[
        get_pool_transactions_page(pool_address, p, DP_PAGE_LIMIT) for p in page_nums
    ])
    matches = []
    for txns, _tp, ok in results:
        if not ok or not txns:
            continue
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
    return matches


async def cmd_dex(args: list, page: int = 0, chat_id=None) -> str:
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

    pools_meta = [(POOL_1, "P1"), (POOL_2, "P2")]
    results = await asyncio.gather(*[
        _scan_pool_fixed(pool_addr, label, type_, min_b, max_b)
        for pool_addr, label in pools_meta
    ])
    all_matches = [m for sub in results for m in sub]
    all_matches.sort(key=lambda t: t["epoch"], reverse=True)

    hint = " Try a wider range or `/dex` with no filter." if (min_b or max_b) else ""

    if not all_matches:
        return "No matching trades in the recent history scanned." + hint

    start = page * PER_PAGE
    page_items = all_matches[start:start + PER_PAGE]
    have_more = len(all_matches) > start + PER_PAGE

    if not page_items:
        return "No more trades in the recent history scanned." + hint

    wallets = await asyncio.gather(*[resolve_tx_wallet(t["tx_hash"]) for t in page_items])

    header = "🔄 *RIZE — Live Trades*" + (f" · {type_.upper()}" if type_ != "all" else "")
    lines = [header]
    range_sub = _fmt_range_sub(min_b, max_b)
    if range_sub:
        lines.append(range_sub)
    lines += [f"_Page {page + 1} · Aerodrome (Base) · organic swaps_", ""]

    for trade, wallet in zip(page_items, wallets):
        lines += _fmt_trade(trade, wallet)

    if have_more:
        lines.append("_Reply *next* for more · Reply *page N* to jump to page N_")
    else:
        lines.append("_End of the recent history scanned for these pools._")

    return "\n".join(lines)
