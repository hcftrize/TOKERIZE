"""
Commands: /govflows, /govwhalealert, /govwallet, /govbond
Exact logic from rize-governance-hub.html expSearch / expRenderWallet / expRenderBond.

bond-states.json structure:
{
  "generated_at": "...",
  "fullMaturity": 94608000,
  "stats": {"totalBonds":..., "activeBonds":..., "totalRIZE":..., "totalVP":...},
  "ownerIndex": {"0xabc": ["1","2"], ...},
  "bondStates": {"1": {"owner":"0x...", "poolId":2, "current":{...}, "events":[...]}}
}

current fields: balance, timeMarker, maturity, boost, vp, fullMatDate, vpAtFullMat, isActive
events fields: ts, date, type, delta, balance, maturity, boost, vp, txHash
"""
import time as _time
from utils.github_data import (
    get_bond_created, get_bond_broken, get_bond_lifecycle,
    get_bond_states, get_unbonding_queue
)
from utils.formatters import fmt_rize, fmt_num, fmt_pct

FULL_MATURITY_S = 94608000  # 3 years in seconds
TOTAL_SUPPLY    = 5_000_000_000
WHALE_MIN       = 5_000_000


def parse_amt(v) -> float:
    try:
        return float(str(v).replace(",", "")) if v else 0.0
    except Exception:
        return 0.0


def ts_to_date(ts) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return "—"


def short_addr(a: str) -> str:
    if not a or len(a) < 10: return a or "—"
    return f"{a[:6]}…{a[-4:]}"


def _load_bs_parts(bs_raw: dict):
    """Extract bondStates dict and ownerIndex from bond-states.json."""
    if not bs_raw:
        return None, {}, 94608000
    bond_states = bs_raw.get("bondStates", bs_raw)
    owner_index = bs_raw.get("ownerIndex", {})
    full_mat    = bs_raw.get("fullMaturity", FULL_MATURITY_S)
    return bond_states, owner_index, full_mat


def _get_wallet_nfts(addr: str, owner_index: dict, bond_states: dict) -> list:
    """Get all nftIds for a wallet using ownerIndex first, then scan."""
    addr_lower = addr.lower()
    # Use ownerIndex (fast path)
    nft_ids = list(owner_index.get(addr_lower, []))
    if not nft_ids:
        # Fallback: scan all bonds
        nft_ids = [nid for nid, s in bond_states.items()
                   if isinstance(s, dict) and (s.get("owner") or "").lower() == addr_lower]
    return [str(n) for n in nft_ids]


def _compute_loyalty(bond_rows: list, breaks: list, increases: list, releases: list,
                     bond_states: dict, nft_ids: list) -> float:
    """Loyalty score 0-10 — exact logic from expRenderWallet."""
    if not bond_rows:
        return 0.0

    # Peak bonded — track running balance
    all_events = []
    for nid in nft_ids:
        s = bond_states.get(str(nid), {})
        for ev in (s.get("events") or []):
            all_events.append({**ev, "_nid": str(nid)})
    for ev in releases:
        all_events.append({"ts": int(ev.get("timestamp", 0)),
                           "type": "Release",
                           "delta": -parse_amt(ev.get("amount", 0)),
                           "_nid": str(ev.get("nftId", ""))})
    all_events.sort(key=lambda e: e.get("ts", 0))

    bal_by_nft = {}
    peak_bal = 0.0
    for ev in all_events:
        nid = ev.get("_nid", "")
        bal_by_nft[nid] = bal_by_nft.get(nid, 0.0) + (ev.get("delta") or 0.0)
        run_bal = sum(max(0, v) for v in bal_by_nft.values())
        if run_bal > peak_bal:
            peak_bal = run_bal

    total_released = sum(parse_amt(e.get("amount", 0)) for e in releases)
    release_pct    = total_released / peak_bal if peak_bal > 0 else 0
    penalty        = min(5.0, release_pct * 5)

    # Fidelity bonus — oldest bond age
    oldest_tm = min((int(b.get("tm") or _time.time()) for b in bond_rows), default=_time.time())
    oldest_months = (_time.time() - oldest_tm) / 2592000
    bonus_fidelity = min(2.0, oldest_months / 18)

    # IncreaseBond bonus
    total_increased    = sum(parse_amt(e.get("amount", 0)) for e in increases)
    total_ever_bonded  = sum(b.get("amount", 0) for b in bond_rows) + total_released
    inc_pct            = total_increased / total_ever_bonded if total_ever_bonded > 0 else 0
    bonus_increase     = min(2.0, inc_pct * 4)

    # Significant bonds bonus
    sig_bonds = sum(1 for b in bond_rows if peak_bal > 0 and b.get("amount", 0) > peak_bal * 0.01)
    bonus_bonds = min(1.0, max(0.0, (sig_bonds - 1) * 0.5))

    return max(0.0, min(10.0, 5 - penalty + bonus_fidelity + bonus_increase + bonus_bonds))


# ── /govflows ─────────────────────────────────────────────────────────────────

async def cmd_govflows(args: list, page: int = 0) -> str:
    bb = await get_bond_broken()
    bc = await get_bond_created()
    if not bb:
        return "Could not load governance flow data."

    monthly: dict = {}

    def ensure(ym):
        if ym not in monthly:
            monthly[ym] = {"breaks": 0, "breaks_rize": 0.0,
                           "created": 0, "created_rize": 0.0,
                           "increased": 0, "increased_rize": 0.0}

    bb_data = bb if isinstance(bb, dict) else {}
    bc_data = bc if isinstance(bc, dict) else {}

    for e in bb_data.get("bondBrokenEvents", []):
        d  = e.get("date") or ts_to_date(e.get("timestamp", 0))
        ym = d[:7]
        ensure(ym)
        monthly[ym]["breaks"] += 1
        monthly[ym]["breaks_rize"] += parse_amt(e.get("amount", 0))

    for e in bc_data.get("bondCreatedEvents", []):
        d  = e.get("date") or ts_to_date(e.get("timestamp", 0))
        ym = d[:7]
        ensure(ym)
        monthly[ym]["created"] += 1
        monthly[ym]["created_rize"] += parse_amt(e.get("amount", 0))

    for e in bc_data.get("increaseBondEvents", []):
        d  = e.get("date") or ts_to_date(e.get("timestamp", 0))
        ym = d[:7]
        ensure(ym)
        monthly[ym]["increased"] += 1
        monthly[ym]["increased_rize"] += parse_amt(e.get("amount", 0))

    sorted_months = sorted(monthly.keys(), reverse=True)
    per_page = 6
    start    = page * per_page
    page_months = sorted_months[start:start + per_page]

    if not page_months:
        return "No more months to display."

    total_pages = (len(sorted_months) - 1) // per_page + 1
    lines = [f"📊 *Governance Flows* — Page {page+1}/{total_pages}", ""]

    for ym in page_months:
        m = monthly[ym]
        net = m["created_rize"] + m["increased_rize"] - m["breaks_rize"]
        sign = "+" if net >= 0 else ""
        lines += [
            f"*{ym}*",
            f"  Breaks: {m['breaks']} txs · {fmt_rize(m['breaks_rize'])}",
            f"  Created: {m['created']} txs · {fmt_rize(m['created_rize'])}",
            f"  Increased: {m['increased']} txs · {fmt_rize(m['increased_rize'])}",
            f"  Net: {sign}{fmt_rize(net)}",
            "",
        ]

    if start + per_page < len(sorted_months):
        lines.append("_Reply *next* to see earlier months._")

    return "\n".join(lines)


# ── /govwhalealert ────────────────────────────────────────────────────────────

async def cmd_govwhalealert(args: list, page: int = 0) -> str:
    dir_filter = "all"
    whale_min  = WHALE_MIN
    from utils.formatters import parse_amount
    for a in args:
        al = a.lower()
        if al in ("breaks", "break"):               dir_filter = "break"
        elif al in ("bond", "bonds"):               dir_filter = "bond"
        elif al in ("increase", "increases"):       dir_filter = "increase"
        elif al in ("bond+increase", "bondinc"):    dir_filter = "bond+increase"
        elif al in ("releases", "release"):         dir_filter = "release"
        else:
            # Try to parse as custom threshold e.g. "1M", "5m", "10000000"
            parsed = parse_amount(al)
            if parsed and parsed >= 100_000:
                whale_min = parsed

    bb = await get_bond_broken()
    bc = await get_bond_created()
    lc = await get_bond_lifecycle()
    bs = await get_bond_states()
    if not bb:
        return "Could not load whale alert data."

    bb_data = bb if isinstance(bb, dict) else {}
    bc_data = bc if isinstance(bc, dict) else {}
    lc_data = lc if isinstance(lc, dict) else {}
    # bond_states used to resolve missing owner on breaks
    bond_states_data, _, _ = _load_bs_parts(bs) if bs else (None, {}, FULL_MATURITY_S)

    def resolve_owner(e: dict, nft_key: str = "nftId") -> str:
        """Return owner from event, falling back to bond_states lookup."""
        owner = e.get("owner", "") or ""
        if not owner and bond_states_data:
            nft_id = str(e.get(nft_key, ""))
            owner = (bond_states_data.get(nft_id) or {}).get("owner", "") or ""
        return owner

    all_events = []

    for e in bb_data.get("bondBrokenEvents", []):
        rize = parse_amt(e.get("amount", 0))
        if rize >= whale_min:
            all_events.append({"ts": int(e.get("timestamp", 0)),
                "date": e.get("date") or ts_to_date(e.get("timestamp", 0)),
                "dir": "break", "nft": e.get("nftId", "?"),
                "rize": rize, "owner": resolve_owner(e)})

    for e in bc_data.get("bondCreatedEvents", []):
        rize = parse_amt(e.get("amount", 0))
        if rize >= whale_min:
            all_events.append({"ts": int(e.get("timestamp", 0)),
                "date": e.get("date") or ts_to_date(e.get("timestamp", 0)),
                "dir": "bond", "nft": e.get("nftId", "?"),
                "rize": rize, "owner": resolve_owner(e)})

    for e in bc_data.get("increaseBondEvents", []):
        rize = parse_amt(e.get("amount", 0))
        if rize >= whale_min:
            all_events.append({"ts": int(e.get("timestamp", 0)),
                "date": e.get("date") or ts_to_date(e.get("timestamp", 0)),
                "dir": "increase", "nft": e.get("nftId", "?"),
                "rize": rize, "owner": resolve_owner(e)})

    for e in lc_data.get("tokensReleasedEvents", []):
        rize = parse_amt(e.get("amount", 0))
        if rize >= whale_min:
            all_events.append({"ts": int(e.get("timestamp", 0)),
                "date": e.get("date") or ts_to_date(e.get("timestamp", 0)),
                "dir": "release", "nft": e.get("nftId", "?"),
                "rize": rize, "owner": (e.get("to") or resolve_owner(e))})

    if dir_filter == "break":
        all_events = [e for e in all_events if e["dir"] == "break"]
    elif dir_filter == "bond":
        all_events = [e for e in all_events if e["dir"] == "bond"]
    elif dir_filter == "increase":
        all_events = [e for e in all_events if e["dir"] == "increase"]
    elif dir_filter == "bond+increase":
        all_events = [e for e in all_events if e["dir"] in ("bond", "increase")]
    elif dir_filter == "release":
        all_events = [e for e in all_events if e["dir"] == "release"]

    all_events.sort(key=lambda e: e["ts"], reverse=True)

    per_page = 5
    start    = page * per_page
    page_ev  = all_events[start:start + per_page]

    if not page_ev:
        return f"No whale events found (>{fmt_rize(whale_min)})."

    total       = len(all_events)
    total_pages = (total - 1) // per_page + 1
    dir_icon    = {"break": "🔴", "bond": "🟢", "increase": "🟡", "release": "🔵"}
    filter_label = {"all": "All", "break": "Breaks", "bond": "Bonds",
                    "increase": "Increases", "bond+increase": "Bonds+Inc",
                    "release": "Releases"}.get(dir_filter, dir_filter)

    lines = [
        f"🐋 *Whale Alert — {filter_label}* (>{fmt_rize(whale_min)})",
        f"_Page {page+1}/{total_pages} · {total} events_",
        "",
    ]

    for e in page_ev:
        icon = dir_icon.get(e["dir"], "⚪")
        owner_full = e['owner']
        owner_disp = short_addr(owner_full)
        lines += [
            f"{icon} *{e['dir'].capitalize()}* — Bond #{e['nft']}",
            f"  {e['date']} · {fmt_rize(e['rize'])}",
            f"  `{owner_full}`",
            "",
        ]

    if start + per_page < total:
        lines.append("_Reply *next* to see more._")

    return "\n".join(lines)


# ── /govbond ──────────────────────────────────────────────────────────────────

async def cmd_govbond(args: list) -> str:
    """Bond explorer — mirrors expRenderBond."""
    if not args:
        return "Provide a bond number.\nExample: `/govbond 1234` or `/govbond #1234`"

    nft_id = args[0].lstrip("#").strip()
    if not nft_id.isdigit():
        return f"Invalid bond number: `{args[0]}`"

    bs_raw = await get_bond_states()
    bc     = await get_bond_created()
    bb     = await get_bond_broken()
    lc     = await get_bond_lifecycle()

    bond_states, owner_index, full_mat = _load_bs_parts(bs_raw)
    if not bond_states:
        return "Could not load bond states data."

    state = bond_states.get(str(nft_id))
    if not state:
        return f"Bond #{nft_id} not found."

    cur   = state.get("current", {})
    owner = (state.get("owner") or "").lower()

    balance     = parse_amt(cur.get("balance", 0))
    mat         = float(cur.get("maturity", 0))
    boost       = float(cur.get("boost", 1))
    vp          = parse_amt(cur.get("vp", 0))
    vp_full_mat = parse_amt(cur.get("vpAtFullMat", 0)) or (balance * 3)
    full_mat_date = cur.get("fullMatDate", "—")
    is_active   = cur.get("isActive", balance > 0)

    # VP rank for owner
    wallet_vp: dict = {}
    for nid, s in bond_states.items():
        if isinstance(s, dict):
            o = (s.get("owner") or "").lower()
            c = s.get("current", {})
            bal = parse_amt(c.get("balance", 0))
            if bal > 0:
                wallet_vp[o] = wallet_vp.get(o, 0.0) + parse_amt(c.get("vp", 0))
    ranked = sorted(wallet_vp.items(), key=lambda x: x[1], reverse=True)
    rank   = next((i+1 for i, (o, _) in enumerate(ranked) if o == owner), None)

    # Timeline from bond-states events (pre-computed)
    events = state.get("events", [])
    # Inject releases from bond-lifecycle (not in bond-states events)
    lc_data = lc if isinstance(lc, dict) else {}
    for e in lc_data.get("tokensReleasedEvents", []):
        if str(e.get("nftId", "")) == str(nft_id):
            events = list(events) + [{
                "ts"   : int(e.get("timestamp", 0)),
                "date" : e.get("date") or ts_to_date(e.get("timestamp", 0)),
                "type" : "Release",
                "delta": -parse_amt(e.get("amount", 0)),
            }]
    timeline = sorted(events, key=lambda e: e.get("ts", 0), reverse=True)

    # First created date
    created_ev = next((e for e in sorted(events, key=lambda e: e.get("ts", 0))
                       if e.get("type") == "BondCreated"), None)
    first_date = created_ev.get("date", "—") if created_ev else "—"

    # All bonds for this wallet
    owner_nfts = _get_wallet_nfts(owner, owner_index, bond_states)
    active_owner_bonds = [n for n in owner_nfts
                          if parse_amt(bond_states.get(str(n), {}).get("current", {}).get("balance", 0)) > 0]

    # Store full owner address for "see wallet" reply
    # Display truncated but full address in backtick is copyable
    lines = [
        f"🔗 *Bond #{nft_id}*",
        f"{'Active' if is_active else 'Inactive'}{' · Rank #' + str(rank) if rank else ''}",
        "",
        f"Owner: `{owner}`",
        f"RIZE Bonded: *{fmt_rize(balance)}*",
        f"Maturity: {mat*100:.2f}%",
        f"Boost: {boost:.3f}×",
        f"Voting Power: {fmt_rize(vp)}",
        f"VP at Full Mat: {fmt_rize(vp_full_mat)}",
        f"Full Mat Date: {full_mat_date}",
        f"First Bond: {first_date}",
        "",
    ]

    if len(active_owner_bonds) > 1:
        other = [f"#{n}" for n in active_owner_bonds if str(n) != str(nft_id)]
        lines.append(f"_Other active bonds: {', '.join(other[:5])}_")
        lines.append("")

    if timeline:
        lines.append("*Recent Activity:*")
        for e in timeline[:5]:
            delta = e.get("delta", 0)
            sign  = "+" if (delta or 0) > 0 else ""
            amt   = abs(parse_amt(delta))
            lines.append(
                f"  {e.get('date','—')}  {e.get('type','—')}"
                f"{'  ' + sign + fmt_rize(amt) if amt else ''}"
            )
        if len(timeline) > 5:
            lines.append("_Reply *next* to see more activity._")

    lines += [
        "",
        f"_Reply *see wallet* to view {short_addr(owner)} full profile._",
    ]

    return "\n".join(lines)


# ── /govwallet ────────────────────────────────────────────────────────────────

async def cmd_govwallet(args: list, page: int = 0) -> str:
    """Wallet explorer — mirrors expRenderWallet exactly."""
    if not args:
        return (
            "Provide a wallet address or bond number.\n\n"
            "Examples:\n"
            "`/govwallet 0x88ab...`\n"
            "`/govbond 10034`"
        )

    query = args[0].strip()

    # Bond number → show bond then offer wallet link
    if query.lstrip("#").isdigit():
        return await cmd_govbond([query])

    addr = query.lower()

    bs_raw = await get_bond_states()
    bc     = await get_bond_created()
    bb     = await get_bond_broken()
    lc     = await get_bond_lifecycle()

    bond_states, owner_index, full_mat = _load_bs_parts(bs_raw)
    if not bond_states:
        return "Could not load bond states data."

    bc_data = bc if isinstance(bc, dict) else {}
    bb_data = bb if isinstance(bb, dict) else {}
    lc_data = lc if isinstance(lc, dict) else {}

    # Get all nftIds for this wallet
    nft_ids = _get_wallet_nfts(addr, owner_index, bond_states)

    # Also check bondCreatedEvents (bonds created after last snapshot)
    all_bond_created = [e for e in bc_data.get("bondCreatedEvents", [])
                        if (e.get("owner") or "").lower() == addr]
    for e in all_bond_created:
        nid = str(e.get("nftId", ""))
        if nid and nid not in nft_ids:
            nft_ids.append(nid)

    if not nft_ids:
        return f"No bonds found for `{short_addr(addr)}`."

    nft_set = set(nft_ids)

    # All wallet events from other JSONs
    increases = [e for e in bc_data.get("increaseBondEvents", [])
                 if str(e.get("nftId", "")) in nft_set]
    breaks    = [e for e in bb_data.get("bondBrokenEvents", [])
                 if str(e.get("nftId", "")) in nft_set]
    releases  = [e for e in lc_data.get("tokensReleasedEvents", [])
                 if str(e.get("nftId", "")) in nft_set]

    # Build bond rows from bond-states (exact, pre-computed)
    bond_rows = []
    total_vp = 0.0
    total_rize = 0.0
    mat_weight = 0.0

    for nft_id in nft_ids:
        s   = bond_states.get(str(nft_id), {})
        cur = s.get("current", {})
        if not cur:
            continue
        bal  = parse_amt(cur.get("balance", 0))
        mat  = float(cur.get("maturity", 0))
        boost = float(cur.get("boost", 1))
        vp   = parse_amt(cur.get("vp", 0))
        tm   = int(cur.get("timeMarker", 0))
        if bal > 0:
            total_vp    += vp
            total_rize  += bal
            mat_weight  += mat * bal
        bond_rows.append({
            "nftId": nft_id,
            "amount": bal,
            "mat": mat,
            "boost": boost,
            "vp": vp,
            "tm": tm,
            "fullMatDate": cur.get("fullMatDate", "—"),
            "vpAtFullMat": parse_amt(cur.get("vpAtFullMat", 0)),
            "isActive": cur.get("isActive", bal > 0),
            "createdAtDate": next((e.get("date","—") for e in (s.get("events") or [])
                                   if e.get("type") == "BondCreated"), "—"),
        })

    active_bonds = [b for b in bond_rows if b["isActive"]]
    avg_mat = (mat_weight / total_rize * 100) if total_rize > 0 else 0.0

    # VP rank
    wallet_vp: dict = {}
    for nid, s in bond_states.items():
        if isinstance(s, dict):
            o   = (s.get("owner") or "").lower()
            cur = s.get("current", {})
            bal = parse_amt(cur.get("balance", 0))
            if bal > 0:
                wallet_vp[o] = wallet_vp.get(o, 0.0) + parse_amt(cur.get("vp", 0))
    total_all_vp = sum(wallet_vp.values())
    ranked = sorted(wallet_vp.items(), key=lambda x: x[1], reverse=True)
    rank   = next((i+1 for i, (o, _) in enumerate(ranked) if o == addr), None)
    vp_pct = (total_vp / total_all_vp * 100) if total_all_vp > 0 else 0

    # First bond date
    first_bond = "—"
    if bond_rows:
        oldest = min(bond_rows, key=lambda b: b.get("tm") or float("inf"))
        first_bond = oldest.get("createdAtDate", "—")

    # Loyalty score
    loyalty = _compute_loyalty(bond_rows, breaks, increases, releases, bond_states, nft_ids)
    has_never_broken = len(breaks) == 0

    # VP projections
    now_ts = _time.time()
    def proj_vp(months):
        total = 0.0
        for b in bond_rows:
            if b["amount"] <= 0: continue
            tm = b["tm"] or int(now_ts)
            future_mat = min(1.0, max(0.0, (now_ts + months * 2592000 - tm) / full_mat))
            total += b["amount"] * (1 + future_mat * 2)
        return total

    vp_6m  = proj_vp(6)
    vp_12m = proj_vp(12)
    vp_full = sum(b["amount"] * 3 for b in bond_rows if b["amount"] > 0)

    # Full maturity date = latest timeMarker + fullMaturity
    if bond_rows:
        latest_tm = max((b["tm"] for b in bond_rows if b["tm"]), default=0)
        from datetime import datetime, timezone
        full_mat_date = datetime.fromtimestamp(
            latest_tm + full_mat, tz=timezone.utc
        ).strftime("%Y-%m-%d") if latest_tm else "—"
    else:
        full_mat_date = "—"

    # Unbonding queue (breaks last 7 days)
    cutoff = now_ts - 7 * 86400
    unb_queue = sum(parse_amt(e.get("amount", 0)) for e in breaks
                    if int(e.get("timestamp", 0)) > cutoff)

    # Unified timeline
    all_events = []
    for nid in nft_ids:
        s = bond_states.get(str(nid), {})
        for ev in (s.get("events") or []):
            all_events.append({**ev, "_nftId": nid})
    # Inject releases from bond-lifecycle (not in bond-states events)
    for e in releases:
        all_events.append({
            "ts"    : int(e.get("timestamp", 0)),
            "date"  : e.get("date") or ts_to_date(e.get("timestamp", 0)),
            "type"  : "Release",
            "delta" : -parse_amt(e.get("amount", 0)),
            "_nftId": str(e.get("nftId", "")),
        })
    all_events.sort(key=lambda e: e.get("ts", 0), reverse=True)

    lines = [
        f"🔗 *Wallet Profile*",
        f"`{addr}`",  # Full address - copyable
        f"{'Rank #' + str(rank) if rank else 'Unranked'}",
        "",
        f"Total VP: *{fmt_rize(total_vp)}* ({vp_pct:.3f}% of total)",
        f"RIZE Bonded: {fmt_rize(total_rize)}",
        f"Active Bonds: {len(active_bonds)}",
        f"Avg Maturity: {avg_mat:.2f}%",
        f"Loyalty Score: {loyalty:.1f}/10{'  (Never broken)' if has_never_broken else ''}",
        f"First Bond: {first_bond}",
    ]

    if unb_queue > 0:
        lines.append(f"Unbonding Queue: {fmt_rize(unb_queue)} (last 7d)")

    lines += [
        "",
        "*VP Projection:*",
        f"  Today: {fmt_rize(total_vp)}",
        f"  In 6 months: {fmt_rize(vp_6m)}",
        f"  In 12 months: {fmt_rize(vp_12m)}",
        f"  At Full Maturity: {fmt_rize(vp_full)}",
        f"  Full Mat Date: {full_mat_date}",
        "",
    ]

    # Active bonds list
    if active_bonds:
        lines.append("*Active Bonds:*")
        for b in sorted(active_bonds, key=lambda x: x["vp"], reverse=True)[:5]:
            lines.append(
                f"  #{b['nftId']} — {fmt_rize(b['amount'])} "
                f"· Mat {b['mat']*100:.2f}% · Boost {b['boost']:.3f}× · VP {fmt_rize(b['vp'])}"
            )
        if len(active_bonds) > 5:
            lines.append(f"  _…and {len(active_bonds)-5} more_")
        lines.append("")

    all_sorted = sorted(all_events, key=lambda e: e.get("ts", 0), reverse=True)

    if page == 0:
        # First page: full wallet profile + first 5 timeline entries
        if all_sorted:
            lines.append("*Full Activity Timeline:*")
            for e in all_sorted[:5]:
                delta = e.get("delta", 0) or 0
                amt   = abs(parse_amt(delta))
                sign  = "+" if delta > 0 else ""
                lines.append(
                    f"  {e.get('date','—')}  {e.get('type','—')}  "
                    f"#{e.get('_nftId','?')}"
                    f"{'  ' + sign + fmt_rize(amt) if amt else ''}"
                )
            if len(all_sorted) > 5:
                lines.append("_Reply *next* to see full activity timeline._")
    else:
        # Next pages: short header + 30 timeline entries per page
        per_page = 30
        start = (page - 1) * per_page  # page 1 = events 0-29, page 2 = 30-59...
        total   = len(all_sorted)

        # Guard: stale/inherited page out of range → fall back to page 0 profile view
        if start >= total or total == 0:
            if all_sorted:
                lines.append("*Full Activity Timeline:*")
                for e in all_sorted[:5]:
                    delta = e.get("delta", 0) or 0
                    amt   = abs(parse_amt(delta))
                    sign  = "+" if delta > 0 else ""
                    lines.append(
                        f"  {e.get('date','—')}  {e.get('type','—')}  "
                        f"#{e.get('_nftId','?')}"
                        f"{'  ' + sign + fmt_rize(amt) if amt else ''}"
                    )
            return "\n".join(l for l in lines if l is not None)

        page_ev = all_sorted[start:start + per_page]

        lines = [
            f"🔗 *Wallet Profile*",
            f"`{addr}`",
            f"Rank #{rank}" if rank else "",
            "",
            f"*Full Activity Timeline* — {start+1}–{min(start+per_page, total)} of {total}",
            "",
        ]
        for e in page_ev:
            delta = e.get("delta", 0) or 0
            amt   = abs(parse_amt(delta))
            sign  = "+" if delta > 0 else ""
            lines.append(
                f"  {e.get('date','—')}  {e.get('type','—')}  "
                f"#{e.get('_nftId','?')}"
                f"{'  ' + sign + fmt_rize(amt) if amt else ''}"
            )
        if start + per_page < total:
            lines.append(f"_Reply *next* for more._")

    return "\n".join(l for l in lines if l is not None)
