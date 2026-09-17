#!/usr/bin/env python3
"""
scrape_lighthouse.py  —  Tokerize
Fetches T-RIZE canton revenue from lighthouse.fivenorth.io and writes
rize-data-hub/canton-revenue.json in the same raw format as the API.

Tracks 3 party IDs: VALIDATOR_ID (original featured-app provider, still
active — never drop it or its history), RIZESCORE_ID, and REGISTRAR_ID
(new featured-app provider since the 2026-09 governance vote; 0 rewards
so far but tracked from now on).

Usage:
  python scrape_lighthouse.py             # incremental (last 14 days)
  python scrape_lighthouse.py --bootstrap # full history from genesis
                                           # (rewrites every entry for all
                                           # 3 IDs from fresh API data —
                                           # this is how to correct any
                                           # historically-misattributed
                                           # entries, no separate diff/
                                           # verify logic needed)
"""

import json
import os
import sys
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
API_BASE     = "https://lighthouse.fivenorth.io/api/parties"
RZ_GENESIS   = "2024-07-31T00:00:00.000Z"
# Original T-RIZE featured-app party ID. Still exists and still shows real
# transactions as of 2026-09-17 — per explicit instruction, NEVER stop
# scraping this one and NEVER drop its historical entries, even though
# governance moved the featured-app status elsewhere (see REGISTRAR_ID below).
VALIDATOR_ID = "TRIZEGroup-cantonMainnetValidator-1::12206ab3bf15b14410220357d6a6375eb1015f2e7fade1deb449463c2f2a25304889"
RIZESCORE_ID = "TRIZEGroup-RIZEScore::12206ab3bf15b14410220357d6a6375eb1015f2e7fade1deb449463c2f2a25304889"
# New featured-app provider party ID for T-RIZE, per Canton governance vote
# (unfeatured TRIZEGroup-cantonMainnetValidator-1, featured this one in its
# place, both effective same date-time —
# https://lists.sync.global/g/tokenomics-announce/message/222).
# 0 rewards as of 2026-09-17; tracked here ALONGSIDE, not instead of,
# VALIDATOR_ID.
REGISTRAR_ID = "t-rize-registrar::1220175c60c87ed5e1c9c1c619da159bde8e1c2e407a4d334e755c167488d3964946"
OUTPUT       = Path(__file__).parent.parent / "rize-data-hub" / "canton-revenue.json"
TIMEOUT      = 30
WINDOW_DAYS  = 14  # refetch window in incremental mode

# Lighthouse now requires "Authorization: Bearer <key>" on every request
# (enforcement went live 2026-09-16 — unkeyed calls fail outright).
LIGHTHOUSE_KEY = os.environ.get("LIGHTHOUSE_KEY", "")


def lighthouse_headers() -> dict:
    return {"Authorization": f"Bearer {LIGHTHOUSE_KEY}"} if LIGHTHOUSE_KEY else {}


# ── Helpers ────────────────────────────────────────────────────────────────────
def fetch_json(url: str) -> list:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Tokerize-DataBot/1.0 (+https://tokerize.com)",
        "Accept":     "application/json",
        **lighthouse_headers(),
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            if r.status != 200:
                print(f"  WARNING HTTP {r.status}: {url}", file=sys.stderr)
                return []
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        print(f"  ERROR HTTP {e.code} fetching {url}\n  Response body: {body}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}", file=sys.stderr)
        return []


def build_url(party_id: str, endpoint: str, start: str, end: str) -> str:
    params = urllib.parse.urlencode({"start_time": start, "end_time": end})
    pid    = urllib.parse.quote(party_id, safe="")
    return f"{API_BASE}/{pid}/stats/{endpoint}?{params}"


def merge(existing: list, fresh: list, window_start_iso: str) -> list:
    """
    Keep existing entries strictly before the window, replace everything
    from window_start onwards with the freshly fetched data.
    """
    kept = [r for r in existing if r["time"] < window_start_iso]
    # Deduplicate fresh by time (last wins)
    by_time = {r["time"]: r for r in fresh}
    merged  = kept + sorted(by_time.values(), key=lambda r: r["time"])
    return merged


def load_existing() -> dict:
    if OUTPUT.exists():
        try:
            with open(OUTPUT) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "updatedAt":           "",
        "validator_rewards":   [],
        "score_rewards":       [],
        "validator_burns":     [],
        "score_burns":         [],
        "registrar_rewards":   [],
        "registrar_burns":     [],
    }


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    bootstrap = "--bootstrap" in sys.argv
    now       = datetime.now(timezone.utc)
    now_iso   = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    if bootstrap:
        print("=== BOOTSTRAP — full history from genesis ===")
        start            = RZ_GENESIS
        window_start_iso = RZ_GENESIS   # replace everything
    else:
        print("=== INCREMENTAL — last 14 days ===")
        window_dt        = now - timedelta(days=WINDOW_DAYS)
        start            = window_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        window_start_iso = window_dt.strftime("%Y-%m-%dT00:00:00.000Z")

    # ── Fetch ──────────────────────────────────────────────────────────────────
    print("  Fetching validator rewards …")
    val_rew    = fetch_json(build_url(VALIDATOR_ID, "rewards", start, now_iso))
    print(f"    → {len(val_rew)} entries")

    print("  Fetching RIZEScore rewards …")
    score_rew  = fetch_json(build_url(RIZESCORE_ID, "rewards", start, now_iso))
    print(f"    → {len(score_rew)} entries")

    print("  Fetching validator burns …")
    val_burn   = fetch_json(build_url(VALIDATOR_ID, "burns",   start, now_iso))
    print(f"    → {len(val_burn)} entries")

    print("  Fetching RIZEScore burns …")
    score_burn = fetch_json(build_url(RIZESCORE_ID, "burns",   start, now_iso))
    print(f"    → {len(score_burn)} entries")

    print("  Fetching registrar rewards …")
    registrar_rew  = fetch_json(build_url(REGISTRAR_ID, "rewards", start, now_iso))
    print(f"    → {len(registrar_rew)} entries")

    print("  Fetching registrar burns …")
    registrar_burn = fetch_json(build_url(REGISTRAR_ID, "burns",   start, now_iso))
    print(f"    → {len(registrar_burn)} entries")

    if not any([val_rew, score_rew, val_burn, score_burn, registrar_rew, registrar_burn]):
        print("WARNING: all endpoints returned empty — output unchanged.", file=sys.stderr)
        sys.exit(0)

    # ── Merge with existing ────────────────────────────────────────────────────
    existing = load_existing()

    payload = {
        "updatedAt":           now_iso,
        "validator_rewards":   merge(existing.get("validator_rewards", []), val_rew,         window_start_iso),
        "score_rewards":       merge(existing.get("score_rewards",     []), score_rew,       window_start_iso),
        "validator_burns":     merge(existing.get("validator_burns",   []), val_burn,        window_start_iso),
        "score_burns":         merge(existing.get("score_burns",       []), score_burn,      window_start_iso),
        "registrar_rewards":   merge(existing.get("registrar_rewards", []), registrar_rew,   window_start_iso),
        "registrar_burns":     merge(existing.get("registrar_burns",   []), registrar_burn,  window_start_iso),
    }

    # ── Write ──────────────────────────────────────────────────────────────────
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"  ✓ validator_rewards : {len(payload['validator_rewards'])} entries")
    print(f"  ✓ score_rewards     : {len(payload['score_rewards'])} entries")
    print(f"  ✓ validator_burns   : {len(payload['validator_burns'])} entries")
    print(f"  ✓ score_burns       : {len(payload['score_burns'])} entries")
    print(f"  ✓ registrar_rewards : {len(payload['registrar_rewards'])} entries")
    print(f"  ✓ registrar_burns   : {len(payload['registrar_burns'])} entries")
    print(f"  → {OUTPUT}")


if __name__ == "__main__":
    main()
