"""
scrape_fa_locking.py
Fetches Featured App locking data from Lighthouse API and updates two JSON files:
  - rize-data-hub/fa-locking.json        (full snapshot)
  - rize-data-hub/locking-history.json   (daily timeseries, fa side)
"""

import json
import os
import urllib.request
from datetime import datetime, timezone

FA_API_URL = "https://lighthouse.xyz/api/featured-app-locking"
CG_CC_URL      = "https://api.coingecko.com/api/v3/coins/canton-network"
CG_CC_HIST_URL = "https://api.coingecko.com/api/v3/coins/canton-network/market_chart"
CG_KEY         = os.environ.get("COINGECKO_API_KEY", "")
DATA_DIR = "rize-data-hub"
FA_SNAPSHOT_PATH = os.path.join(DATA_DIR, "fa-locking.json")
HISTORY_PATH = os.path.join(DATA_DIR, "locking-history.json")

# Statuses considered "active" for total lock computation
ACTIVE_STATUSES = {"3-Approved"}


def fetch_json(url: str, headers: dict = None) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "tokerize-scraper/1.0",
            **(headers or {}),
        }
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_cc_market() -> tuple[float | None, float | None]:
    """Fetch CC circulating supply and current price from CoinGecko.
    Returns (supply, price).
    """
    try:
        headers = {}
        if CG_KEY:
            headers["x-cg-demo-api-key"] = CG_KEY
        data = fetch_json(CG_CC_URL, headers)
        md     = data.get("market_data", {})
        supply = md.get("circulating_supply")
        price  = md.get("current_price", {}).get("usd")
        supply = float(supply) if supply else None
        price  = float(price)  if price  else None
        print(f"CC supply: {supply:,.0f} | price: ${price}" if supply and price else "Warning: partial CoinGecko data")
        return supply, price
    except Exception as e:
        print(f"Warning: CoinGecko fetch failed — {e}")
        return None, None


def fetch_cc_price_history(days: int = 8) -> dict[str, float]:
    """Fetch daily close prices for the last N days from CoinGecko.
    Returns {date_str: close_price} e.g. {"2026-08-15": 0.0991}.
    """
    try:
        headers = {"x-cg-demo-api-key": CG_KEY} if CG_KEY else {}
        params  = f"?vs_currency=usd&days={days}&interval=daily"
        data    = fetch_json(CG_CC_HIST_URL + params, headers)
        prices  = data.get("prices", [])
        result  = {}
        for ts_ms, price in prices:
            from datetime import datetime, timezone
            date_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            result[date_str] = float(price)
        print(f"CC price history: {len(result)} days fetched")
        return result
    except Exception as e:
        print(f"Warning: CoinGecko price history fetch failed — {e}")
        return {}


def parse_float(val) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def map_status(source_status: str) -> str:
    """Map raw Lighthouse status to display label."""
    s = (source_status or "").lower()
    if "paused" in s or "revoked" in s:
        return "Pause / Revoke"
    if s == "3-approved":
        return "Approved"
    return source_status or "Unknown"


def resolve_role(app: dict) -> str:
    if app.get("asset_issuer"):
        return "Asset Issuer"
    return "App Provider"


def resolve_compliance(app: dict) -> str:
    """Return meets_requirement label."""
    if app.get("source_status", "") not in ACTIVE_STATUSES:
        return map_status(app.get("source_status", ""))
    req = parse_float(app.get("required_lock", 0))
    bal = parse_float(app.get("locking_wallet_total_balance", 0))
    dq = app.get("data_quality", [])
    shortfall = parse_float(app.get("effective_shortfall", 0))

    if dq:
        return "Data Review"
    if app.get("meets_requirement"):
        return "Meets Requirement"
    if shortfall > 0:
        return "Below Requirement"
    return "Below Requirement"


def build_fa_rows(data: dict) -> list:
    rows = []
    for app in data.get("apps", []):
        locked = parse_float(app.get("locking_wallet_total_balance", 0))
        required = parse_float(app.get("required_lock", 0))
        shortfall = parse_float(app.get("effective_shortfall", 0))
        source_status = app.get("source_status", "")

        row = {
            "app_name": app.get("app_name", ""),
            "institution": app.get("institution", ""),
            "role": resolve_role(app),
            "asset_issuer": bool(app.get("asset_issuer")),
            "source_status": source_status,
            "status_label": resolve_compliance(app),
            "meets_requirement": bool(app.get("meets_requirement")),
            "locked_balance": locked,
            "required_lock": required,
            "shortfall": shortfall,
            "data_quality": app.get("data_quality", []),
            "is_active": source_status in ACTIVE_STATUSES,
        }
        rows.append(row)
    return rows


def compute_fa_total(data: dict) -> float:
    """
    Sum locking_wallet_total_balance for 3-Approved apps only.
    Note: some wallets are shared across apps (e.g. PixelPlex 3 apps share 1 wallet).
    We use locking_wallet_total_balance as displayed on Lighthouse (app-level view).
    The summary.unique_wallet_total is available in the snapshot for reference.
    """
    total = 0.0
    for app in data.get("apps", []):
        if app.get("source_status") not in ACTIVE_STATUSES:
            continue
        total += parse_float(app.get("locking_wallet_total_balance", 0))
    return total


def load_history() -> list:
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            return json.load(f)
    return []


def save_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"Saved {path}")


def main():
    print(f"Fetching FA locking from {FA_API_URL}...")
    data = fetch_json(FA_API_URL)

    # ── Build snapshot ────────────────────────────────────────────────────────
    rows = build_fa_rows(data)
    total_fa_locked = compute_fa_total(data)

    # Summary block from API for reference
    summary = data.get("summary", {})
    by_type = {bt["type_key"]: bt for bt in summary.get("by_type", [])}

    snapshot = {
        "updated_at": data.get("updated_at") or datetime.now(timezone.utc).isoformat(),
        "last_closed_round": data.get("last_closed_round"),
        "policy": data.get("policy", {}),
        "summary": {
            "total_apps": summary.get("total_apps"),
            "meets_requirement_count": summary.get("meets_requirement_count"),
            "shortfall_count": summary.get("shortfall_count"),
            "data_issue_count": summary.get("data_issue_count"),
            # App Providers
            "app_provider_count": by_type.get("app_provider", {}).get("app_count"),
            "app_provider_locked": parse_float(by_type.get("app_provider", {}).get("wallet_total_app_level")),
            "app_provider_required": parse_float(by_type.get("app_provider", {}).get("required_total")),
            "app_provider_shortfall": parse_float(by_type.get("app_provider", {}).get("shortfall_total")),
            # Asset Issuers
            "asset_issuer_count": by_type.get("asset_issuer", {}).get("app_count"),
            "asset_issuer_locked": parse_float(by_type.get("asset_issuer", {}).get("wallet_total_app_level")),
            "asset_issuer_required": parse_float(by_type.get("asset_issuer", {}).get("required_total")),
            "asset_issuer_shortfall": parse_float(by_type.get("asset_issuer", {}).get("shortfall_total")),
            # Totals
            "total_locked_app_level": total_fa_locked,
            "unique_wallet_total": parse_float(summary.get("unique_wallet_total")),
            "required_total": parse_float(summary.get("required_total")),
            "shortfall_total": parse_float(summary.get("shortfall_total")),
        },
        "apps": rows,
    }
    save_json(FA_SNAPSHOT_PATH, snapshot)

    # ── Fetch CC market data from CoinGecko ──────────────────────────────────
    cc_supply, cc_price = fetch_cc_market()

    # ── Fetch CC price history to correct last 8 days ────────────────────────
    price_history = fetch_cc_price_history(days=8)

    # ── Update history ────────────────────────────────────────────────────────
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = load_history()

    # Correct prices for last 8 days using CoinGecko historical close prices
    for entry in history:
        date = entry.get("date", "")
        if date in price_history and date != today:
            hist_price = price_history[date]
            entry["cc_price"]  = round(hist_price, 8)
            sv = entry.get("sv", 0) or 0
            fa = entry.get("fa", 0) or 0
            entry["usd_sv"]    = round(sv * hist_price)
            entry["usd_fa"]    = round(fa * hist_price)
            entry["usd_total"] = round((sv + fa) * hist_price)

    existing = next((e for e in history if e.get("date") == today), None)
    sv_locked    = existing.get("sv", 0) if existing else 0
    total_locked = sv_locked + total_fa_locked

    # Compute % of circulating supply locked
    pct_sv    = round(sv_locked       / cc_supply * 100, 4) if cc_supply else None
    pct_fa    = round(total_fa_locked  / cc_supply * 100, 4) if cc_supply else None
    pct_total = round(total_locked     / cc_supply * 100, 4) if cc_supply else None

    # Compute USD values using today snapshot price
    usd_sv    = round(sv_locked       * cc_price) if cc_price else None
    usd_fa    = round(total_fa_locked  * cc_price) if cc_price else None
    usd_total = round(total_locked     * cc_price) if cc_price else None

    if existing:
        existing["fa"]        = round(total_fa_locked)
        existing["total"]     = round(total_locked)
        existing["supply"]    = round(cc_supply)    if cc_supply else existing.get("supply")
        existing["cc_price"]  = round(cc_price, 8)  if cc_price  else existing.get("cc_price")
        existing["pct_sv"]    = pct_sv
        existing["pct_fa"]    = pct_fa
        existing["pct_total"] = pct_total
        existing["usd_sv"]    = usd_sv
        existing["usd_fa"]    = usd_fa
        existing["usd_total"] = usd_total
    else:
        history.append({
            "date":      today,
            "sv":        0,
            "fa":        round(total_fa_locked),
            "total":     round(total_fa_locked),
            "supply":    round(cc_supply)   if cc_supply else None,
            "cc_price":  round(cc_price, 8) if cc_price  else None,
            "pct_sv":    pct_sv,
            "pct_fa":    pct_fa,
            "pct_total": pct_total,
            "usd_sv":    usd_sv,
            "usd_fa":    usd_fa,
            "usd_total": usd_total,
        })

    history.sort(key=lambda e: e["date"])
    history = history[-730:]
    save_json(HISTORY_PATH, history)

    print(f"Total FA locked: {total_fa_locked:,.0f} CC")
    if cc_price:
        print(f"USD locked — SV: ${usd_sv:,.0f} | FA: ${usd_fa:,.0f} | Total: ${usd_total:,.0f}")
    if cc_supply:
        print(f"% supply — SV: {pct_sv}% | FA: {pct_fa}% | Total: {pct_total}%")
    print(f"History entries: {len(history)}")


if __name__ == "__main__":
    main()
