"""
DexPaprika API wrapper — deep historical DEX trade data for RIZE's two
Aerodrome pools (Base). Free, keyless, no signup required for the endpoints
used here (https://api.dexpaprika.com).

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
import time
import datetime
import httpx

from utils.geckoterminal import POOL_1, POOL_2, RIZE_TOKEN  # reuse same constants

DP_BASE = "https://api.dexpaprika.com"
NETWORK = "base"
POOLS = [POOL_1, POOL_2]

# Free public Base mainnet RPC — no key, standard well-known endpoint.
BASE_RPC = "https://mainnet.base.org"

# ── Short TTL cache for pool/page fetches — protects DexPaprika's free-tier
# rate limit (15 req/min keyless, 50 req/min with a free account) given /dex
# can scan multiple pages per command. ──────────────────────────────────────
_cache: dict = {}
CACHE_TTL = 30  # seconds

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
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    _cache_set(cache_key, data)
    return data


async def get_pool_transactions_page(pool_address: str, page: int, limit: int = 100):
    """
    GET /networks/base/pools/{address}/transactions?page=N&limit=M
    Returns (transactions: list[dict], total_pages: int). Newest-first.
    Returns ([], 0) on any failure — callers treat that as 'stop scanning'.
    """
    cache_key = f"txns:{pool_address}:{page}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    url = f"{DP_BASE}/networks/{NETWORK}/pools/{pool_address}/transactions"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                url, headers={"Accept": "application/json"},
                params={"page": page, "limit": limit},
            )
            r.raise_for_status()
            data = r.json()
    except Exception:
        return [], 0
    txns = data.get("transactions") or []
    page_info = data.get("page_info") or {}
    try:
        total_pages = int(page_info.get("total_pages") or 0)
    except (TypeError, ValueError):
        total_pages = 0
    result = (txns, total_pages)
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
