"""
DexPaprika API wrapper — deep historical DEX trade data for RIZE's two
Aerodrome pools (Base). Works keyless (free, no signup) on
https://api.dexpaprika.com, and picks up a free-tier API key automatically
from the DEXPAPRIKA_KEY env var if set.

RATE LIMIT / CREDITS (confirmed against the account's own billing page,
2026-09-16 — not just DexPaprika's marketing docs, which quoted a generic
50 req/min elsewhere and turned out to not match this actual Free-tier
key): **30 req/min, 100K credits/month** (rolling 30-day window). 1 API
request = 1 credit flat, no per-endpoint multiplier — confirmed via
DexPaprika's own pricing page. RATE_LIMIT_PER_MIN below is set with a
safety margin under the real 30/min ceiling. 100K credits/month is
generous for this bot's actual traffic (≈3300/day) as long as /dex's
per-call scan budget stays modest — see TOTAL_BUDGET_PER_CALL in
commands/dex.py, deliberately kept small precisely for this reason.

AUTH (confirmed against DexPaprika's docs): the key goes in the
`Authorization` header as its ENTIRE raw value — no "Bearer " prefix, e.g.
`Authorization: api_xxxxx`. This is a FREE-tier key on the standard
api.dexpaprika.com host; a PRO key would need api-pro.dexpaprika.com instead
(sending a free key to the pro host, or vice versa, returns a 403) — if
DEXPAPRIKA_KEY turns out to be a Pro key, DP_BASE below needs updating.

Confirmed LIVE 2026-09-16 against real responses for POOL_1:
  - GET /networks/base/pools/{address}
    → pool-level aggregates: price, liquidity_usd, token_reserves (RIZE +
      quote amounts separately, in both native and USD), and per-window
      (5m/15m/30m/1h/6h/24h) volume_usd/buy_usd/sell_usd/buys/sells/txns.
  - GET /networks/base/pools/{address}/transactions?page=N&limit=M
    → envelope EXACTLY: {"transactions": [...], "page_info": {"limit",
      "page", "total_items", "total_pages"}}. Newest-first. Paginable well
      beyond GeckoTerminal's 24h/300-trade ceiling — confirmed reachable
      back to this pool's actual on-chain creation (~2670 trades total as
      of this writing, page 27 at limit=100 = the true last page).
    Transaction fields used: id (tx hash), created_at (ISO8601 'Z' UTC),
    amount_0, amount_1 (SIGNED, already decimal-adjusted — not raw wei),
    volume_0, volume_1 (unsigned), price_0_usd, price_1_usd, token_0,
    token_1 (addresses).

SIGN CONVENTION (verified by cross-checking a real trade — tx hash
0x9a85a5d4...e9a14 — against its Basescan transaction detail AND
GeckoTerminal's independently-reported `kind` for the same trade): for a
given token, a POSITIVE amount means the wallet that initiated the trade
RECEIVED that token (a BUY of it); a NEGATIVE amount means it SENT that
token away (a SELL). This is the trader's own balance delta, not the pool
reserve's delta (those are opposite signs of each other).

WALLET CAVEAT (important, confirmed by the same cross-check): DexPaprika's
own `sender`/`recipient` fields are the pool-level Swap-event participants,
which are very often a router or aggregator contract — NOT reliably the
actual trader. On that same test transaction, neither sender nor recipient
matched the tx's true signer. Instead of trusting those fields, we resolve
the wallet via a direct call to Base's free public JSON-RPC
(eth_getTransactionByHash → .from) — the same signal GeckoTerminal exposes
as tx_from_address. This is best-effort, industry-standard practice: exactly
correct for a plain EOA-initiated swap (the large majority of trades), and
can point to a bundler/relayer instead of the true trader only for trades
routed through an ERC-4337 smart wallet + aggregator — a limitation
confirmed (via Basescan + Allium's own public docs) to affect every
provider checked, not something unique to this bot.
"""
import os
import time
import asyncio
import datetime
import httpx

from utils.geckoterminal import POOL_1, POOL_2, RIZE_TOKEN  # reuse same constants

DP_BASE = "https://api.dexpaprika.com"
NETWORK = "base"
POOLS = [POOL_1, POOL_2]

DEXPAPRIKA_KEY = (os.environ.get("DEXPAPRIKA_KEY") or "").strip()
HAS_KEY = bool(DEXPAPRIKA_KEY)


def _dp_headers() -> dict:
    headers = {"Accept": "application/json"}
    if DEXPAPRIKA_KEY:
        headers["Authorization"] = DEXPAPRIKA_KEY
    return headers

# Free public Base mainnet RPC — no key, standard well-known endpoint.
BASE_RPC = "https://mainnet.base.org"

# ── Client-side rate limiter — makes "never send DexPaprika a 429" an actual
# guarantee instead of a hope. Shared across EVERY request this process
# makes (both pools, both endpoints): a sliding 60s window tracks recent
# request timestamps, and any new request blocks until there's real room
# under the limit. This is what commands/dex.py's small per-call scan
# budget was implicitly relying on without enforcing — fine for one call in
# isolation, but nothing stopped several rapid `next` taps from cumulatively
# blowing the per-minute ceiling. Now the limit is enforced here, once, for
# real, regardless of how fast the user taps.
RATE_LIMIT_PER_MIN = 24 if HAS_KEY else 10  # safety margin under confirmed 30 / assumed 15
_request_times: list = []
_throttle_lock = asyncio.Lock()


async def _throttle():
    """Blocks (if needed) until firing one more request is safely under
    RATE_LIMIT_PER_MIN in the trailing 60s. Reserves the slot atomically
    (lock held only for the bookkeeping, not the actual HTTP call after)."""
    while True:
        async with _throttle_lock:
            now = time.time()
            # prune anything older than 60s
            while _request_times and now - _request_times[0] > 60:
                _request_times.pop(0)
            if len(_request_times) < RATE_LIMIT_PER_MIN:
                _request_times.append(now)
                return
            wait_for = 60 - (now - _request_times[0]) + 0.05
        await asyncio.sleep(max(wait_for, 0.05))


# ── TTL cache for pool/page fetches ─────────────────────────────────────────
# Set to 65s — deliberately just OVER the throttle's 60s sliding window, not
# under it. commands/dex.py's /dex always re-requests the exact same fixed
# page numbers every call, so a repeat call either (a) lands within
# CACHE_TTL and costs 0 new requests (pure cache hit), or (b) lands after
# CACHE_TTL, by which point the previous call's requests have ALSO fully
# aged out of the throttle's 60s window. There's no gap between those two
# cases where a repeat call both misses the cache AND still collides with
# the still-warm throttle window — which is exactly what used to cause a
# real, measured ~25s wait on a "next" tapped 30-60s after the previous
# one (verified by simulation). A shorter TTL (e.g. 30s) reopens that gap.
_cache: dict = {}
CACHE_TTL = 65  # seconds — see rationale above; keep >= the throttle's 60s window

# Resolved tx wallets are cached permanently (per-process) — a mined tx's
# `from` never changes, so there's no reason to ever re-fetch it.
_wallet_cache: dict = {}


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


async def get_pool_detail(pool_address: str) -> dict | None:
    """GET /networks/base/pools/{address} — pool-level aggregates."""
    cache_key = f"pool:{pool_address}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    url = f"{DP_BASE}/networks/{NETWORK}/pools/{pool_address}"
    await _throttle()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=_dp_headers())
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    _cache_set(cache_key, data)
    return data


async def get_pool_transactions_page(pool_address: str, page: int, limit: int = 100):
    """
    GET /networks/base/pools/{address}/transactions?page=N&limit=M
    Returns (transactions: list[dict], total_pages: int, ok: bool). Newest-first.

    ok=False means the request genuinely FAILED (rate-limited or errored,
    even after retries) — NOT the same thing as a confirmed-empty page.
    This distinction matters: /dex's deep-scan can fire enough concurrent
    page requests (POOL_1 + POOL_2, several pages/round) to occasionally
    trip DexPaprika's rate limit mid-scan. A 429 used to be silently treated
    as "no more transactions here", which made /dex falsely declare a
    pool's history "fully exhausted" partway through — losing real trades
    that were simply rate-limited, not actually absent. Callers must only
    treat an empty result as real exhaustion when ok=True.

    Gets ONE quick retry on failure (short fixed backoff) — deliberately
    light. The incremental scan design in commands/dex.py means a page that
    still fails just gets picked up cheaply on the *next* `/dex`/`next`
    call, so this doesn't need to fight hard to succeed within one call;
    an earlier version retried up to 3x with growing backoff specifically
    to make deep, bursty scans "succeed no matter what" — that combination
    (large bursts + aggressive retries) is what made /dex both slow and
    still occasionally lossy. Small bursts + a light retry is the right
    balance now that scanning itself is bounded and incremental.
    """
    cache_key = f"txns:{pool_address}:{page}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    url = f"{DP_BASE}/networks/{NETWORK}/pools/{pool_address}/transactions"
    data = None
    for attempt in range(2):
        await _throttle()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    url, headers=_dp_headers(),
                    params={"page": page, "limit": limit},
                )
                if r.status_code == 429:
                    if attempt == 0:
                        await asyncio.sleep(0.8)
                        continue
                    return [], 0, False
                r.raise_for_status()
                data = r.json()
                break
        except Exception:
            if attempt == 0:
                await asyncio.sleep(0.4)
                continue
            return [], 0, False
    if data is None:
        return [], 0, False
    txns = data.get("transactions") or []
    page_info = data.get("page_info") or {}
    try:
        total_pages = int(page_info.get("total_pages") or 0)
    except (TypeError, ValueError):
        total_pages = 0
    result = (txns, total_pages, True)
    _cache_set(cache_key, result)
    return result


def _parse_iso(ts: str) -> float:
    if not ts:
        return 0.0
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        ).timestamp()
    except Exception:
        return 0.0


def derive_trade(txn: dict, rize_addr: str = None) -> dict | None:
    """
    Normalize one raw DexPaprika transaction into:
    {tx_hash, kind, rize_amount, usd_value, price_usd, created_at, epoch}
    Returns None if this row isn't a clean two-sided RIZE swap — guards
    against any non-swap rows (liquidity add/remove) this endpoint might
    ever include, via a structural check (both sides non-zero, opposite
    signs) rather than trusting an explicit type field DexPaprika doesn't
    actually provide.
    """
    rize_addr = (rize_addr or RIZE_TOKEN).lower()
    t0 = (txn.get("token_0") or "").lower()
    t1 = (txn.get("token_1") or "").lower()
    a0, a1 = txn.get("amount_0"), txn.get("amount_1")
    if a0 is None or a1 is None:
        return None
    try:
        a0f, a1f = float(a0), float(a1)
    except (TypeError, ValueError):
        return None
    if a0f == 0 or a1f == 0 or (a0f > 0) == (a1f > 0):
        return None  # not a clean two-sided swap — skip

    if t0 == rize_addr:
        raw = a0f
        rize_amount = abs(float(txn.get("volume_0") or 0))
        price_usd_raw = txn.get("price_0_usd")
    elif t1 == rize_addr:
        raw = a1f
        rize_amount = abs(float(txn.get("volume_1") or 0))
        price_usd_raw = txn.get("price_1_usd")
    else:
        return None  # unexpected pairing — not a RIZE swap

    try:
        price_usd = float(price_usd_raw) if price_usd_raw is not None else 0.0
    except (TypeError, ValueError):
        price_usd = 0.0

    kind = "buy" if raw > 0 else "sell"
    return {
        "tx_hash": txn.get("id"),
        "kind": kind,
        "rize_amount": rize_amount,
        "usd_value": rize_amount * price_usd,
        "price_usd": price_usd,
        "created_at": txn.get("created_at"),
        "epoch": _parse_iso(txn.get("created_at")),
    }


async def resolve_tx_wallet(tx_hash: str) -> str | None:
    """
    Best-effort trader wallet via Base's free public JSON-RPC
    (eth_getTransactionByHash → .from) — see module docstring for the
    accuracy caveat. Permanently cached per tx_hash.
    """
    if not tx_hash:
        return None
    if tx_hash in _wallet_cache:
        return _wallet_cache[tx_hash]
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.post(BASE_RPC, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "eth_getTransactionByHash",
                "params": [tx_hash],
            })
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    addr = ((data or {}).get("result") or {}).get("from")
    if addr:
        _wallet_cache[tx_hash] = addr
    return addr
