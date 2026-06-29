"""CoinGecko API wrapper — fully dynamic, no hardcoded coin maps."""
import os
import httpx

CG_BASE = "https://api.coingecko.com/api/v3"
CG_KEY  = os.environ.get("COINGECKO_API_KEY", "")

HEADERS = {
    "accept": "application/json",
    "x-cg-demo-api-key": CG_KEY,
}

RIZE_ID     = "rize"
RIZE_SUPPLY = 5_000_000_000

# Small cache for search results to avoid redundant API calls within a request
_search_cache: dict = {}

# ── TradingView symbol resolution ─────────────────────────────────────────────

CG_TO_TV_EXCHANGE = {
    "binance":             "BINANCE",
    "coinbase exchange":   "COINBASE",
    "coinbase":            "COINBASE",
    "gdax":                "COINBASE",
    "kraken":              "KRAKEN",
    "okex":                "OKX",
    "okx":                 "OKX",
    "bybit":               "BYBIT",
    "bybit_spot":          "BYBIT",
    "bitstamp":            "BITSTAMP",
    "kucoin":              "KUCOIN",
    "huobi":               "HUOBI",
    "bitfinex":            "BITFINEX",
    "gemini":              "GEMINI",
    "mexc":                "MEXC",
    "gate":                "GATEIO",
    "gate.io":             "GATEIO",
    "whitebit":            "WHITEBIT",
    "bitget":              "BITGET",
    "htx":                 "HTX",
    "bingx":               "BINGX",
    "lbank":               "LBANK",
    "crypto.com exchange": "CRYPTO",
    "crypto.com":          "CRYPTO",
}

EXCHANGE_PRIORITY = [
    "BINANCE","COINBASE","KRAKEN","OKX","BYBIT",
    "BITSTAMP","KUCOIN","BITFINEX","GEMINI","MEXC",
    "WHITEBIT","GATEIO","BITGET","HTX","CRYPTO",
]

QUOTE_PRIORITY = ["USD","USDT","USDC","EUR","BTC"]

_tv_symbol_cache: dict = {}


def resolve_tv_symbol_from_tickers(tickers: list, coin_symbol: str) -> str | None:
    """Method 1: resolve TradingView symbol from CoinGecko tickers (no extra API call)."""
    if not tickers:
        return None
    base = coin_symbol.upper()
    candidates = []
    for t in tickers:
        ticker_base  = (t.get("base",   "") or "").upper()
        ticker_quote = (t.get("target", "") or "").upper()
        exch_name    = (t.get("market", {}).get("name", "") or "").lower().strip()
        tv_exch      = CG_TO_TV_EXCHANGE.get(exch_name)
        if not tv_exch: continue
        if ticker_base != base: continue
        # Exclude futures/perp
        identifier = t.get("market", {}).get("identifier", "") or ""
        if "_futures" in identifier or "_perp" in identifier: continue
        exch_score  = EXCHANGE_PRIORITY.index(tv_exch) if tv_exch in EXCHANGE_PRIORITY else 99
        quote_score = QUOTE_PRIORITY.index(ticker_quote) if ticker_quote in QUOTE_PRIORITY else 99
        volume      = (t.get("converted_volume") or {}).get("usd", 0) or 0
        candidates.append({
            "symbol":      f"{tv_exch}:{ticker_base}{ticker_quote}",
            "exch_score":  exch_score,
            "quote_score": quote_score,
            "volume":      volume,
        })
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x["exch_score"], x["quote_score"], -x["volume"]))
    return candidates[0]["symbol"]


async def resolve_tv_symbol_from_search(coin_symbol: str) -> str | None:
    """Method 2: fallback via TradingView symbol search API."""
    s = coin_symbol.upper()
    try:
        url = f"https://symbol-search.tradingview.com/symbol_search/v3/?text={s}&hl=false&exchange=&lang=en&type=crypto&domain=production"
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            if not r.is_success:
                return None
            data = r.json()
        symbols = data.get("symbols", []) if isinstance(data, dict) else []
        # Filter exact base match
        def get_base(sym):
            for q in ["USDT","USDC","USD","BTC","ETH","EUR"]:
                if sym.upper().endswith(q):
                    return sym.upper()[:-len(q)]
            return sym.upper()
        exact = [item for item in symbols if get_base(item.get("symbol","")) == s]
        pool  = exact if exact else symbols[:10]
        pool  = [item for item in pool if not (item.get("symbol","").upper().endswith("_PERP") or item.get("symbol","").upper().endswith(".P"))] or pool
        scored = []
        for item in pool:
            exch  = (item.get("exchange","") or "").upper()
            sym   = (item.get("symbol",  "") or "").upper()
            es    = EXCHANGE_PRIORITY.index(exch) if exch in EXCHANGE_PRIORITY else 99
            qs    = next((i for i,q in enumerate(QUOTE_PRIORITY) if sym.endswith(q)), 99)
            scored.append((es, qs, item))
        scored.sort(key=lambda x: (x[0], x[1]))
        if not scored:
            return None
        best = scored[0][2]
        return f"{(best.get('exchange','') or '').upper()}:{(best.get('symbol','') or '').upper()}"
    except Exception:
        return None


async def get_tv_symbol(coin_id: str, coin_symbol: str) -> str:
    """
    Resolve the best TradingView symbol for a coin.
    1. Check cache
    2. Try tickers from CoinGecko
    3. Fallback to TV symbol search
    4. Last resort: BINANCE:{SYM}USDT
    """
    cache_key = coin_id.lower()
    if cache_key in _tv_symbol_cache:
        return _tv_symbol_cache[cache_key]

    # Special case: RIZE only on Kraken
    if coin_id == RIZE_ID:
        result = "KRAKEN:RIZEUSD"
        _tv_symbol_cache[cache_key] = result
        return result

    # Method 1: from tickers
    tickers = await get_tickers(coin_id)
    symbol = resolve_tv_symbol_from_tickers(tickers or [], coin_symbol)

    # Method 2: TV search fallback
    if not symbol:
        symbol = await resolve_tv_symbol_from_search(coin_symbol)

    # Last resort
    if not symbol:
        symbol = f"BINANCE:{coin_symbol.upper()}USDT"

    _tv_symbol_cache[cache_key] = symbol
    return symbol



async def cg_get(path: str, params: dict = None) -> dict | list | None:
    url = CG_BASE + path
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=HEADERS, params=params or {})
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


async def search_coin(query: str) -> dict | None:
    """Search CoinGecko and return the best match {id, symbol, name} or None."""
    q = query.lower().strip()
    if q in _search_cache:
        return _search_cache[q]

    # Special case: RIZE always resolves to "rize"
    if q == "rize":
        result = {"id": "rize", "symbol": "RIZE", "name": "RIZE"}
        _search_cache[q] = result
        return result

    data = await cg_get("/search", {"query": q})
    if not data or not data.get("coins"):
        _search_cache[q] = None
        return None

    coins = data["coins"]
    # Prefer exact symbol match first, then exact name match, then first result
    best = None
    for coin in coins:
        if coin.get("symbol", "").lower() == q:
            best = coin
            break
    if not best:
        for coin in coins:
            if coin.get("name", "").lower() == q:
                best = coin
                break
    if not best:
        best = coins[0]

    result = {
        "id":     best["id"],
        "symbol": best.get("symbol", "").upper(),
        "name":   best.get("name", best["id"]),
    }
    _search_cache[q] = result
    return result


_rank_cache: dict = {}


async def resolve_rank_coin(rank: int) -> dict | None:
    """
    Fetch the coin at market cap rank #N from CoinGecko.
    Returns {id, symbol, name, rank} or None.
    """
    if rank in _rank_cache:
        return _rank_cache[rank]
    data = await cg_get("/coins/markets", {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 1,
        "page": rank,
    })
    if not data or not isinstance(data, list) or len(data) == 0:
        return None
    coin = data[0]
    result = {
        "id":     coin["id"],
        "symbol": coin.get("symbol", "").upper(),
        "name":   coin.get("name", coin["id"]),
        "rank":   rank,
    }
    _rank_cache[rank] = result
    # Also cache in _search_cache so display_name() works
    _search_cache[f"rank{rank}"] = result
    return result


def _parse_rank(token: str) -> int | None:
    """Return N if token matches 'rankN' (case-insensitive), else None."""
    t = token.lower().strip()
    if t.startswith("rank") and t[4:].isdigit():
        return int(t[4:])
    return None


async def resolve_coin_id(token: str) -> str | None:
    """Resolve a ticker/name/rankN string to a CoinGecko coin ID."""
    rank = _parse_rank(token)
    if rank is not None:
        coin = await resolve_rank_coin(rank)
        return coin["id"] if coin else None
    match = await search_coin(token)
    return match["id"] if match else None


async def resolve_coin_ids(tokens: list[str]) -> dict[str, str]:
    """Resolve multiple tokens (including rankN) to {original: coin_id}."""
    result = {}
    for t in tokens:
        coin_id = await resolve_coin_id(t)
        if coin_id:
            result[t] = coin_id
    return result


def display_name(coin_id: str, original: str = "") -> str:
    """Return display name: use original ticker uppercased, fallback to coin_id.
    For rankN tokens, returns 'SYM #N' (e.g. 'BNB #4').
    """
    if original:
        rank = _parse_rank(original)
        if rank is not None:
            cached = _rank_cache.get(rank) or _search_cache.get(f"rank{rank}")
            if cached:
                return f"{cached['symbol'].upper()} #{cached.get('rank', rank)}"
            return f"#{rank}"
        return original.upper()
    # Use cached search result if available
    cached = _search_cache.get(coin_id.lower())
    if cached and cached.get("symbol"):
        return cached["symbol"].upper()
    return coin_id.upper()[:10]


async def parse_base_and_compare(args: list[str]) -> tuple[str, list[str]]:
    """
    Fully dynamic: resolves first non-numeric arg via live CoinGecko search.
    If it resolves to a coin != RIZE, it's the base.
    Otherwise RIZE is the base.
    Returns (base_coin_id, compare_tokens).
    """
    if not args:
        return RIZE_ID, []

    # Find first non-numeric arg as potential base
    for i, arg in enumerate(args):
        first = arg.strip()
        clean = first.replace(".", "").replace(",", "").replace(" ", "").rstrip("mkb")
        if clean.isdigit():
            continue  # skip amounts
        # Try to resolve as a coin
        coin_id = await resolve_coin_id(first)
        if coin_id and coin_id != RIZE_ID:
            return coin_id, args[:i] + args[i+1:]
        break  # first non-numeric didn't resolve to a non-RIZE coin → RIZE is base

    return RIZE_ID, args


async def get_markets(coin_ids: list[str]) -> list | None:
    return await cg_get("/coins/markets", {
        "vs_currency": "usd", "ids": ",".join(coin_ids),
        "price_change_percentage": "1h,7d,30d,90d",
        "order": "market_cap_desc", "per_page": 50, "page": 1,
    })


async def get_coin_detail(coin_id: str) -> dict | None:
    return await cg_get(f"/coins/{coin_id}", {
        "localization": "false", "tickers": "false", "market_data": "true",
        "community_data": "false", "developer_data": "false",
    })


async def get_market_chart(coin_id: str, days: int = 90) -> dict | None:
    return await cg_get(f"/coins/{coin_id}/market_chart",
                        {"vs_currency": "usd", "days": days, "interval": "daily"})


async def get_global() -> dict | None:
    return await cg_get("/global")


async def get_tickers(coin_id: str) -> list | None:
    data = await cg_get(f"/coins/{coin_id}/tickers",
                        {"include_exchange_logo": "false", "order": "volume_desc", "depth": "false"})
    return data.get("tickers") if data else None


async def get_simple_price(coin_ids: list[str]) -> dict | None:
    return await cg_get("/simple/price", {
        "ids": ",".join(coin_ids), "vs_currencies": "usd,btc,eth",
        "include_market_cap": "true", "include_24hr_vol": "true", "include_24hr_change": "true",
    })
