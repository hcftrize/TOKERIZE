"""
Load JSON files from the TOKERIZE GitHub repo.
Exact URLs from rize-governance-hub.html:
  RAW = https://raw.githubusercontent.com/hcftrize/TOKERIZE/main/rize-governance-hub
"""
import httpx
import time

# Base URLs exactly as in the governance hub
RAW_GOV  = "https://raw.githubusercontent.com/hcftrize/TOKERIZE/main/rize-governance-hub"
RAW_DEV  = "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev"
RAW_MAIN = "https://raw.githubusercontent.com/hcftrize/TOKERIZE/main"

URLS = {
    "bondBroken":    f"{RAW_GOV}/bond-broken.json",
    "bondCreated":   f"{RAW_GOV}/bond-created.json",
    "bondLifecycle": f"{RAW_GOV}/bond-lifecycle.json",
    "bondTimemarker":f"{RAW_GOV}/bond-timemarker.json",
    "bondStates":    f"{RAW_GOV}/bond-states.json",
    "poolConfig":    f"{RAW_GOV}/pool-config.json",
    "mcapHistory":   f"{RAW_MAIN}/rize-data-hub/mcap-history.json",
    "volumeHistory": f"{RAW_MAIN}/rize-data-hub/volume-history.json",
    "unbondingQueue":f"{RAW_MAIN}/rize-data-hub/unbonding-queue.json",
    "cips":          f"{RAW_MAIN}/canton-ecosystem/cips.json",
    "entities":      f"{RAW_DEV}/canton-ecosystem/entities.json",
    "cantonnews":    f"{RAW_MAIN}/canton-ecosystem/news.json",
    "arcEntities":   f"{RAW_DEV}/arc-ecosystem/arcentities.json",
}

# ── Simple TTL cache ──────────────────────────────────────────────────────────
_cache: dict = {}   # key → {"data": ..., "ts": float}
CACHE_TTL = 300     # 5 minutes


def _is_fresh(key: str) -> bool:
    entry = _cache.get(key)
    return entry is not None and (time.time() - entry["ts"]) < CACHE_TTL


async def load_json(key: str) -> dict | list | None:
    if _is_fresh(key):
        return _cache[key]["data"]

    url = URLS.get(key, key)  # fallback: key is a full URL
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            r.raise_for_status()
            raw = r.json()
            # Governance hub uses: j.data || j
            data = raw.get("data", raw) if isinstance(raw, dict) and "data" in raw else raw
            _cache[key] = {"data": data, "ts": time.time()}
            return data
    except Exception as e:
        return None


# Convenience functions
async def get_bond_broken():    return await load_json("bondBroken")
async def get_bond_created():   return await load_json("bondCreated")
async def get_bond_lifecycle(): return await load_json("bondLifecycle")
async def get_bond_states():    return await load_json("bondStates")
async def get_mcap_history():   return await load_json("mcapHistory")
async def get_volume_history(): return await load_json("volumeHistory")
async def get_unbonding_queue():return await load_json("unbondingQueue")
async def get_cips():           return await load_json("cips")
async def get_entities():       return await load_json("entities")
async def get_cantonnews():     return await load_json("cantonnews")
async def get_arc_entities():   return await load_json("arcEntities")
