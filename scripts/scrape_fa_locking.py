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
CG_CC_URL  = "https://api.coingecko.com/api/v3/coins/canton-network"
CG_KEY     = os.environ.get("COINGECKO_API_KEY", "")
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


def fetch_cc_supply() -> float | None:
    """Fetch CC circulating supply from CoinGecko."""
    try:
        headers = {}
        if CG_KEY:
            headers["x-cg-demo-api-key"] = CG_KEY
        data = fetch_json(CG_CC_URL, headers)
        supply = data.get("market_data", {}).get("circulating_supply")
        if supply:
            print(f"CC circulating supply: {float(supply):,.0f}")
            return float(supply)
        print("Warning: could not parse CC supply from CoinGecko response")
        return None
    except Exception as e:
        print(f"Warning: CoinGecko fetch failed — {e}")
        return None


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

    # ── Fetch CC circulating supply from CoinGecko ───────────────────────────
    cc_supply = fetch_cc_supply()

    # ── Update history ────────────────────────────────────────────────────────
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = load_history()

    existing = next((e for e in history if e.get("date") == today), None)
    sv_locked = existing.get("sv", 0) if existing else 0
    total_locked = sv_locked + total_fa_locked

    # Compute % of circulating supply locked
    pct_sv    = round(sv_locked      / cc_supply * 100, 4) if cc_supply else None
    pct_fa    = round(total_fa_locked / cc_supply * 100, 4) if cc_supply else None
    pct_total = round(total_locked    / cc_supply * 100, 4) if cc_supply else None

    if existing:
        existing["fa"]        = round(total_fa_locked)
        existing["total"]     = round(total_locked)
        existing["supply"]    = round(cc_supply) if cc_supply else existing.get("supply")
        existing["pct_sv"]    = pct_sv
        existing["pct_fa"]    = pct_fa
        existing["pct_total"] = pct_total
    else:
        history.append({
            "date":      today,
            "sv":        0,
            "fa":        round(total_fa_locked),
            "total":     round(total_fa_locked),
            "supply":    round(cc_supply) if cc_supply else None,
            "pct_sv":    pct_sv,
            "pct_fa":    pct_fa,
            "pct_total": pct_total,
        })

    history.sort(key=lambda e: e["date"])
    history = history[-730:]
    save_json(HISTORY_PATH, history)

    print(f"Total FA locked (app-level, Approved only): {total_fa_locked:,.0f} CC")
    if cc_supply:
        print(f"CC supply: {cc_supply:,.0f} | Locked %: SV={pct_sv}% FA={pct_fa}% Total={pct_total}%")
    print(f"History entries: {len(history)}")


if __name__ == "__main__":
    main()
