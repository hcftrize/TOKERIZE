"""
scrape_sv_locking.py
Fetches SV locking data from Lighthouse API and updates two JSON files:
  - rize-data-hub/sv-locking.json        (full snapshot)
  - rize-data-hub/locking-history.json   (daily timeseries, sv side)
"""

import json
import os
import urllib.request
from datetime import datetime, timezone

SV_API_URL = "https://lighthouse.xyz/api/sv-locking"
DATA_DIR = "rize-data-hub"
SV_SNAPSHOT_PATH = os.path.join(DATA_DIR, "sv-locking.json")
HISTORY_PATH = os.path.join(DATA_DIR, "locking-history.json")


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "tokerize-scraper/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def parse_float(val) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def classify_sv(sv: dict) -> str:
    """Return display category for a SV entry."""
    scope = sv.get("scope_type", "")
    if sv.get("is_aggregate"):
        return "org_aggregate"
    if scope == "Escrow Exempt":
        return "escrow_exempt"
    if sv.get("pending_reason") == "ghost":
        return "ghost"
    if scope == "Standalone":
        return "standalone_sub" if sv.get("organization") else "standalone"
    if scope == "Hosted":
        return "hosted_sub" if sv.get("organization") else "hosted"
    return "other"

# Canton PartyId nodes that appear as SVs in the API but are custodian wallets,
# not independent Super Validators — excluded from snapshot and totals.
EXCLUDED_SV_NAMES = {
    "23d169c2-0909-4c70-81d1-1922de6febaa",
}


def compute_sv_total(data: dict) -> float:
    """
    Sum locked_balance for countable SVs only.

    Counted:
      - Org Aggregates (is_aggregate=True) — canonical number for multi-node orgs
      - Standalone SVs with no organization — independent operators
      - Hosted SVs with no organization — independent hosted nodes

    NOT counted:
      - standalone_sub / hosted_sub belonging to an org → already in org_aggregate
      - Escrow Exempt → they appear as BOTH Escrow Exempt AND Hosted entries
        sharing the same locking wallet; the Hosted entry is already counted above.
        Counting Escrow Exempt too would double-count (e.g. Ubyx, Zenith).
      - Ghost (pending_reason=ghost) → locked_balance is always 0 anyway
    """
    total = 0.0
    for sv in data.get("svs", []):
        if sv.get("name") in EXCLUDED_SV_NAMES:
            continue
        cat = classify_sv(sv)
        locked = parse_float(sv.get("locked_balance", 0))
        if cat in ("org_aggregate", "standalone", "hosted"):
            total += locked
    return total


def build_sv_rows(data: dict) -> list:
    """
    Build cleaned rows for the snapshot JSON.
    - One row per Org Aggregate (replaces sub-SVs)
    - One row per independent Standalone / Hosted
    - One row per Escrow Exempt (for the table only, shown with badge)
    - Skips standalone_sub / hosted_sub (already in their org aggregate)
    """
    rows = []
    for sv in data.get("svs", []):
        if sv.get("name") in EXCLUDED_SV_NAMES:
            continue
        cat = classify_sv(sv)
        # Skip sub-SVs belonging to an org — represented by their aggregate
        if cat in ("standalone_sub", "hosted_sub"):
            continue

        name = sv.get("display_name") or sv.get("name") or sv.get("sv_id", "")
        locked = parse_float(sv.get("locked_balance", 0))
        required = parse_float(sv.get("required_lock", 0))
        shortfall = parse_float(sv.get("shortfall", 0))

        row = {
            "name": name,
            "scope_type": sv.get("scope_type", ""),
            "category": cat,
            "tier": sv.get("tier_label"),
            "tier_num": sv.get("tier"),
            "locked_balance": locked,
            "required_lock": required,
            "shortfall": shortfall,
            "compliance_pct": sv.get("compliance_pct"),
            "breaches_35d": sv.get("breaches_35d"),
            "is_ghost": sv.get("pending_reason") == "ghost",
            "is_aggregate": bool(sv.get("is_aggregate")),
            "total_weight": sv.get("total_weight"),
        }
        rows.append(row)

    return rows


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
    print(f"Fetching SV locking from {SV_API_URL}...")
    data = fetch_json(SV_API_URL)

    rows = build_sv_rows(data)
    total_sv_locked = compute_sv_total(data)

    snapshot = {
        "updated_at": data.get("updated_at") or datetime.now(timezone.utc).isoformat(),
        "last_closed_round": data.get("last_closed_round"),
        "total_svs": data.get("total_svs"),
        "tier1_count": data.get("tier1_count"),
        "tier2_count": data.get("tier2_count"),
        "no_tier_count": data.get("no_tier_count"),
        "pending_count": data.get("pending_count"),
        "total_sv_locked": total_sv_locked,
        "svs": rows,
    }
    save_json(SV_SNAPSHOT_PATH, snapshot)

    # ── Update history ────────────────────────────────────────────────────────
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = load_history()

    existing = next((e for e in history if e.get("date") == today), None)
    if existing:
        existing["sv"] = round(total_sv_locked)
        existing["total"] = round(existing.get("fa", 0) + total_sv_locked)
    else:
        history.append({
            "date": today,
            "sv": round(total_sv_locked),
            "fa": 0,
            "total": round(total_sv_locked),
        })

    history.sort(key=lambda e: e["date"])
    history = history[-730:]
    save_json(HISTORY_PATH, history)

    print(f"Total SV locked: {total_sv_locked:,.0f} CC ({total_sv_locked/1e9:.2f}B)")
    print(f"History entries: {len(history)}")


if __name__ == "__main__":
    main()
