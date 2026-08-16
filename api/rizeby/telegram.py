"""
RizeBy Telegram Bot — Vercel Serverless Webhook Handler
File location in repo: api/rizeby/telegram.py
"""
import json, os, asyncio, sys, httpx, time
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'rizeby-bot'))

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ── Pagination state ──────────────────────────────────────────────────────────
_pagination: dict = {}
PAGE_TTL = 600  # 10 min

# ── Bot message cache (points 1, 2, 3) ───────────────────────────────────────
# Maps bot_message_id → {cmd, page, args, chat_id, thread_id, ts}
# Allows any user to reply to any bot message and get the right context.
# TTL: 24h — cleaned up lazily on each write.
_bot_msg_cache: dict = {}
BOT_MSG_TTL = 86400  # 24 hours


def _cache_bot_msg(bot_msg_id: int, cmd: str, page: int, args: list,
                   chat_id: int, thread_id):
    """Store context for a bot message so replies can find it later."""
    now = time.time()
    # Lazy cleanup: remove entries older than 24h
    expired = [k for k, v in _bot_msg_cache.items()
               if now - v.get("ts", 0) > BOT_MSG_TTL]
    for k in expired:
        del _bot_msg_cache[k]
    _bot_msg_cache[bot_msg_id] = {
        "cmd": cmd, "page": page, "args": args,
        "chat_id": chat_id, "thread_id": thread_id, "ts": now,
    }


def _get_cached_bot_msg(bot_msg_id: int) -> dict | None:
    """Retrieve cached context for a bot message if still fresh."""
    entry = _bot_msg_cache.get(bot_msg_id)
    if entry and (time.time() - entry.get("ts", 0)) < BOT_MSG_TTL:
        return entry
    return None


def _set_page(chat_id: int, cmd: str, page: int, args: list):
    _pagination[chat_id] = {"cmd": cmd, "page": page, "args": args, "ts": time.time()}


def _get_page(chat_id: int):
    state = _pagination.get(chat_id)
    if state and (time.time() - state["ts"]) < PAGE_TTL:
        return state
    return None


def _next_page(chat_id: int):
    state = _get_page(chat_id)
    if state:
        state["page"] += 1
        state["ts"] = time.time()
        return state
    return None


async def route_command(cmd: str, args: list, chat_id: int, message_id: int = 0,
                        thread_id: int = None, cached_ctx: dict = None) -> None:
    """
    cached_ctx: if the user replied to a bot message, this holds the bot message's
    stored context {cmd, page, args, chat_id, thread_id}.  We use it to send the
    response to the correct chat/thread regardless of who typed the reply.
    """
    cmd_lower = cmd.lower().strip()

    # When a cached context is available, always reply to the channel/thread
    # where the original bot message was sent (fixes point 1 & enables point 3).
    if cached_ctx:
        reply_chat_id   = cached_ctx["chat_id"]
        reply_thread_id = cached_ctx["thread_id"]
    else:
        reply_chat_id   = chat_id
        reply_thread_id = thread_id

    # ── "next" reply ──────────────────────────────────────────────────────
    if cmd_lower in ("next",):
        # Prefer cached context from the replied-to bot message (points 1+3)
        if cached_ctx:
            state = dict(cached_ctx)  # copy so we don't mutate the cache
            state["page"] += 1
            state["ts"] = time.time()
            # Also update _pagination so subsequent "next" without reply works
            _set_page(reply_chat_id, state["cmd"], state["page"], state["args"])
        else:
            state = _next_page(chat_id)
        if not state:
            await send_message(reply_chat_id, "No active list to navigate. Run a command first.",
                               thread_id=reply_thread_id)
            return
        cmd_map = {
            "govbond_owner": "govbond",
            "govwallet_timeline": "govwallet",
            "cclock_all": "cclock",
            "cclock_sv": "cclock",
            "cclock_fa": "cclock",
            "cclock_assetissuer": "cclock",
        }
        effective_cmd = cmd_map.get(state["cmd"], state["cmd"])
        await route_command(effective_cmd, state["args"], reply_chat_id,
                            message_id, reply_thread_id)
        return

    # ── "see wallet" reply after /govbond ────────────────────────────────
    if cmd_lower in ("see wallet", "seewallet") or " ".join([cmd_lower] + args).lower() == "see wallet":
        # Use cached context if available for state lookup
        ctx_chat = reply_chat_id
        state = _get_page(ctx_chat)
        if state and state["cmd"] == "govbond_owner" and state["args"]:
            nft_id = state["args"][0].lstrip("#").strip()
            try:
                from utils.github_data import get_bond_states
                from commands.governance import _load_bs_parts, cmd_govwallet
                bs_raw = await get_bond_states()
                bond_states, _, _ = _load_bs_parts(bs_raw)
                state_data = (bond_states or {}).get(str(nft_id), {})
                owner = state_data.get("owner", "")
                if owner:
                    _set_page(reply_chat_id, "govwallet", 0, [owner])
                    await send_message(reply_chat_id, await cmd_govwallet([owner], page=0),
                                       thread_id=reply_thread_id)
                else:
                    await send_message(reply_chat_id,
                                       f"Owner not found for bond #{nft_id}. Try `/govwallet 0x...`",
                                       thread_id=reply_thread_id)
            except Exception:
                await send_message(reply_chat_id,
                                   "Could not load wallet. Try `/govwallet 0x...` directly.",
                                   thread_id=reply_thread_id)
        else:
            await send_message(reply_chat_id,
                               "Reply *see wallet* right after a `/govbond` result.",
                               thread_id=reply_thread_id)
        return

    # ── "page N" reply ────────────────────────────────────────────────────
    if cmd_lower.startswith("page") and args and args[0].isdigit():
        target = int(args[0]) - 1  # 0-indexed
        if cached_ctx:
            state = dict(cached_ctx)
            state["page"] = target
            state["ts"] = time.time()
            _set_page(reply_chat_id, state["cmd"], target, state["args"])
        else:
            state = _get_page(chat_id)
            if state:
                state["page"] = target
                state["ts"] = time.time()
        if state:
            await route_command(state["cmd"], state["args"], reply_chat_id,
                                message_id, reply_thread_id)
        else:
            await send_message(reply_chat_id, "No active list to navigate.",
                               thread_id=reply_thread_id)
        return

    # ── CC sub-commands ───────────────────────────────────────────────────
    if cmd_lower in ("cc", "ccprice"):
        from commands.cc import cmd_cc_price
        await send_message(reply_chat_id, await cmd_cc_price(args), thread_id=reply_thread_id)
        return
    if cmd_lower in ("ccburnmint", "ccburn", "ccmint"):
        from commands.cc import cmd_cc_burnmint
        await send_message(reply_chat_id, await cmd_cc_burnmint(args), thread_id=reply_thread_id)
        return
    if cmd_lower in ("ccallocation", "ccalloc"):
        from commands.cc import cmd_cc_allocation
        await send_message(reply_chat_id, await cmd_cc_allocation(args), thread_id=reply_thread_id)

    if cmd_lower == "cclock":
        from commands.cclock import cmd_cclock_summary, cmd_cclock_list
        mode = args[0].lower().replace(" ", "") if args else None
        valid_modes = ("all", "sv", "fa", "assetissuer")
        if mode in valid_modes:
            page = _get_page(reply_chat_id)
            p = page["page"] if page and page["cmd"] == f"cclock_{mode}" else 0
            bot_mid = await send_message(
                reply_chat_id,
                await cmd_cclock_list(mode, p),
                thread_id=reply_thread_id,
            )
            _set_page(reply_chat_id, f"cclock_{mode}", p, [mode])
            if bot_mid: _cache_bot_msg(bot_mid, f"cclock_{mode}", p, [mode], reply_chat_id, reply_thread_id)
        else:
            await send_message(reply_chat_id, await cmd_cclock_summary(), thread_id=reply_thread_id)
        return

    # ── Price & market ────────────────────────────────────────────────────
    if cmd_lower in ("price", "p"):
        from commands.price import cmd_price
        text, markup = await cmd_price(args)
        await send_message(reply_chat_id, text, markup, thread_id=reply_thread_id)

    elif cmd_lower in ("chart", "c"):
        from commands.price import cmd_chart
        img, caption = await cmd_chart(args)
        if img: await send_photo(reply_chat_id, img, caption, thread_id=reply_thread_id)
        else:   await send_message(reply_chat_id, caption, thread_id=reply_thread_id)

    elif cmd_lower == "tvl":
        from commands.price import cmd_tvl
        await send_message(reply_chat_id, await cmd_tvl(args), thread_id=reply_thread_id)

    elif cmd_lower in ("perf", "performance"):
        from commands.market import cmd_perf
        await send_message(reply_chat_id, await cmd_perf(args), thread_id=reply_thread_id)

    elif cmd_lower in ("pricesim", "ps"):
        from commands.market import cmd_pricesim
        await send_message(reply_chat_id, await cmd_pricesim(args), thread_id=reply_thread_id)

    elif cmd_lower in ("portfoliosim", "portfolio", "bag"):
        from commands.market import cmd_portfoliosim
        await send_message(reply_chat_id, await cmd_portfoliosim(args), thread_id=reply_thread_id)

    elif cmd_lower in ("arbitrage", "ratio", "arb"):
        from commands.market import cmd_arbitrage
        await send_message(reply_chat_id, await cmd_arbitrage(args), thread_id=reply_thread_id)

    elif cmd_lower in ("market", "mkt"):
        from commands.market import cmd_market
        await send_message(reply_chat_id, await cmd_market(args), thread_id=reply_thread_id)

    elif cmd_lower in ("unbond", "queue"):
        from commands.rize import cmd_unbond
        page = _get_page(reply_chat_id)
        p = page["page"] if page and page["cmd"] == "unbond" else 0
        _set_page(reply_chat_id, "unbond", p, args)
        bot_mid = await send_message(reply_chat_id, await cmd_unbond(args, page=p), thread_id=reply_thread_id)
        if bot_mid: _cache_bot_msg(bot_mid, "unbond", p, args, reply_chat_id, reply_thread_id)

    elif cmd_lower in ("totalbonded", "bonded"):
        from commands.rize import cmd_totalbonded
        await send_message(reply_chat_id, await cmd_totalbonded(args), thread_id=reply_thread_id)

    elif cmd_lower in ("traderize",):
        from commands.price import cmd_traderize
        await send_message(reply_chat_id, await cmd_traderize(args), thread_id=reply_thread_id)

    elif cmd_lower in ("tradecc",):
        from commands.price import cmd_tradecc
        await send_message(reply_chat_id, await cmd_tradecc(args), thread_id=reply_thread_id)

    elif cmd_lower.startswith("trade") and len(cmd_lower) > 5:
        from commands.price import cmd_trade_any
        await send_message(reply_chat_id, await cmd_trade_any(cmd_lower[5:]), thread_id=reply_thread_id)

    # ── Ecosystem ─────────────────────────────────────────────────────────
    elif cmd_lower == "rwa":
        from commands.ecosystem import cmd_rwa
        await send_message(reply_chat_id, await cmd_rwa(args), thread_id=reply_thread_id)

    elif cmd_lower in ("vision87", "v87"):
        from commands.ecosystem import cmd_vision87
        await send_message(reply_chat_id, await cmd_vision87(args), thread_id=reply_thread_id)

    elif cmd_lower in ("vision60", "v60"):
        from commands.ecosystem import cmd_vision60
        await send_message(reply_chat_id, await cmd_vision60(args), thread_id=reply_thread_id)

    elif cmd_lower == "kairos":
        from commands.ecosystem import cmd_kairos
        await send_message(reply_chat_id, await cmd_kairos(args), thread_id=reply_thread_id)

    elif cmd_lower == "cantonboard":
        from commands.ecosystem import cmd_cantonboard
        bot_mid = await send_message(reply_chat_id, await cmd_cantonboard(args), thread_id=reply_thread_id)
        if not args:
            # Only set pagination context when showing the list (no args)
            # so plain text replies trigger member lookup
            _set_page(reply_chat_id, "cantonboard", 0, [])
            if bot_mid: _cache_bot_msg(bot_mid, "cantonboard", 0, [], reply_chat_id, reply_thread_id)

    elif cmd_lower == "cantonlist":
        page = _get_page(reply_chat_id)
        p = page["page"] if page and page["cmd"] == "cantonlist" else 0
        _set_page(reply_chat_id, "cantonlist", p, args)
        await _cmd_cantonlist(reply_chat_id, p, thread_id=reply_thread_id)

    elif cmd_lower.startswith("ecosystem"):
        from commands.ecosystem import cmd_ecosystem
        parts = cmd_lower[len("ecosystem"):].strip().split() + args
        entity_args = [a for a in parts if a]
        if not entity_args:
            _set_page(reply_chat_id, "ecosystem", 0, [])
        await send_message(reply_chat_id, await cmd_ecosystem(entity_args), thread_id=reply_thread_id)

    elif cmd_lower.startswith("canton") and cmd_lower not in ("cantongov", "cantonboard", "cantonlist"):
        from commands.ecosystem import cmd_canton
        parts = cmd_lower[len("canton"):].strip().split() + args
        await send_message(reply_chat_id, await cmd_canton([a for a in parts if a]), thread_id=reply_thread_id)

    # ── Canton governance ─────────────────────────────────────────────────
    elif cmd_lower.startswith("cip"):
        from commands.canton_gov import cmd_cip
        cip_args = cmd_lower[3:].strip().split() + args
        cip_args = [a for a in cip_args if a]
        if not cip_args:
            page = _get_page(reply_chat_id)
            p = page["page"] if page and page["cmd"] == "cip" else 0
            _set_page(reply_chat_id, "cip", p, [])
            bot_mid = await send_message(reply_chat_id, await cmd_cip([], page=p), thread_id=reply_thread_id)
            if bot_mid: _cache_bot_msg(bot_mid, "cip", p, [], reply_chat_id, reply_thread_id)
        else:
            await send_message(reply_chat_id, await cmd_cip(cip_args), thread_id=reply_thread_id)

    elif cmd_lower in ("cantongov", "cgov"):
        from commands.canton_gov import cmd_cantongov
        page = _get_page(reply_chat_id)
        p = page["page"] if page and page["cmd"] == "cantongov" else 0
        _set_page(reply_chat_id, "cantongov", p, args)
        bot_mid = await send_message(reply_chat_id, await cmd_cantongov(args, page=p), thread_id=reply_thread_id)
        if bot_mid: _cache_bot_msg(bot_mid, "cantongov", p, args, reply_chat_id, reply_thread_id)

    # ── Governance hub ────────────────────────────────────────────────────
    elif cmd_lower in ("govflows", "flows"):
        from commands.governance import cmd_govflows
        page = _get_page(reply_chat_id)
        p = page["page"] if page and page["cmd"] == "govflows" else 0
        _set_page(reply_chat_id, "govflows", p, args)
        bot_mid = await send_message(reply_chat_id, await cmd_govflows(args, page=p), thread_id=reply_thread_id)
        if bot_mid: _cache_bot_msg(bot_mid, "govflows", p, args, reply_chat_id, reply_thread_id)

    elif cmd_lower in ("govwhalealert", "whales", "whale"):
        from commands.governance import cmd_govwhalealert
        page = _get_page(reply_chat_id)
        p = page["page"] if page and page["cmd"] == "govwhalealert" else 0
        _set_page(reply_chat_id, "govwhalealert", p, args)
        bot_mid = await send_message(reply_chat_id, await cmd_govwhalealert(args, page=p), thread_id=reply_thread_id)
        if bot_mid: _cache_bot_msg(bot_mid, "govwhalealert", p, args, reply_chat_id, reply_thread_id)

    elif cmd_lower.startswith("govbond"):
        from commands.governance import cmd_govbond
        query = cmd_lower.replace("govbond", "").strip()
        combined = ([query] if query else []) + args
        if combined:
            _set_page(reply_chat_id, "govbond_owner", 0, combined)
        result = await cmd_govbond(combined)
        await send_message(reply_chat_id, result, thread_id=reply_thread_id)

    elif cmd_lower.startswith("govwallet"):
        from commands.governance import cmd_govwallet
        query = cmd_lower.replace("govwallet", "").strip()
        combined = ([query] if query else []) + args
        page = _get_page(reply_chat_id)
        # Only keep existing page if navigating the exact same wallet address
        p = page["page"] if (page and page["cmd"] == "govwallet" and page["args"] == combined) else 0
        _set_page(reply_chat_id, "govwallet", p, combined)
        bot_mid = await send_message(reply_chat_id, await cmd_govwallet(combined, page=p), thread_id=reply_thread_id)
        if bot_mid: _cache_bot_msg(bot_mid, "govwallet", p, combined, reply_chat_id, reply_thread_id)

    # ── Fun ───────────────────────────────────────────────────────────────
    elif cmd_lower in ("sayhello", "hello", "hi", "start"):
        from commands.fun import cmd_sayhello
        await send_message(reply_chat_id, await cmd_sayhello(args), thread_id=reply_thread_id)

    elif cmd_lower in ("insult", "roast"):
        from commands.fun import cmd_insult
        await send_message(reply_chat_id, await cmd_insult(args), thread_id=reply_thread_id)

    elif cmd_lower in ("help", "commands", ""):
        await send_message(reply_chat_id, HELP_TEXT, thread_id=reply_thread_id)

    else:
        from commands.ecosystem import lookup_any
        result = await lookup_any(cmd_lower + (" " + " ".join(args) if args else ""))
        if result:
            await send_message(reply_chat_id, result, thread_id=reply_thread_id)
        # If nothing found — silently ignore (no error in groups)


async def _cmd_cantonlist(chat_id: int, page: int, thread_id: int = None):
    from utils.github_data import get_entities
    entities = await get_entities()
    if not entities:
        await send_message(chat_id, "Could not load Canton entities.", thread_id=thread_id)
        return
    per_page = 20
    start = page * per_page
    page_ents = entities[start:start + per_page]
    total = len(entities)
    total_pages = (total - 1) // per_page + 1

    lines = [f"🏛 *Canton Network — All Entities*",
             f"_Page {page+1}/{total_pages} · {total} entities_", ""]

    for e in page_ents:
        name = e.get("name", "?")
        raw_tags = e.get("tags", [])
        clean = [t for t in raw_tags if isinstance(t, str) and len(t) < 40
                 and "\n" not in t and "Roles" not in t and "Network" not in t
                 and "items found" not in t]
        tag_str = clean[0] if clean else ""
        lines.append(f"• *{name}*" + (f" — {tag_str}" if tag_str else ""))

    lines += [
        "",
        "_Reply with a name to learn more, or type `/canton {name}`_",
        "_Reply *next* for more · Reply *page N* to jump to page N_",
    ]
    bot_msg_id = await send_message(chat_id, "\n".join(lines), thread_id=thread_id)
    _set_page(chat_id, "cantonlist", page, [])
    # Store in bot_msg_cache so any user can reply to this specific message
    if bot_msg_id:
        _cache_bot_msg(bot_msg_id, "cantonlist", page, [], chat_id, thread_id)


# ── Callback query handler (Refresh button) ───────────────────────────────────

async def handle_callback(callback: dict) -> None:
    data    = callback.get("data", "")
    chat_id = callback["message"]["chat"]["id"]
    msg_id  = callback["message"]["message_id"]
    if data.startswith("price_"):
        coin_id = data[6:]
        from commands.price import cmd_price
        # coin_id is the CoinGecko id — pass directly, cmd_price resolves it
        text, markup = await cmd_price([coin_id])
        await edit_message(chat_id, msg_id, text, markup)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(f"{TG_API}/answerCallbackQuery",
                             json={"callback_query_id": callback["id"]})
    except Exception:
        pass


HELP_TEXT = """
🤖 RIZEBY — Tokerize Intelligence Bot

━━ PRICES & CHARTS ━━

/price — RIZE price, MCap, ATH, Vol, TVL
/price cc · /price eth · /price ondo — any coin
/chart — RIZE/USD daily chart
/chart 1h · /chart 4h · /chart 1w — any timeframe
/chart cc · /chart eth 4h — any coin, any timeframe
/tvl — TVL, MCap/TVL, FDV/TVL
/market — Assets dominance, Fear&Greed, AltSzn

━━ ANALYSIS ━━

Put any coin first to change the base asset.

/perf (tickers) — Performance 7D / 30D / 90D
/pricesim (tickers) — Price sim vs other mcaps
/portfoliosim (qty) (token) to (tickers) — Bag simulation
/arbitrage (qty) (token) to (tickers) — Ratio analysis

━━ ON-CHAIN RIZE ━━

/unbond — Live unbonding queue (last 7 days)
/totalbonded — Total RIZE bonded live
/govflows — Monthly bond flows
/govwhalealert — Whale moves >5M RIZE
/govwhalealert breaks · bond+increase · releases
/govwhalealert 1M — Custom RIZE threshold
/govwallet 0x... — Full wallet governance profile
/govbond 1234 — Bond profile · reply see wallet for owner

━━ TRADING PAIRS ━━

/traderize — RIZE pairs & volumes
/tradecc — CC pairs & volumes
/tradebtc · /tradeeth · /tradelink — any coin

━━ CANTON COIN ━━

/ccprice — Canton Coin price & stats
/ccburnmint · /ccburnmint 1w — Burn/mint ratio
/ccallocation — Mint allocation by role
/cclock — CC locking overview (SVs + FAs)
/cclock sv · fa · assetissuer · all — Locking directory

━━ T-RIZE ECOSYSTEM ━━

/cantonlist — Browse all 290+ Canton entities
/canton entity — Search any Canton entity
/ecosystem — All T-RIZE partners
/ecosystem name — Partner deep-dive
/cantonboard — Canton Foundation board
/cantonboard name — Member background
/rwa — T-RIZE RWA deals overview
/vision87 · /vision60 · /kairos — Deal details

━━ CANTON GOVERNANCE ━━

/cip — Latest CIPs · reply next for more
/cip 0116 — Specific CIP deep-dive
/cantongov — Active governance proposals

━━ FUN ━━

/sayhello — GM
/insult — Get roasted

━━ NAVIGATION ━━

Reply next — next page of any list
Reply page 7 — jump to page 7
Reply a name — search within active list
""".strip()


# ── Telegram API helpers ──────────────────────────────────────────────────────

async def send_message(chat_id: int, text: str, reply_markup: dict = None,
                       thread_id: int = None) -> int | None:
    """Send a message and return the bot's message_id (for cache storage)."""
    payload = {"chat_id": chat_id, "text": text,
               "parse_mode": "Markdown", "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    if thread_id:
        payload["message_thread_id"] = thread_id
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{TG_API}/sendMessage", json=payload)
            data = r.json()
            if data.get("ok"):
                return data["result"]["message_id"]
    except Exception:
        pass
    return None


async def edit_message(chat_id: int, msg_id: int, text: str, reply_markup: dict = None) -> None:
    payload = {"chat_id": chat_id, "message_id": msg_id, "text": text,
               "parse_mode": "Markdown", "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{TG_API}/editMessageText", json=payload)
    except Exception:
        pass


async def send_photo(chat_id: int, photo_bytes: bytes, caption: str = "",
                     thread_id: int = None) -> int | None:
    """Send a photo and return the bot's message_id (for cache storage)."""
    try:
        data = {"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}
        if thread_id:
            data["message_thread_id"] = str(thread_id)
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{TG_API}/sendPhoto",
                data=data,
                files={"photo": ("chart.png", photo_bytes, "image/png")},
            )
            result = r.json()
            if result.get("ok"):
                return result["result"]["message_id"]
    except Exception:
        pass
    return None


async def register_commands() -> None:
    """Register bot commands for the Telegram command list dropdown."""
    commands = [
    {"command": "help",         "description": "All commands & how to use RIZEBY"},
    {"command": "p",            "description": "RIZE price, MCap, ATH, Vol, TVL — /p cc /p eth /p ondo for any coin"},
    {"command": "chart",        "description": "RIZE/USD daily chart — /chart 1h /chart 4h /chart 1w any timeframe"},
    {"command": "tvl",          "description": "TVL, MCap/TVL, FDV/TVL"},
    {"command": "market",       "description": "Assets dominance, Fear & Greed, AltSzn"},
    {"command": "perf",         "description": "Performance 7D / 30D / 90D — put any token first to change base"},
    {"command": "pricesim",     "description": "Price sim vs other mcaps — /pricesim (tickers)"},
    {"command": "portfoliosim", "description": "Bag simulation — /portfoliosim (qty) (token) to (tickers)"},
    {"command": "arbitrage",    "description": "Ratio analysis — /arbitrage (qty) (token) to (tickers)"},
    {"command": "unbond",       "description": "Live unbonding queue (last 7 days)"},
    {"command": "totalbonded",  "description": "Total RIZE bonded live"},
    {"command": "govflows",     "description": "Monthly bond flows"},
    {"command": "govwhalealert","description": "Whale moves >5M RIZE — breaks · bond+increase · releases · /govwhalealert 1M"},
    {"command": "govwallet",    "description": "Full wallet governance profile — /govwallet 0x..."},
    {"command": "govbond",      "description": "Bond profile · reply see wallet for owner — /govbond 1234"},
    {"command": "traderize",    "description": "RIZE pairs & volumes"},
    {"command": "tradecc",      "description": "CC pairs & volumes — /tradebtc /tradeeth /tradelink any ticker"},
    {"command": "ccprice",      "description": "Canton Coin price & stats"},
    {"command": "ccburnmint",   "description": "Burn/mint ratio — /ccburnmint · /ccburnmint 1w"},
    {"command": "ccallocation", "description": "Mint allocation by role"},
    {"command": "cclock",       "description": "CC locking overview · /cclock sv · fa · assetissuer · all"},
    {"command": "cantonlist",   "description": "Browse all 290+ Canton entities"},
    {"command": "canton",       "description": "Search any Canton entity — /canton entity"},
    {"command": "ecosystem",    "description": "All T-RIZE partners — /ecosystem or /ecosystem name for deep-dive"},
    {"command": "cantonboard",  "description": "Canton Foundation board members — /cantonboard (name)"},
    {"command": "rwa",          "description": "T-RIZE RWA deals overview"},
    {"command": "vision87",     "description": "Vision 87 by Champfleury deal"},
    {"command": "vision60",     "description": "Vision 60 by Ste-Rose deal"},
    {"command": "kairos",       "description": "Kairos Digital Loan Notes"},
    {"command": "cip",          "description": "Latest CIPs · reply next for more — /cip 0116 for specific CIP"},
    {"command": "cantongov",    "description": "Active Canton governance proposals"},
    {"command": "sayhello",     "description": "GM"},
    {"command": "insult",       "description": "Get roasted"},
]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{TG_API}/setMyCommands", json={"commands": commands})
    except Exception:
        pass


def parse_update(body: dict):
    if body.get("callback_query"):
        return "callback", body["callback_query"]

    msg = body.get("message") or body.get("edited_message")
    if not msg:
        return None, None

    # CRITICAL: use the message's own chat id, not the update-level id
    # message_thread_id handles forum sub-channels (FR chat, EN chat etc)
    chat_id   = msg["chat"]["id"]
    msg_id    = msg.get("message_id", 0)
    thread_id = msg.get("message_thread_id")  # None for main channel
    text    = (msg.get("text") or "").strip()
    if not text:
        return None, None

    # Extract reply_to_message_id — used to look up bot message cache (points 1,2,3)
    reply_to_msg_id = None
    reply_to = msg.get("reply_to_message")
    if reply_to:
        reply_to_msg_id = reply_to.get("message_id")

    # Strip bot @mention
    if "@" in text:
        text = text.split("@")[0].strip()

    parts = text.split()
    if not parts:
        return None, None

    first = parts[0].lstrip("/").lower()

    # Only process if it's a command (starts with /) or known keywords
    is_command = parts[0].startswith("/")
    known_keywords = {"next", "page"}
    # Multi-word keywords
    full_text_lower = text.lower().strip()
    if full_text_lower in ("see wallet", "seewallet"):
        return "cmd", (chat_id, "see wallet", [], msg_id, thread_id, reply_to_msg_id)

    if not is_command and first not in known_keywords:
        # Check if user is replying to a paginated context (cantonlist, ecosystem, cantonboard)
        active_state = _pagination.get(chat_id)
        if active_state and (time.time() - active_state.get("ts", 0)) < PAGE_TTL:
            active_cmd = active_state.get("cmd", "")
            if active_cmd == "cantonlist":
                return "cmd", (chat_id, "canton", parts, msg_id, thread_id, reply_to_msg_id)
            if active_cmd == "ecosystem":
                return "cmd", (chat_id, "ecosystem", parts, msg_id, thread_id, reply_to_msg_id)
            if active_cmd == "cantonboard":
                return "cmd", (chat_id, "cantonboard", parts, msg_id, thread_id, reply_to_msg_id)
        # In groups: ignore plain text — don't spam error messages
        return None, None

    # "next" as standalone
    if first == "next":
        return "cmd", (chat_id, "next", [], msg_id, thread_id, reply_to_msg_id)

    # "page N"
    if first == "page" and len(parts) > 1 and parts[1].isdigit():
        return "cmd", (chat_id, "page", [parts[1]], msg_id, thread_id, reply_to_msg_id)

    if first == "rizeby":
        cmd  = parts[1].lower() if len(parts) > 1 else "help"
        args = parts[2:]
    else:
        cmd  = first
        args = parts[1:]

    return "cmd", (chat_id, cmd, list(args), msg_id, thread_id, reply_to_msg_id)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            kind, payload = parse_update(body)
            if kind == "callback":
                asyncio.run(handle_callback(payload))
            elif kind == "cmd":
                chat_id, cmd, args, msg_id, thread_id, reply_to_msg_id = payload
                # Look up bot message cache if user replied to a bot message (points 1,2,3)
                cached_ctx = None
                if reply_to_msg_id is not None:
                    cached_ctx = _get_cached_bot_msg(reply_to_msg_id)
                asyncio.run(route_command(cmd, args, chat_id, msg_id, thread_id, cached_ctx))
            # else: plain text in group — silently ignore
        except Exception:
            pass
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        # Register commands on GET (called once when checking webhook)
        asyncio.run(register_commands())
        self.wfile.write(b"RizeBy bot is running.")

    def log_message(self, format, *args):
        pass
