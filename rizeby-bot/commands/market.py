"""
Commands: /perf, /pricesim, /portfoliosim, /arbitrage, /market, /volume
Mobile-friendly text format. Cache TTL 5min.
"""
import httpx
from utils.coingecko import (
    get_markets, get_market_chart, get_global, get_simple_price,
    resolve_coin_ids, resolve_coin_id, get_coin_detail, parse_base_and_compare,
    display_name, RIZE_ID, RIZE_SUPPLY, cg_get, parse_mcap_token,
)
from utils.formatters import fmt_usd, fmt_pct, fmt_price, fmt_sim_price, fmt_num, parse_amount
import time

_cache: dict = {}
CACHE_TTL = 300

async def _cached(key: str, coro):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    result = await coro
    if result is not None:
        _cache[key] = {"data": result, "ts": time.time()}
    return result


async def cmd_perf(args: list) -> str:
    base_id, compare_tokens = await parse_base_and_compare(args)
    token_map = await resolve_coin_ids(compare_tokens)
    all_ids   = list(set([base_id] + list(token_map.values())))

    markets_data = await _cached(f"mkts_{','.join(sorted(all_ids))}", get_markets(all_ids))
    if not markets_data:
        return "Could not fetch performance data."
    by_id = {c["id"]: c for c in markets_data}

    def sym(coin_id, original=""):
        """Use display_name for rankN support, fallback to markets data symbol."""
        if original:
            return display_name(coin_id, original)
        return (by_id.get(coin_id, {}).get("symbol") or coin_id).upper()

    base_name = sym(base_id)

    perf_90d = {}
    for cid in all_ids:
        chart = await _cached(f"chart90_{cid}", get_market_chart(cid, 90))
        if chart and chart.get("prices") and len(chart["prices"]) >= 2:
            p0 = chart["prices"][0][1]
            p1 = chart["prices"][-1][1]
            if p0:
                perf_90d[cid] = ((p1 - p0) / p0) * 100

    def make_row(cid, label):
        c = by_id.get(cid, {})
        return {"label": label,
                "p7":  c.get("price_change_percentage_7d_in_currency"),
                "p30": c.get("price_change_percentage_30d_in_currency"),
                "p90": perf_90d.get(cid)}

    rows = [make_row(base_id, base_name)]
    for orig, cid in token_map.items():
        if cid != base_id:
            rows.append(make_row(cid, sym(cid, orig)))
    rows = [rows[0]] + sorted(rows[1:], key=lambda r: (r["p7"] or -9999), reverse=True)

    def fc(v):
        if v is None: return "—"
        return f"+{v:.1f}%" if v > 0 else f"{v:.1f}%"

    def period_block(key, label):
        lines = [f"*{label}*"]
        pairs = [rows[i:i+2] for i in range(0, len(rows), 2)]
        for pair in pairs:
            parts = [f"{r['label']}: {fc(r[key])}" for r in pair]
            lines.append("  ".join(parts))
        return "\n".join(lines)

    return "\n".join([
        "📊 *Performance Comparison*",
        "_7D, 30D and 90D price performance against USD_",
        "",
        period_block("p7", "7 Days"),
        "",
        period_block("p30", "30 Days"),
        "",
        period_block("p90", "90 Days"),
    ])


async def cmd_pricesim(args: list) -> str:
    base_id, compare_tokens = await parse_base_and_compare(args)

    # Split mcap tokens from regular tokens
    mcap_tokens = [(t, parse_mcap_token(t)) for t in compare_tokens if parse_mcap_token(t) is not None]
    regular_tokens = [t for t in compare_tokens if parse_mcap_token(t) is None]

    token_map = await resolve_coin_ids(regular_tokens)
    all_ids = list(set([base_id] + list(token_map.values())))
    markets_data = await _cached(f"mkts_{','.join(sorted(all_ids))}", get_markets(all_ids))
    if not markets_data:
        return "Could not fetch market data."
    by_id = {c["id"]: c for c in markets_data}

    def sym(coin_id, original=""):
        if original:
            return display_name(coin_id, original)
        return (by_id.get(coin_id, {}).get("symbol") or coin_id).upper()

    base_name = sym(base_id)
    base = by_id.get(base_id, {})
    base_price  = base.get("current_price", 0)
    base_supply = base.get("circulating_supply") or (RIZE_SUPPLY if base_id == RIZE_ID else 1)
    base_mcap   = base.get("market_cap", 0)
    base_rank   = base.get("market_cap_rank")

    lines = [
        f"🎯 *{base_name} Price Simulation*",
        f"_What would {base_name} be worth if it had each asset's market cap?_",
        "",
        f"Current: {fmt_price(base_price)}",
        f"Supply: {fmt_num(base_supply)}",
        f"MCap: {fmt_usd(base_mcap)}" + (f" · Rank #{base_rank}" if base_rank else ""),
        "",
    ]
    rows = []

    # Regular token rows
    for orig, cid in token_map.items():
        if cid == base_id: continue
        c = by_id.get(cid, {})
        target_mcap = c.get("market_cap", 0)
        if not target_mcap: continue
        target_rank = c.get("market_cap_rank")
        hyp_price   = target_mcap / base_supply
        pct_change  = ((hyp_price / base_price) - 1) * 100 if base_price else 0
        pct_of_mcap = (base_mcap / target_mcap) * 100 if target_mcap else 0
        rows.append((hyp_price, sym(cid, orig), target_mcap, target_rank, pct_change, pct_of_mcap))

    # MCap target rows
    for orig, target_mcap in mcap_tokens:
        hyp_price   = target_mcap / base_supply
        pct_change  = ((hyp_price / base_price) - 1) * 100 if base_price else 0
        pct_of_mcap = (base_mcap / target_mcap) * 100 if target_mcap else 0
        label = fmt_usd(target_mcap)
        rows.append((hyp_price, label, target_mcap, None, pct_change, pct_of_mcap))

    rows.sort(key=lambda r: r[0], reverse=True)

    for hyp_price, label, target_mcap, target_rank, pct_change, pct_of_mcap in rows:
        sign = "+" if pct_change > 0 else ""
        rank_str = f" #{target_rank}" if (target_rank and "#" not in label) else ""
        lines += [
            f"*{label}{rank_str}*",
            f"  MCap: {fmt_usd(target_mcap)}",
            f"  {base_name} price: {fmt_sim_price(hyp_price)}",
            f"  Change: {sign}{pct_change:.0f}%",
            f"  {base_name} = {pct_of_mcap:.3f}% of {label.split('#')[0].strip()}",
            "",
        ]
    return "\n".join(lines)


async def cmd_portfoliosim(args: list) -> str:
    tokens = list(args)
    amount = None
    base_id = RIZE_ID
    compare_tokens = []

    if "to" in [t.lower() for t in tokens]:
        to_idx = [t.lower() for t in tokens].index("to")
        left   = tokens[:to_idx]
        right  = tokens[to_idx+1:]
        for t in left:
            parsed = parse_amount(t)
            if parsed is not None: amount = parsed
            else:
                resolved = await resolve_coin_id(t)
                if resolved: base_id = resolved
        compare_tokens = right
    else:
        base_id, remaining = await parse_base_and_compare(tokens)
        for t in remaining:
            parsed = parse_amount(t)
            if parsed is not None and amount is None: amount = parsed
            elif parse_amount(t) is None: compare_tokens.append(t)

    # Resolve base_name early for error message (use ticker or coin_id)
    _base_ticker = next((t for t in tokens if t.lower() not in [str(parse_amount(t) or "").lower()] and parse_amount(t) is None), base_id)
    base_name_early = _base_ticker.upper() if _base_ticker != base_id else base_id.upper()
    if amount is None:
        return (
            f"Please include your {base_name_early} amount.\n\n"
            f"Format: `/portfoliosim {{amount}} {{coin}} to {{assets}}`\n"
            f"Example: `/portfoliosim 1000000 rize to eth link mantra`"
        )

    # Split mcap tokens from regular tokens
    mcap_tokens = [(t, parse_mcap_token(t)) for t in compare_tokens if parse_mcap_token(t) is not None]
    regular_tokens = [t for t in compare_tokens if parse_mcap_token(t) is None]

    token_map = await resolve_coin_ids(regular_tokens)
    all_ids = list(set([base_id] + list(token_map.values())))
    markets_data = await _cached(f"mkts_{','.join(sorted(all_ids))}", get_markets(all_ids))
    if not markets_data:
        return "Could not fetch market data."
    by_id = {c["id"]: c for c in markets_data}

    def sym(coin_id, original=""):
        if original:
            return display_name(coin_id, original)
        return (by_id.get(coin_id, {}).get("symbol") or coin_id).upper()

    base = by_id.get(base_id, {})
    base_name   = sym(base_id)
    base_price  = base.get("current_price", 0)
    base_supply = base.get("circulating_supply") or (RIZE_SUPPLY if base_id == RIZE_ID else 1)
    base_mcap   = base.get("market_cap", 0)
    base_rank   = base.get("market_cap_rank")
    current_bag = amount * base_price

    base_lines = [
        "💼 *Portfolio Simulation*",
        f"_Estimated value of {fmt_num(amount)} {base_name} at each simulated target price_",
        "",
        f"Current: {fmt_price(base_price)}",
    ]
    if base_rank:
        base_lines.append(f"Rank: #{base_rank}")
    base_lines += [
        f"MCap: {fmt_usd(base_mcap)}",
        f"Current bag: {fmt_usd(current_bag)}",
        "",
    ]
    lines = base_lines

    rows = []
    for orig, cid in token_map.items():
        if cid == base_id: continue
        c = by_id.get(cid, {})
        target_mcap = c.get("market_cap", 0)
        if not target_mcap: continue
        target_rank = c.get("market_cap_rank")
        hyp_price = target_mcap / base_supply
        bag_value = amount * hyp_price
        pct = ((bag_value / current_bag) - 1) * 100 if current_bag else 0
        label = sym(cid, orig)
        rows.append((hyp_price, label, target_mcap, target_rank, bag_value, pct))

    # MCap target rows
    for orig, target_mcap in mcap_tokens:
        hyp_price = target_mcap / base_supply
        bag_value = amount * hyp_price
        pct = ((bag_value / current_bag) - 1) * 100 if current_bag else 0
        label = fmt_usd(target_mcap)
        rows.append((hyp_price, label, target_mcap, None, bag_value, pct))

    rows.sort(key=lambda r: r[0], reverse=True)

    for hyp_price, label, target_mcap, target_rank, bag_value, pct in rows:
        sign = "+" if pct > 0 else ""
        rank_str = f" #{target_rank}" if (target_rank and "#" not in label) else ""
        pct_of_mcap = (base_mcap / target_mcap) * 100 if target_mcap else 0
        lines += [
            f"*{label}{rank_str}*",
            f"  MCap: {fmt_usd(target_mcap)}",
            f"  {base_name} @ {fmt_price(hyp_price)}",
            f"  Bag: {fmt_usd(bag_value)} ({sign}{pct:.0f}%)",
            f"  {base_name} = {pct_of_mcap:.3f}% of {label.split('#')[0].strip()}",
            "",
        ]
    return "\n".join(lines)


async def cmd_arbitrage(args: list) -> str:
    tokens = list(args)
    amount = None
    base_id = RIZE_ID
    base_ticker = ""
    compare_tokens = []

    if "to" in [t.lower() for t in tokens]:
        to_idx = [t.lower() for t in tokens].index("to")
        left   = tokens[:to_idx]
        right  = tokens[to_idx+1:]
        for t in left:
            parsed = parse_amount(t)
            if parsed is not None: amount = parsed
            else:
                resolved = await resolve_coin_id(t)
                if resolved: base_id = resolved; base_ticker = t
        compare_tokens = right
    else:
        base_id, remaining = await parse_base_and_compare(tokens)
        # Find the ticker that became the base
        for t in tokens:
            if parse_amount(t) is None:
                resolved = await resolve_coin_id(t)
                if resolved == base_id: base_ticker = t; break
        for t in remaining:
            parsed = parse_amount(t)
            if parsed is not None and amount is None: amount = parsed
            elif parse_amount(t) is None: compare_tokens.append(t)

    # Fetch markets to get proper symbols
    token_map = await resolve_coin_ids(compare_tokens)
    all_ids   = list(set([base_id] + list(token_map.values())))
    markets_data = await _cached(f"mkts_{','.join(sorted(all_ids))}", get_markets(all_ids))
    by_id = {c["id"]: c for c in markets_data} if markets_data else {}

    def sym(coin_id, original=""):
        if original:
            return display_name(coin_id, original)
        return (by_id.get(coin_id, {}).get("symbol") or coin_id).upper()

    base_name = sym(base_id, base_ticker)

    # Fetch 90d charts for all coins
    charts = {}
    for cid in all_ids:
        chart = await _cached(f"chart90_{cid}", get_market_chart(cid, 90))
        if chart and chart.get("prices"):
            prices = chart["prices"]

            def get_price_at(days_ago, p=prices):
                idx = max(0, len(p) - 1 - days_ago)
                return p[idx][1] if p else None

            charts[cid] = {
                "now": prices[-1][1] if prices else None,
                "7d":  get_price_at(7),
                "30d": get_price_at(30),
                "90d": get_price_at(min(90, len(prices)-1)),
            }

    # Build output — no apostrophes in plain strings to avoid Markdown issues
    intro = (
        "Compare gains and losses in compared asset units. "
        "A positive value means you gain that asset by swapping today vs X days ago."
    )
    lines = [
        f"*Ratio Analysis — {base_name}*",
        f"_{intro}_",
        "",
    ]

    if amount:
        base_now = charts.get(base_id, {}).get("now") or 0
        lines.append(f"Bag: {fmt_num(amount)} {base_name} = {fmt_usd(amount * base_now)}")
        lines.append("")

    for orig, cid in token_map.items():
        if cid == base_id: continue
        label = sym(cid, orig)
        bc = charts.get(base_id, {})
        cc = charts.get(cid, {})

        if not bc.get("now") or not cc.get("now"):
            lines += [f"*{base_name} to {label}*: data unavailable", ""]
            continue

        lines.append(f"*{base_name} to {label}*")

        for period in ["7d", "30d", "90d"]:
            b_now  = bc.get("now", 0)
            b_past = bc.get(period, 0)
            c_now  = cc.get("now", 0)
            c_past = cc.get(period, 0)

            if not all([b_now, b_past, c_now, c_past]):
                lines.append(f"  {period.upper()}: no data")
                continue

            ratio_now  = b_now  / c_now
            ratio_past = b_past / c_past
            delta      = ratio_now - ratio_past

            if amount:
                total_delta = delta * amount
                pct = ((ratio_now / ratio_past) - 1) * 100 if ratio_past else 0
                sign_d = "+" if total_delta >= 0 else ""
                sign_p = "+" if pct >= 0 else ""
                lines.append(
                    f"  {period.upper()}: {sign_d}{fmt_num(total_delta, 4)} {label} ({sign_p}{pct:.1f}%)"
                )
            else:
                pct = ((ratio_now / ratio_past) - 1) * 100 if ratio_past else 0
                sign_d = "+" if delta >= 0 else ""
                sign_p = "+" if pct >= 0 else ""
                lines.append(
                    f"  {period.upper()}: {sign_d}{delta:.6f} {label}/unit ({sign_p}{pct:.1f}%)"
                )

        lines.append("")

    return "\n".join(lines)


async def cmd_market(args: list) -> str:
    import asyncio

    async def fetch_fng():
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get("https://api.alternative.me/fng/?limit=1")
                return r.json()
        except Exception:
            return None

    async def fetch_altszn():
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get("https://api.coin-stats.com/v2/markets/chart/altcoin_season_index?type=3m")
                return r.json()
        except Exception:
            return None

    global_data, fng_data, altszn_data = await asyncio.gather(
        get_global(), fetch_fng(), fetch_altszn()
    )

    lines = ["🌍 *Market Context*", ""]

    if global_data:
        gd = global_data.get("data", {})
        total_mcap = gd.get("total_market_cap", {}).get("usd", 0)
        dominance  = gd.get("market_cap_percentage", {})
        btc_d = dominance.get("btc", 0)
        eth_d = dominance.get("eth", 0)

        # LINK.D — compute from chainlink mcap (not in market_cap_percentage)
        link_rize = await cg_get("/simple/price", {
            "ids": "chainlink,rize", "vs_currencies": "usd", "include_market_cap": "true"
        })
        link_mcap = link_rize.get("chainlink", {}).get("usd_market_cap", 0) if link_rize else 0
        rize_mcap = link_rize.get("rize", {}).get("usd_market_cap", 0) if link_rize else 0
        link_d = (link_mcap / total_mcap * 100) if (link_mcap and total_mcap) else 0
        rize_d = (rize_mcap / total_mcap * 100) if (rize_mcap and total_mcap) else 0

        lines += [
            f"BTC.D: {btc_d:.2f}%",
            f"ETH.D: {eth_d:.2f}%",
            f"LINK.D: {link_d:.3f}%",
            f"RIZE.D: {rize_d:.6f}%",
            "",
            f"TOTAL MCap: {fmt_usd(total_mcap)}",
        ]

    if fng_data:
        fng = fng_data.get("data", [{}])[0]
        lines.append(f"Fear & Greed: {fng.get('value','—')} ({fng.get('value_classification','—')})")

    if altszn_data:
        vals = altszn_data if isinstance(altszn_data, list) else altszn_data.get("data", [])
        if vals:
            last  = vals[-1]
            score = int(last.get("value", 0) if isinstance(last, dict) else last)
            season = "Altcoin Season 🟢" if score >= 75 else "Neutral ⚪" if score >= 50 else "Bitcoin Season 🟠"
            lines += [f"BTC/Alt Season: {season}", f"ALTSZN Score: {score}/100"]

    return "\n".join(lines)


async def cmd_volume(args: list) -> str:
    """
    RIZE volume: live 24h (CoinGecko) + historical series from
    rize-data-hub/volume-history.json (same file + KPIs as the site's
    "RIZE Volume" panel — see rzRenderVolumePanel() in the data hub HTML).

    volume-history.json only ever contains COMPLETE days (today's partial
    volume is deliberately excluded until 00:15 UTC the next day), so
    series[-1] is always "yesterday" — matching the website panel exactly.
    "Daily" is the one live number, fetched fresh from CoinGecko the same
    way /price and /tvl do; everything else is derived from the JSON.
    """
    import asyncio
    from datetime import datetime
    from utils.github_data import get_volume_history

    history, detail = await asyncio.gather(
        get_volume_history(),
        _cached(f"detail_{RIZE_ID}", get_coin_detail(RIZE_ID)),
    )

    series = (history or {}).get("series", []) if isinstance(history, dict) else []
    if not series:
        return "Could not load RIZE volume data."

    volumes = [p.get("volume", 0) or 0 for p in series]
    dates   = [p.get("date", "")  for p in series]
    yesterday_vol = volumes[-1]

    live_vol = None
    if detail:
        live_vol = detail.get("market_data", {}).get("total_volume", {}).get("usd")
    if not live_vol:
        live_vol = yesterday_vol  # fallback if the live CoinGecko call fails

    def avg_last(n):
        s = volumes[-n:]
        return sum(s) / len(s) if s else None

    def sum_last(n):
        return sum(volumes[-n:]) if len(volumes) >= n else None

    def period_pct(n):
        """% change of the sum of the last n complete days vs the n days before that."""
        if len(volumes) < 2 * n:
            return None
        recent = sum(volumes[-n:])
        prior  = sum(volumes[-2*n:-n])
        return ((recent - prior) / prior) * 100 if prior else None

    pct_1d = ((live_vol - yesterday_vol) / yesterday_vol) * 100 if yesterday_vol else None

    def arrow(v):
        if v is None: return "—"
        return f"{'📈' if v > 0 else '📉'} {fmt_pct(v)}"

    def money(v):
        return fmt_usd(v) if v is not None else "—"

    def fmt_ath_date(d):
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            return f"{dt.strftime('%b')} {dt.day}, {dt.year}"
        except Exception:
            return d

    ath_days = sorted(zip(dates, volumes), key=lambda x: x[1], reverse=True)[:5]

    lines = [
        "🔊 *RIZE Volume*",
        "",
        "*RIZE Volume Data*",
        f"Daily: {money(live_vol)}",
        f"Yesterday: {money(yesterday_vol)}",
        f"7D Daily Avg: {money(avg_last(7))}",
        f"1M Daily Avg: {money(avg_last(30))}",
        f"3M Daily Avg: {money(avg_last(90))}",
        f"1Y Daily Avg: {money(avg_last(365))}",
        "",
        "*Cumulative RIZE Volume*",
        f"7D: {money(sum_last(7))}",
        f"30D: {money(sum_last(30))}",
        f"90D: {money(sum_last(90))}",
        f"1Y: {money(sum_last(365))}",
        f"All-Time: {money(sum(volumes))}",
        "",
        "*RIZE Volume Perf*",
        f"1D: {arrow(pct_1d)}",
        f"7D: {arrow(period_pct(7))}",
        f"30D: {arrow(period_pct(30))}",
        f"90D: {arrow(period_pct(90))}",
        "",
        "*Volume ATH Days*",
    ]
    for d, v in ath_days:
        lines.append(f"{fmt_ath_date(d)} : {money(v)}")

    return "\n".join(lines)
