// GET /api/explorize/market?view=trades|liquidity|pools|bridges|tx
// One endpoint for the whole market layer (Vercel counts every file in /api as a function).
//
//   view=trades     chain, side=all|buy|sell|arbitrage, venue, pool, address (trader or sender), min/max (RIZE),
//                   minu/maxu (USD), from/to (UTC), page, limit(<=100)
//                   -> items + exact total + buy/sell totals over 7 / 14 / 30 days
//   view=liquidity  chain, pool, kind=all|add|remove|collect, page, limit
//                   -> one line per pool (balance photographed hourly, moves of the last 7 days) + the moves table
//   view=pools      the pools the explorer knows (name, venue, tokens, status)
//   view=bridges    chain, page, limit -> Chainlink CCIP messages, burned vs minted, plus the summary
//   view=tx         chain, hash -> one trade (every fill) and/or the liquidity moves of the transaction
//
// Everything comes from the derived tables written by the indexer (trades, liquidity_events, pools, ...).
// The raw transfers stay the ground truth; nothing here is ever guessed.
const { CHAINS, CHAIN_BY_ID, ZERO, query, fail, parseAddress, formatUnits } = require("./_db");

const MAX_PAGE = 20000;
const NATIVE_KEY = "0x" + "0".repeat(40);

// ---- small caches for the lookup tables (they change only when the labels files change) -------------------------
let lookups = null, lookupsAt = 0;
async function getLookups() {
  if (lookups && Date.now() - lookupsAt < 300000) return lookups;
  const [t, p, v] = await Promise.all([
    query("SELECT chain_id, encode(address,'hex') AS a, symbol, decimals FROM tokens"),
    query("SELECT chain_id, encode(pool_key,'hex') AS k, name, venue, kind, fee, status, source, note, " +
          "encode(token0,'hex') AS t0, encode(token1,'hex') AS t1 FROM pools"),
    query("SELECT key, name, kind, logo FROM venues"),
  ]);
  const tokens = {}, pools = {}, venues = {};
  for (const r of t.rows) tokens[r.chain_id + ":0x" + r.a] = { symbol: r.symbol, decimals: r.decimals };
  for (const r of p.rows) pools[r.chain_id + ":0x" + r.k] = r;
  for (const r of v.rows) venues[r.key] = { key: r.key, name: r.name, kind: r.kind, logo: r.logo || null };
  lookups = { tokens, pools, venues };
  lookupsAt = Date.now();
  return lookups;
}

function tokenOf(L, chainId, addr) {
  const t = L.tokens[chainId + ":" + String(addr).toLowerCase()];
  return t || { symbol: null, decimals: null };
}
function counterList(L, chainId, obj) {
  const out = [];
  for (const [addr, raw] of Object.entries(obj || {})) {
    const t = tokenOf(L, chainId, addr);
    out.push({
      address: addr,
      symbol: t.symbol || (addr.slice(0, 6) + "…" + addr.slice(-4)),
      known: !!t.symbol,
      amount: t.decimals == null ? null : formatUnits(raw, t.decimals),
      raw: String(raw),
    });
  }
  return out;
}
function poolInfo(L, chainId, key) {
  const p = L.pools[chainId + ":" + String(key).toLowerCase()];
  if (!p) return { key, name: String(key).slice(0, 8) + "…" + String(key).slice(-4), venue: null, venue_name: null, venue_logo: null };
  const v = L.venues[p.venue] || null;
  return { key, name: p.name, venue: p.venue || null, venue_name: v ? v.name : null, venue_logo: v ? v.logo : null,
           kind: p.kind, status: p.status };
}
const chainKey = (id) => CHAIN_BY_ID[id] || String(id);
const labelOf = (label, category) => (label ? { label, category } : null);
const hex = (b) => (b ? "0x" + Buffer.from(b).toString("hex") : null);

// "2026-11-01" or "2026-11-01T08:30" (UTC) -> ISO; end=true rounds up
function parseTime(v, end) {
  if (v == null || v === "") return null;
  const s = String(v).trim();
  let m = /^(\d{4}-\d{2}-\d{2})$/.exec(s);
  let iso = null;
  if (m) iso = end ? `${m[1]}T23:59:59.999Z` : `${m[1]}T00:00:00.000Z`;
  else if ((m = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(s))) {
    const sec = m[4] != null ? m[4] : end ? "59" : "00";
    iso = `${m[1]}T${m[2]}:${m[3]}:${sec}.${end && m[4] == null ? "999" : "000"}Z`;
  }
  if (!iso) return false;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? false : d.toISOString();
}
function parseRize(v) {
  if (v == null || v === "") return null;
  const s = String(v).trim();
  if (!/^\d{1,30}(\.\d{1,18})?$/.test(s)) return false;
  const [whole, frac = ""] = s.split(".");
  return (BigInt(whole) * 10n ** 18n + BigInt(frac.padEnd(18, "0"))).toString();
}
function parseUsd(v) {
  if (v == null || v === "") return null;
  const s = String(v).trim();
  return /^\d{1,15}(\.\d{1,6})?$/.test(s) ? s : false;
}
// 40 hex (address pool) or 64 hex (pool id) -> Buffer
function parseKey(v) {
  if (v == null || v === "") return null;
  const s = String(v).trim();
  return /^0x([0-9a-fA-F]{40}|[0-9a-fA-F]{64})$/.test(s) ? Buffer.from(s.slice(2), "hex") : false;
}
function paging(q) {
  const limit = Math.min(Math.max(parseInt(q.limit, 10) || 25, 1), 100);
  const page = Math.min(Math.max(parseInt(q.page, 10) || 1, 1), MAX_PAGE);
  return { limit, page, offset: (page - 1) * limit };
}
function send(res, body, cache) {
  res.setHeader("Cache-Control", cache || "public, s-maxage=15, stale-while-revalidate=60");
  return res.status(200).json(body);
}
const bad = (res, error) => res.status(400).json({ error });

// ------------------------------------------------------------------------------------------------ trades
async function viewTrades(q, res) {
  const ck = String(q.chain || "all").toLowerCase();
  if (ck !== "all" && !CHAINS[ck]) return bad(res, "bad_chain");
  const side = String(q.side || "all").toLowerCase();
  if (!["all", "buy", "sell", "arbitrage"].includes(side)) return bad(res, "bad_side");
  const address = parseAddress(q.address); if (address === false) return bad(res, "bad_address");
  const pool = parseKey(q.pool); if (pool === false) return bad(res, "bad_pool");
  const from = parseTime(q.from, false); if (from === false) return bad(res, "bad_from");
  const to = parseTime(q.to, true); if (to === false) return bad(res, "bad_to");
  const min = parseRize(q.min); if (min === false) return bad(res, "bad_min");
  const max = parseRize(q.max); if (max === false) return bad(res, "bad_max");
  const minu = parseUsd(q.minu); if (minu === false) return bad(res, "bad_minu");
  const maxu = parseUsd(q.maxu); if (maxu === false) return bad(res, "bad_maxu");
  const venue = q.venue ? String(q.venue).toLowerCase() : null;
  if (venue && !/^[a-z0-9-]{1,40}$/.test(venue)) return bad(res, "bad_venue");
  const { limit, page, offset } = paging(q);
  const L = await getLookups();

  // filters shared by the list and the totals (chain, pool, venue); the others only narrow the list
  const params = [];
  const add = (v) => { params.push(v); return "$" + params.length; };
  const base = [];
  if (ck !== "all") base.push(`t.chain_id = ${add(CHAINS[ck].id)}`);
  let poolKeys = null;
  if (pool) poolKeys = ["0x" + pool.toString("hex")];
  if (venue) {
    const ks = Object.entries(L.pools).filter(([, p]) => p.venue === venue).map(([k]) => k.split(":")[1]);
    poolKeys = poolKeys ? poolKeys.filter((k) => ks.includes(k)) : ks;
  }
  if (poolKeys) {
    const p = add(poolKeys.length ? poolKeys : ["-"]);
    base.push(`EXISTS (SELECT 1 FROM jsonb_array_elements(t.fills) f WHERE f->>'pool' = ANY(${p}::text[]))`);
  }
  const listWhere = base.slice();
  if (side !== "all") listWhere.push(`t.side = ${add(side)}`);
  if (address) { const p = add(address); listWhere.push(`(t.trader = ${p} OR t.sender = ${p})`); }
  if (from) listWhere.push(`t.block_time >= ${add(from)}::timestamptz`);
  if (to) listWhere.push(`t.block_time <= ${add(to)}::timestamptz`);
  if (min) listWhere.push(`t.rize >= ${add(min)}::numeric`);
  if (max) listWhere.push(`t.rize <= ${add(max)}::numeric`);
  if (minu) listWhere.push(`t.usd >= ${add(minu)}::numeric`);
  if (maxu) listWhere.push(`t.usd <= ${add(maxu)}::numeric`);
  const whereSql = listWhere.length ? "WHERE " + listWhere.join(" AND ") : "";
  const baseSql = base.length ? "WHERE " + base.join(" AND ") : "";

  const listParams = params.slice();
  const lp = "$" + (listParams.push(limit), listParams.length);
  const op = "$" + (listParams.push(offset), listParams.length);
  const baseCount = (() => { // base-only parameters are the first ones, in the same order
    const n = base.reduce((s, c) => s + (c.match(/\$\d+/g) || []).length, 0);
    return params.slice(0, n);
  })();

  const listSql = `
    SELECT t.chain_id, t.block_number, t.block_time, encode(t.tx_hash,'hex') AS tx_hex, t.side,
           encode(t.trader,'hex') AS trader_hex, encode(t.sender,'hex') AS sender_hex, encode(t.router,'hex') AS router_hex,
           t.rize::text AS rize_raw, t.counter, t.usd::text AS usd, t.price_usd::text AS price_usd, t.fills, t.swap_count,
           t.tags, t.flags,
           tl.label AS trader_label, tl.category AS trader_category, rl.label AS router_label, rl.category AS router_category
      FROM trades t
      LEFT JOIN LATERAL (SELECT label, category FROM labels WHERE address = t.trader AND chain_id IN (0, t.chain_id)
                          ORDER BY chain_id DESC LIMIT 1) tl ON true
      LEFT JOIN LATERAL (SELECT label, category FROM labels WHERE address = t.router AND chain_id IN (0, t.chain_id)
                          ORDER BY chain_id DESC LIMIT 1) rl ON true
     ${whereSql}
     ORDER BY t.block_time DESC, t.chain_id DESC, t.block_number DESC, t.tx_hash DESC
     LIMIT ${lp} OFFSET ${op}`;
  const countSql = `SELECT COUNT(*)::bigint AS n FROM trades t ${whereSql}`;
  const totalsSql = `
    SELECT t.side,
      COUNT(*) FILTER (WHERE t.block_time >= now() - interval '7 days')  AS n7,
      COUNT(*) FILTER (WHERE t.block_time >= now() - interval '14 days') AS n14,
      COUNT(*) FILTER (WHERE t.block_time >= now() - interval '30 days') AS n30,
      COALESCE(SUM(t.rize) FILTER (WHERE t.block_time >= now() - interval '7 days'), 0)::text  AS r7,
      COALESCE(SUM(t.rize) FILTER (WHERE t.block_time >= now() - interval '14 days'), 0)::text AS r14,
      COALESCE(SUM(t.rize) FILTER (WHERE t.block_time >= now() - interval '30 days'), 0)::text AS r30,
      COALESCE(SUM(t.usd) FILTER (WHERE t.block_time >= now() - interval '7 days'), 0)::text  AS u7,
      COALESCE(SUM(t.usd) FILTER (WHERE t.block_time >= now() - interval '14 days'), 0)::text AS u14,
      COALESCE(SUM(t.usd) FILTER (WHERE t.block_time >= now() - interval '30 days'), 0)::text AS u30,
      COUNT(*) FILTER (WHERE t.usd IS NULL AND t.block_time >= now() - interval '30 days') AS unpriced30
      FROM trades t ${baseSql ? baseSql + " AND" : "WHERE"} t.block_time >= now() - interval '30 days'
     GROUP BY t.side`;

  const [list, count, totals] = await Promise.all([
    query(listSql, listParams), query(countSql, listParams.slice(0, params.length)), query(totalsSql, baseCount)]);
  const items = list.rows.map((r) => {
    const fills = (r.fills || []).map((f) => poolInfo(L, r.chain_id, f.pool));
    const seen = new Set();
    const venues = fills.filter((f) => !seen.has(f.key) && seen.add(f.key));
    return {
      chain: chainKey(r.chain_id), chain_name: CHAINS[chainKey(r.chain_id)] ? CHAINS[chainKey(r.chain_id)].name : "",
      block: Number(r.block_number), time: r.block_time.toISOString(), tx: "0x" + r.tx_hex, side: r.side,
      trader: r.trader_hex ? "0x" + r.trader_hex : null, trader_label: labelOf(r.trader_label, r.trader_category),
      sender: r.sender_hex ? "0x" + r.sender_hex : null,
      router: r.router_hex ? "0x" + r.router_hex : null, router_label: labelOf(r.router_label, r.router_category),
      rize: formatUnits(r.rize_raw),
      usd: r.usd == null ? null : Number(r.usd), price_usd: r.price_usd == null ? null : Number(r.price_usd),
      counter: counterList(L, r.chain_id, r.counter),
      venues, swaps: r.swap_count, tags: r.tags || [], flags: r.flags || [],
    };
  });
  const win = { 7: {}, 14: {}, 30: {} };
  for (const d of [7, 14, 30]) for (const s of ["buy", "sell", "arbitrage"]) win[d][s] = { trades: 0, rize: "0", usd: 0 };
  let unpriced30 = 0;
  for (const r of totals.rows) {
    for (const d of [7, 14, 30]) {
      win[d][r.side] = { trades: Number(r["n" + d]), rize: formatUnits(r["r" + d]), usd: Number(r["u" + d]) };
    }
    unpriced30 += Number(r.unpriced30);
  }
  const total = Number(count.rows[0].n);
  return send(res, { ok: true, view: "trades", page, limit, total, pages: Math.max(1, Math.ceil(total / limit)),
                     count: items.length, items, totals: win, unpriced_30d: unpriced30 });
}

// ------------------------------------------------------------------------------------------------ liquidity
async function viewLiquidity(q, res) {
  const ck = String(q.chain || "all").toLowerCase();
  if (ck !== "all" && !CHAINS[ck]) return bad(res, "bad_chain");
  const kind = String(q.kind || "all").toLowerCase();
  if (!["all", "add", "remove", "collect"].includes(kind)) return bad(res, "bad_kind");
  const pool = parseKey(q.pool); if (pool === false) return bad(res, "bad_pool");
  const { limit, page, offset } = paging(q);
  const L = await getLookups();

  const params = [];
  const add = (v) => { params.push(v); return "$" + params.length; };
  const where = [];
  if (ck !== "all") where.push(`e.chain_id = ${add(CHAINS[ck].id)}`);
  if (pool) where.push(`e.pool_key = ${add(pool)}`);
  if (kind !== "all") where.push(`e.kind = ${add(kind)}`);
  const whereSql = where.length ? "WHERE " + where.join(" AND ") : "";
  const lp = "$" + (params.push(limit), params.length);
  const op = "$" + (params.push(offset), params.length);

  const eventsSql = `
    SELECT e.chain_id, e.block_number, e.block_time, encode(e.tx_hash,'hex') AS tx_hex, e.seq, encode(e.pool_key,'hex') AS pool_hex,
           e.kind, encode(e.provider,'hex') AS provider_hex, e.rize::text AS rize_raw, e.counter, e.detail,
           pl.label AS provider_label, pl.category AS provider_category
      FROM liquidity_events e
      LEFT JOIN LATERAL (SELECT label, category FROM labels WHERE address = e.provider AND chain_id IN (0, e.chain_id)
                          ORDER BY chain_id DESC LIMIT 1) pl ON true
     ${whereSql}
     ORDER BY e.block_time DESC, e.chain_id DESC, e.block_number DESC, e.tx_hash DESC, e.seq DESC
     LIMIT ${lp} OFFSET ${op}`;
  const countSql = `SELECT COUNT(*)::bigint AS n FROM liquidity_events e ${whereSql}`;

  // one line per pool: the last photograph + the moves of the last 7 days
  const pp = [];
  let poolWhere = "";
  if (ck !== "all") { pp.push(CHAINS[ck].id); poolWhere = "WHERE p.chain_id = $1"; }
  const linesSql = `
    SELECT p.chain_id, encode(p.pool_key,'hex') AS k, s.ts, s.block_number, s.rize::text AS snap_rize,
           encode(s.counter_token,'hex') AS snap_ct, s.counter::text AS snap_counter,
           COALESCE(m.adds,0) AS adds, COALESCE(m.removes,0) AS removes, COALESCE(m.collects,0) AS collects,
           COALESCE(m.net,0)::text AS net7
      FROM pools p
      LEFT JOIN LATERAL (SELECT ts, block_number, rize, counter_token, counter FROM pool_snapshots
                          WHERE chain_id = p.chain_id AND pool_key = p.pool_key ORDER BY ts DESC LIMIT 1) s ON true
      LEFT JOIN LATERAL (SELECT COUNT(*) FILTER (WHERE kind='add') AS adds, COUNT(*) FILTER (WHERE kind='remove') AS removes,
                                COUNT(*) FILTER (WHERE kind='collect') AS collects,
                                SUM(CASE WHEN kind IN ('add','remove') THEN rize END) AS net
                           FROM liquidity_events WHERE chain_id = p.chain_id AND pool_key = p.pool_key
                            AND block_time >= now() - interval '7 days') m ON true
     ${poolWhere}
     ORDER BY CASE p.chain_id WHEN 8453 THEN 0 WHEN 1 THEN 1 WHEN 56 THEN 2 ELSE 3 END, p.name`;

  const [ev, count, lines] = await Promise.all([query(eventsSql, params), query(countSql, params.slice(0, params.length - 2)),
                                               query(linesSql, pp)]);
  const items = ev.rows.map((r) => {
    const info = poolInfo(L, r.chain_id, "0x" + r.pool_hex);
    const d = r.detail || {};
    return {
      chain: chainKey(r.chain_id), time: r.block_time.toISOString(), block: Number(r.block_number), tx: "0x" + r.tx_hex,
      pool: info, kind: r.kind,
      provider: r.provider_hex ? "0x" + r.provider_hex : null, provider_label: labelOf(r.provider_label, r.provider_category),
      rize: r.rize_raw == null ? null : formatUnits(r.rize_raw),
      counter: counterList(L, r.chain_id, r.counter),
      rebalance: d.tx_verdict === "liquidity_rebalance",
      tick_lower: d.tick_lower == null ? null : d.tick_lower, tick_upper: d.tick_upper == null ? null : d.tick_upper,
    };
  });
  const pools = lines.rows.map((r) => {
    const info = poolInfo(L, r.chain_id, "0x" + r.k);
    const t = r.snap_ct ? tokenOf(L, r.chain_id, "0x" + r.snap_ct) : { symbol: null, decimals: null };
    return {
      chain: chainKey(r.chain_id), ...info,
      snapshot: r.ts ? {
        at: r.ts.toISOString(), block: r.block_number == null ? null : Number(r.block_number),
        rize: formatUnits(r.snap_rize),
        counter_symbol: t.symbol, counter: r.snap_counter == null || t.decimals == null ? null : formatUnits(r.snap_counter, t.decimals),
      } : null,
      moves_7d: { adds: Number(r.adds), removes: Number(r.removes), collects: Number(r.collects), net_rize: formatUnits(r.net7) },
    };
  });
  const total = Number(count.rows[0].n);
  return send(res, { ok: true, view: "liquidity", page, limit, total, pages: Math.max(1, Math.ceil(total / limit)),
                     count: items.length, items, pools });
}

// ------------------------------------------------------------------------------------------------ pools
async function viewPools(q, res) {
  const L = await getLookups();
  const items = Object.entries(L.pools).map(([k, p]) => {
    const chainId = Number(k.split(":")[0]);
    const v = L.venues[p.venue] || null;
    const t0 = p.t0 ? tokenOf(L, chainId, "0x" + p.t0).symbol : null;
    const t1 = p.t1 ? tokenOf(L, chainId, "0x" + p.t1).symbol : null;
    return { chain: chainKey(chainId), key: "0x" + p.k, name: p.name, venue: p.venue || null, venue_name: v ? v.name : null,
             venue_logo: v ? v.logo : null, kind: p.kind, fee: p.fee, status: p.status, source: p.source,
             pair: [t0, t1].filter(Boolean).join(" / ") || null, note: p.note || null };
  });
  items.sort((a, b) => (a.chain + a.name).localeCompare(b.chain + b.name));
  return send(res, { ok: true, view: "pools", count: items.length, items,
                     venues: Object.values(L.venues) }, "public, s-maxage=300, stale-while-revalidate=600");
}

// ------------------------------------------------------------------------------------------------ bridges
async function viewBridges(q, res) {
  const ck = String(q.chain || "all").toLowerCase();
  if (ck !== "all" && !CHAINS[ck]) return bad(res, "bad_chain");
  const { limit, page, offset } = paging(q);
  const params = [ZERO];
  let where = "";
  if (ck !== "all") { params.push(CHAINS[ck].id); where = "WHERE m.source_chain_id = $2 OR m.dest_chain_id = $2"; }
  const lp = "$" + (params.push(limit), params.length);
  const op = "$" + (params.push(offset), params.length);
  const listSql = `
    SELECT encode(m.message_id,'hex') AS id, m.status, m.source_chain_id, m.dest_chain_id,
           encode(m.source_tx,'hex') AS source_tx, encode(m.dest_tx,'hex') AS dest_tx,
           encode(m.sender,'hex') AS sender, encode(m.receiver,'hex') AS receiver, m.amount::text AS amount,
           m.sent_at, m.received_at,
           m.raw->'sourceNetworkInfo'->>'name' AS src_name, m.raw->'destNetworkInfo'->>'name' AS dest_name,
           (SELECT COALESCE(SUM(amount),0) FROM transfers x WHERE x.chain_id = m.source_chain_id AND x.tx_hash = m.source_tx
               AND x.to_addr = $1)::text AS burned,
           (SELECT COALESCE(SUM(amount),0) FROM transfers x WHERE x.chain_id = m.dest_chain_id AND x.tx_hash = m.dest_tx
               AND x.from_addr = $1)::text AS minted
      FROM ccip_messages m ${where}
     ORDER BY m.sent_at DESC NULLS LAST, m.message_id
     LIMIT ${lp} OFFSET ${op}`;
  const countSql = `SELECT COUNT(*)::bigint AS n FROM ccip_messages m ${where.replace(/\$2/g, "$1")}`;
  const summarySql = `
    SELECT COUNT(*)::bigint AS messages,
           COUNT(*) FILTER (WHERE m.status = 'SUCCESS')::bigint AS success,
           COUNT(*) FILTER (WHERE m.status = 'FAILED')::bigint AS failed,
           COUNT(*) FILTER (WHERE m.status IS NULL OR m.status NOT IN ('SUCCESS','FAILED'))::bigint AS in_flight
      FROM ccip_messages m`;
  const missingSql = `SELECT chain_id, COUNT(*)::bigint AS n FROM ccip_checked WHERE NOT found GROUP BY chain_id`;
  const [list, count, summary, missing] = await Promise.all([
    query(listSql, params), query(countSql, params.slice(1, params.length - 2)), query(summarySql), query(missingSql)]);
  const items = list.rows.map((r) => {
    // a destination we do not index (another kind of network): only the burn side can be checked
    const outside = !CHAIN_BY_ID[r.dest_chain_id];
    const reconciled = r.status === "SUCCESS" && r.dest_tx && BigInt(r.burned) > 0n && r.burned === r.minted;
    const burnOnly = r.status === "SUCCESS" && outside && r.amount != null && BigInt(r.burned) > 0n && r.burned === r.amount;
    let verdict = "in_flight";
    if (r.status === "FAILED") verdict = "failed";
    else if (r.status === "SUCCESS") verdict = burnOnly ? "burn_only" : reconciled && !outside ? "reconciled" : "mismatch";
    return {
      id: "0x" + r.id, status: r.status, verdict,
      source: CHAIN_BY_ID[r.source_chain_id] ? chainKey(r.source_chain_id) : (r.src_name || "other"),
      dest: CHAIN_BY_ID[r.dest_chain_id] ? chainKey(r.dest_chain_id) : (r.dest_name || "other"),
      source_tx: r.source_tx ? "0x" + r.source_tx : null, dest_tx: r.dest_tx ? "0x" + r.dest_tx : null,
      sender: r.sender ? "0x" + r.sender : null, receiver: r.receiver ? "0x" + r.receiver : null,
      amount_ccip: r.amount == null ? null : formatUnits(r.amount),
      burned: formatUnits(r.burned), minted: formatUnits(r.minted),
      sent_at: r.sent_at ? r.sent_at.toISOString() : null, received_at: r.received_at ? r.received_at.toISOString() : null,
    };
  });
  const s = summary.rows[0];
  const noMsg = {};
  for (const r of missing.rows) noMsg[chainKey(r.chain_id)] = Number(r.n);
  const total = Number(count.rows[0].n);
  return send(res, { ok: true, view: "bridges", page, limit, total, pages: Math.max(1, Math.ceil(total / limit)),
                     count: items.length, items,
                     summary: { messages: Number(s.messages), success: Number(s.success), failed: Number(s.failed),
                                in_flight: Number(s.in_flight), burns_without_message: noMsg } });
}

// ------------------------------------------------------------------------------------------------ one transaction
async function viewTx(q, res) {
  const ck = String(q.chain || "").toLowerCase();
  if (!CHAINS[ck]) return bad(res, "bad_chain");
  const hash = String(q.hash || "").trim();
  if (!/^0x[0-9a-fA-F]{64}$/.test(hash)) return bad(res, "bad_hash");
  const cid = CHAINS[ck].id, h = Buffer.from(hash.slice(2), "hex");
  const L = await getLookups();
  const [t, e] = await Promise.all([
    query(`SELECT t.block_number, t.block_time, t.side, encode(t.trader,'hex') AS trader_hex, encode(t.sender,'hex') AS sender_hex,
                  encode(t.router,'hex') AS router_hex, t.rize::text AS rize_raw, t.counter, t.usd::text AS usd,
                  t.price_usd::text AS price_usd, t.fills, t.swap_count, t.tags, t.flags, t.extra,
                  tl.label AS trader_label, tl.category AS trader_category, rl.label AS router_label, rl.category AS router_category
             FROM trades t
             LEFT JOIN LATERAL (SELECT label, category FROM labels WHERE address = t.trader AND chain_id IN (0, t.chain_id)
                                 ORDER BY chain_id DESC LIMIT 1) tl ON true
             LEFT JOIN LATERAL (SELECT label, category FROM labels WHERE address = t.router AND chain_id IN (0, t.chain_id)
                                 ORDER BY chain_id DESC LIMIT 1) rl ON true
            WHERE t.chain_id = $1 AND t.tx_hash = $2`, [cid, h]),
    query(`SELECT e.block_number, e.block_time, e.seq, encode(e.pool_key,'hex') AS pool_hex, e.kind,
                  encode(e.provider,'hex') AS provider_hex, e.rize::text AS rize_raw, e.counter, e.detail
             FROM liquidity_events e WHERE e.chain_id = $1 AND e.tx_hash = $2 ORDER BY e.seq`, [cid, h]),
  ]);
  if (!t.rows.length && !e.rows.length) {
    res.setHeader("Cache-Control", "no-store");
    return res.status(404).json({ error: "not_found" });
  }
  let trade = null;
  if (t.rows.length) {
    const r = t.rows[0];
    trade = {
      side: r.side, trader: r.trader_hex ? "0x" + r.trader_hex : null, trader_label: labelOf(r.trader_label, r.trader_category),
      sender: r.sender_hex ? "0x" + r.sender_hex : null,
      router: r.router_hex ? "0x" + r.router_hex : null, router_label: labelOf(r.router_label, r.router_category),
      rize: formatUnits(r.rize_raw), usd: r.usd == null ? null : Number(r.usd),
      price_usd: r.price_usd == null ? null : Number(r.price_usd), counter: counterList(L, cid, r.counter),
      swaps: r.swap_count, tags: r.tags || [], flags: r.flags || [],
      fills: (r.fills || []).map((f) => ({
        pool: poolInfo(L, cid, f.pool), rize: formatUnits(f.rize), direction: f.dir > 0 ? "sell" : "buy",
        counter: counterList(L, cid, f.counter), usd: f.usd == null ? null : Number(f.usd),
      })),
      extra: r.extra || {},
    };
  }
  const first = t.rows[0] || e.rows[0];
  const liquidity = e.rows.map((r) => {
    const d = r.detail || {};
    return { pool: poolInfo(L, cid, "0x" + r.pool_hex), kind: r.kind, provider: r.provider_hex ? "0x" + r.provider_hex : null,
             rize: r.rize_raw == null ? null : formatUnits(r.rize_raw), counter: counterList(L, cid, r.counter),
             rebalance: d.tx_verdict === "liquidity_rebalance",
             tick_lower: d.tick_lower == null ? null : d.tick_lower, tick_upper: d.tick_upper == null ? null : d.tick_upper };
  });
  return send(res, { ok: true, view: "tx", chain: ck, chain_name: CHAINS[ck].name, tx: hash.toLowerCase(),
                     block: Number(first.block_number), time: first.block_time.toISOString(), trade, liquidity },
              "public, s-maxage=300, stale-while-revalidate=600");
}

const VIEWS = { trades: viewTrades, liquidity: viewLiquidity, pools: viewPools, bridges: viewBridges, tx: viewTx };

module.exports = async function handler(req, res) {
  if (req.method !== "GET") return res.status(405).json({ error: "method_not_allowed" });
  const q = req.query || {};
  const view = String(q.view || "trades").toLowerCase();
  if (!VIEWS[view]) return res.status(400).json({ error: "bad_view" });
  try {
    return await VIEWS[view](q, res);
  } catch (err) {
    return fail(res, err);
  }
};
module.exports._test = { parseKey, parseUsd, counterList, poolInfo, NATIVE_KEY, hex };
