"""
GeckoTerminal API wrapper — on-chain DEX data for RIZE's two Aerodrome pools.

Free, keyless public API (https://api.geckoterminal.com/api/v2) — NOT the same
key system as CoinGecko's Demo/Pro API in utils/coingecko.py. No auth header
needed. Public rate limit is ~30 req/min per IP, shared across ALL Telegram
users hitting this bot — hence the short local cache below, which matters more
than usual here (unlike CoinGecko's 5min TTL elsewhere in the bot).

NOTE ON FIELD NAMES (trades endpoint): confirmed 2026-09-16 against a live
response from /networks/base/pools/{POOL_1}/trades — tx_from_address,
from_token_amount, to_token_amount, from_token_address, to_token_address,
kind, volume_in_usd and block_timestamp (format 'YYYY-MM-DDTHH:MM:SSZ') are
all exactly as coded below. get_trade_rize_amount() additionally cross-checks
from_token_address/to_token_address against RIZE_TOKEN rather than trusting
`kind` alone, so it stays correct even if a pool's buy/sell orientation ever
differs from POOL_1's.
"""
import time
import httpx

GT_BASE = "https://api.geckoterminal.com/api/v2"
NETWORK = "base"

# The two Aerodrome pools RIZE trades on (Base network).
POOL_1 = "0x5be479f54363910264ad875b75d314f84c70d08d"
POOL_2 = "0xea5cb64754ad7aa24f7a6bbe3b724f29b4f822b8"
POOLS  = [POOL_1, POOL_2]

RIZE_TOKEN = "0x9818B6c09f5ECc843060927E8587c427C7C93583"

# Verified live 2026-09-16 for POOL_1: `kind` is reported relative to RIZE
# (sell = RIZE is from_token, buy = RIZE is to_token) — see get_trade_kind().
# POOL_2 is assumed to follow the same convention but hasn't been checked
# against a live response yet. If a POOL_2 buy/sell ever looks flipped
# (e.g. large "sells" that are clearly RIZE being bought), flip its entry
# here — one-line fix, no need to touch the parsing logic.
INVERT_KIND = {
    POOL_1: False,
    POOL_2: False,
}

# ── Short TTL cache — protects the shared 30 req/min public budget ─────────
_cache: dict = {}
CACHE_TTL = 30  # seconds — DEX data moves fast, but this still caps worst-case
                # request rate regardless of how many people spam /dex at once


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


async def gt_get(path: str, params: dict = None, cache_key: str = None) -> dict | list | None:
    """GET against the public GeckoTerminal API. No headers/auth needed."""
    if cache_key:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
    url = GT_BASE + path
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url, headers={"Accept": "application/json"}, params=params or {})
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    if cache_key and data is not None:
        _cache_set(cache_key, data)
    return data


async def get_pools_multi(addresses: list[str] = None) -> list[dict]:
    """
    Fetch both RIZE pools in a single call:
    /networks/base/pools/multi/{addr1},{addr2}
    Returns a list of pool dicts (JSON:API 'data' entries), or [] on failure.
    """
    addrs = addresses or POOLS
    joined = ",".join(addrs)
    data = await gt_get(
        f"/networks/{NETWORK}/pools/multi/{joined}",
        cache_key=f"pools_multi:{joined}",
    )
    if not data or not isinstance(data, dict):
        return []
    return data.get("data", []) or []


async def get_pool_trades(pool_address: str) -> list[dict]:
    """
    Recent trades (swaps only — buy/sell, no liquidity add/remove) for one pool.
    /networks/base/pools/{address}/trades
    Returns a list of trade dicts (JSON:API 'data' entries), or [] on failure.
    Deliberately NOT cached as long as pools — trades need to feel live — but
    still short-TTL cached (CACHE_TTL) so a burst of /dex calls from several
    users within a few seconds doesn't multiply API hits.
    """
    data = await gt_get(
        f"/networks/{NETWORK}/pools/{pool_address}/trades",
        cache_key=f"trades:{pool_address}",
    )
    if not data or not isinstance(data, dict):
        return []
    return data.get("data", []) or []


# ── Attribute helpers — defensive, tolerate a couple of plausible key names ─

def pool_attrs(pool: dict) -> dict:
    return pool.get("attributes", {}) if pool else {}


def trade_attrs(trade: dict) -> dict:
    return trade.get("attributes", {}) if trade else {}


def get_trade_wallet(attrs: dict) -> str | None:
    """Wallet address that initiated the trade — confirmed field name is
    tx_from_address (verified live 2026-09-16); the rest are kept as a
    harmless safety net in case GeckoTerminal ever renames it."""
    for key in ("tx_from_address", "from_address", "trader_address", "wallet_address"):
        v = attrs.get(key)
        if v:
            return v
    return None


def get_trade_kind(attrs: dict, pool_address: str = None) -> str | None:
    """'buy' or 'sell' relative to RIZE. Confirmed correct as-is for POOL_1
    (verified live 2026-09-16). Applies INVERT_KIND's one-line override for
    any pool where that assumption turns out to be wrong."""
    k = (attrs.get("kind") or "").lower()
    if k not in ("buy", "sell"):
        return None
    if pool_address and INVERT_KIND.get(pool_address):
        k = "sell" if k == "buy" else "buy"
    return k


def get_trade_rize_amount(attrs: dict, kind: str) -> float:
    """RIZE-side amount of the swap, regardless of direction.

    Primary path: match from_token_address/to_token_address against
    RIZE_TOKEN and read the amount off whichever side is actually RIZE —
    this is correct no matter how a given pool's `kind` is oriented.
    Falls back to the kind-based guess (buy→to_token_amount,
    sell→from_token_amount) only if neither address matches RIZE_TOKEN,
    e.g. an unexpected/changed token pairing.

    Verified live 2026-09-16 against POOL_1: sell trades have
    from_token_address == RIZE_TOKEN (RIZE sold in), buy trades have
    to_token_address == RIZE_TOKEN (RIZE bought out) — i.e. `kind` is
    already reported relative to RIZE for that pool, so both paths agree.
    """
    to_amt     = attrs.get("to_token_amount")
    from_amt   = attrs.get("from_token_amount")
    from_addr  = (attrs.get("from_token_address") or "").lower()
    to_addr    = (attrs.get("to_token_address") or "").lower()
    rize_addr  = RIZE_TOKEN.lower()
    try:
        if from_addr == rize_addr and from_amt is not None:
            return abs(float(from_amt))
        if to_addr == rize_addr and to_amt is not None:
            return abs(float(to_amt))
        # Neither side matched RIZE_TOKEN (unexpected) — fall back to kind.
        if kind == "buy" and to_amt is not None:
            return abs(float(to_amt))
        if kind == "sell" and from_amt is not None:
            return abs(float(from_amt))
    except (TypeError, ValueError):
        pass
    return 0.0


def get_trade_usd(attrs: dict) -> float:
    try:
        return abs(float(attrs.get("volume_in_usd") or 0))
    except (TypeError, ValueError):
        return 0.0


def get_trade_timestamp(attrs: dict) -> float:
    """block_timestamp is ISO 8601 (e.g. '2026-09-16T14:32:07Z') → epoch seconds."""
    ts = attrs.get("block_timestamp")
    if not ts:
        return 0.0
    try:
        import datetime
        return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        ).timestamp()
    except Exception:
        return 0.0
