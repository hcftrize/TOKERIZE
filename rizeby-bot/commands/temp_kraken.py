"""
TEMPORARY command: /howbigarewefucked  (not in /help, not in the Telegram command menu)

Live RIZE left on a fixed list of Kraken wallets on Base: balanceOf() read on the chain at every call (no cache, no database).
Reply "details" to the bot's answer for the balance of every wallet.

File location in repo: rizeby-bot/commands/temp_kraken.py
Needs: nothing new (httpx is already used). Uses ALCHEMY_KEY like /totalbonded, the public Base RPC if it is not set.
"""
import os
import httpx
from utils.coingecko import cg_get, RIZE_ID

RIZE_TOKEN = "0x9818B6c09f5ECc843060927E8587c427C7C93583"
DECIMALS = 10 ** 18

KRAKEN_WALLETS = [
    ("Kraken Cold Wallet (0x7DA)", "0x7DAFbA1d69F6C01AE7567Ffd7b046Ca03B706f83"),
    ("Kraken Cold Wallet (0xd2D)", "0xd2DD7b597Fd2435b6dB61ddf48544fd931e6869F"),
    ("Kraken Hot Wallet (0xcC2)",  "0xcC282E2004428939ee5149A9e7872F0B4d5d5ec7"),
    ("Kraken Hot Wallet (0x02A)",  "0x02Ac4617Fe004cf8Cd9c988Ff9C905b2Ec676C2d"),
    ("Kraken Deposit (0x504)",     "0x504Ce9e51E508C85a161058C12e970a903d482fc"),
]
FIXED_PRICES = [0.001, 0.002, 0.003, 0.004, 0.005]

MARKER = "RIZE left on Kraken"          # first line of the answer: telegram.py recognises a reply to it by this text

_alchemy = os.environ.get("ALCHEMY_KEY", "")
RPC_URLS = ([f"https://base-mainnet.g.alchemy.com/v2/{_alchemy}"] if _alchemy else []) + ["https://mainnet.base.org"]


def _balance_of_data(addr: str) -> str:
    return "0x70a08231" + "0" * 24 + addr[2:].lower()


async def _read_balances(post=None) -> dict:
    """{address: RIZE as float, or None when that wallet could not be read}. One batched JSON-RPC request."""
    batch = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
              "params": [{"to": RIZE_TOKEN, "data": _balance_of_data(a)}, "latest"]} for i, (_, a) in enumerate(KRAKEN_WALLETS)]
    for url in RPC_URLS:
        try:
            if post:
                answer = await post(url, batch)
            else:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(url, json=batch)
                    answer = r.json()
            by_id = {x.get("id"): x for x in answer} if isinstance(answer, list) else {}
            out = {}
            for i, (_, a) in enumerate(KRAKEN_WALLETS):
                res = (by_id.get(i) or {}).get("result")
                out[a] = int(res, 16) / DECIMALS if isinstance(res, str) and res.startswith("0x") and len(res) > 2 else None
            if any(v is not None for v in out.values()):
                return out
        except Exception:
            continue
    return {a: None for _, a in KRAKEN_WALLETS}


async def _rize_price(get=None):
    try:
        data = await (get or cg_get)("/simple/price", {"ids": RIZE_ID, "vs_currencies": "usd"})
        p = (data or {}).get(RIZE_ID, {}).get("usd")
        return float(p) if p else None
    except Exception:
        return None


def _rize(v: float) -> str:
    return f"{v / 1e6:.2f}M" if v >= 1e6 else (f"{v / 1e3:.2f}K" if v >= 1e3 else f"{v:.2f}")


def _usd(v: float) -> str:
    return f"${v / 1e6:.2f}M" if v >= 1e6 else (f"${v / 1e3:.1f}k" if v >= 1e3 else f"${v:,.0f}")


def _short(a: str) -> str:
    return a[:6] + "…" + a[-4:]


async def cmd_howbigarewefucked(post=None, get=None) -> str:
    bal = await _read_balances(post)
    read = [v for v in bal.values() if v is not None]
    if not read:
        return "Could not read the Kraken balances right now (Base RPC). Try again in a minute."
    total = sum(read)
    price = await _rize_price(get)
    lines = [f"{MARKER} : {_rize(total)} RIZE",
             f"Kraken wallets covered : {len(read)} wallet{'s' if len(read) != 1 else ''}"
             + ("" if len(read) == len(KRAKEN_WALLETS) else f" (of {len(KRAKEN_WALLETS)}, some could not be read)"),
             ""]
    lines.append(f"Value at the current RIZE price @${price:.5f} : {_usd(total * price)}" if price
                 else "Value at the current RIZE price : price unavailable right now")
    lines.append("")
    for p in FIXED_PRICES:
        lines.append(f"Value at ${p:.3f} : {_usd(total * p)}")
    lines += ["", 'Answer "details" to see wallets balance']
    return "\n".join(lines)


async def cmd_howbigarewefucked_details(post=None) -> str:
    bal = await _read_balances(post)
    lines = ["Kraken wallets, RIZE balance on Base (live)", ""]
    total = 0.0
    for name, a in KRAKEN_WALLETS:
        v = bal.get(a)
        lines.append(f"{name} `{_short(a)}` : " + (f"{_rize(v)} RIZE" if v is not None else "could not be read"))
        total += v or 0
    lines += ["", f"Total : {_rize(total)} RIZE"]
    return "\n".join(lines)
