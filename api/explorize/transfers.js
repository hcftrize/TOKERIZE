// GET /api/explorize/transfers
//   chain=all|base|ethereum|bsc|polygon   type=all|transfer|mint|burn   address=0x...
//   from=2026-11-01 | 2026-11-01T08:30      (UTC, inclusive)      to=... (UTC, inclusive)
//   min=1000  max=50000                      (amount in RIZE, inclusive)
//   mode=raw|reconciled   raw = every transfer (default)
//                         reconciled = ONE line per event: trade (buy / sell / arb), liquidity move, CCIP bridge message, and the
//                         transfers left over (tx, mint, burn, in cex, out cex). The plumbing of a trade, a liquidity move or a
//                         bridge (routers, vaults, pools, relays) is folded into its line.
//   types=buy,sell,arb,liquidity,ccip,tx,mint,burn,in_cex,out_cex   (reconciled only; default = all)
//   page=1 (1-based)   limit=25 (max 100)
// Newest first. Answers with the page of rows plus the exact total, so the page can show "1 / N".
const { CHAINS, CHAIN_BY_ID, ZERO, query, fail, parseAddress, formatUnits } = require("./_db");
const { getLookups, poolInfo, labelOf, netInfo } = require("./_lookups");

const MAX_PAGE = 200000;

// "2026-11-01" or "2026-11-01T08:30[:15]" (UTC) -> ISO string; end=true rounds up to the end of the day/minute/second
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

// "1234.5" RIZE -> raw integer string with 18 decimals
function parseRize(v) {
  if (v == null || v === "") return null;
  const s = String(v).trim();
  if (!/^\d{1,30}(\.\d{1,18})?$/.test(s)) return false;
  const [whole, frac = ""] = s.split(".");
  return (BigInt(whole) * 10n ** 18n + BigInt(frac.padEnd(18, "0"))).toString();
}

module.exports = async function handler(req, res) {
  if (req.method !== "GET") return res.status(405).json({ error: "method_not_allowed" });
  const q = req.query || {};

  const chainKey = String(q.chain || "all").toLowerCase();
  if (chainKey !== "all" && !CHAINS[chainKey]) return res.status(400).json({ error: "bad_chain" });
  const type = String(q.type || "all").toLowerCase();
  if (!["all", "transfer", "mint", "burn"].includes(type)) return res.status(400).json({ error: "bad_type" });
  const address = parseAddress(q.address);
  if (address === false) return res.status(400).json({ error: "bad_address" });
  const from = parseTime(q.from, false);
  if (from === false) return res.status(400).json({ error: "bad_from" });
  const to = parseTime(q.to, true);
  if (to === false) return res.status(400).json({ error: "bad_to" });
  const min = parseRize(q.min);
  if (min === false) return res.status(400).json({ error: "bad_min" });
  const max = parseRize(q.max);
  if (max === false) return res.status(400).json({ error: "bad_max" });
  const mode = String(q.mode || "raw").toLowerCase();
  if (!["raw", "reconciled"].includes(mode)) return res.status(400).json({ error: "bad_mode" });
  const limit = Math.min(Math.max(parseInt(q.limit, 10) || 25, 1), 100);
  const page = Math.min(Math.max(parseInt(q.page, 10) || 1, 1), MAX_PAGE);

  const PLUMBING = `NOT EXISTS (SELECT 1 FROM trades x WHERE x.chain_id = transfers.chain_id AND x.tx_hash = transfers.tx_hash)
      AND NOT EXISTS (SELECT 1 FROM liquidity_events y WHERE y.chain_id = transfers.chain_id AND y.tx_hash = transfers.tx_hash)`;
  // The market tables exist once the enrich job has run once; before that, "reconciled" quietly means "raw".
  try {
    if (mode === "reconciled") return await unified();
    return await answer(false);
  } catch (err) {
    if (mode === "reconciled" && err && err.code === "42P01") return answer(false).catch((e) => fail(res, e));
    return fail(res, err);
  }

  // ---- reconciled feed: one line per event ------------------------------------------------------------------------
  async function unified() {
    const ALL = ["buy", "sell", "arb", "liquidity", "ccip", "tx", "mint", "burn", "in_cex", "out_cex"];
    let types = ALL;
    if (q.types != null && String(q.types).trim() !== "") {
      types = String(q.types).toLowerCase().split(",").map((x) => x.trim()).filter(Boolean);
      if (!types.length || types.some((x) => !ALL.includes(x))) return res.status(400).json({ error: "bad_types" });
    }
    const want = (t) => types.includes(t);
    const zero = "decode('" + "00".repeat(20) + "','hex')";
    const CEX = "(SELECT address FROM labels WHERE category IN ('cex','exchange'))";
    // the chain filter of a CCIP message: either side
    const params = [];
    const add = (v) => { params.push(v); return "$" + params.length; };
    const cid = chainKey !== "all" ? CHAINS[chainKey].id : null;
    const branches = [];   // { ord, sql, count }   sql selects the shared columns, count selects 1 per event

    const COLS = "etype, chain_id, chain2_id, name1, name2, block_number, block_time, sub, tx_hash, from_addr, to_addr, amount, usd, price_usd, meta, ord";

    // --- trades (buy / sell / arb)
    const ts = ["buy", "sell", "arb"].filter(want);
    if (ts.length) {
      const w = [];
      w.push(`CASE t.side WHEN 'arbitrage' THEN 'arb' ELSE t.side END = ANY(${add(ts)}::text[])`);
      if (cid) w.push(`t.chain_id = ${add(cid)}`);
      if (address) { const p = add(address); w.push(`(t.trader = ${p} OR t.sender = ${p})`); }
      if (from) w.push(`t.block_time >= ${add(from)}::timestamptz`);
      if (to) w.push(`t.block_time <= ${add(to)}::timestamptz`);
      if (min) w.push(`t.rize >= ${add(min)}::numeric`);
      if (max) w.push(`t.rize <= ${add(max)}::numeric`);
      const from_ = `FROM trades t WHERE ${w.join(" AND ")}`;
      branches.push({
        ord: 1, count: `SELECT 1 ${from_}`,
        sql: `SELECT CASE t.side WHEN 'arbitrage' THEN 'arb' ELSE t.side END AS etype, t.chain_id, NULL::int AS chain2_id,
                     NULL::text AS name1, NULL::text AS name2, t.block_number, t.block_time, 0 AS sub, t.tx_hash,
                     CASE WHEN t.side = 'buy' THEN NULL ELSE t.trader END AS from_addr,
                     CASE WHEN t.side = 'buy' THEN t.trader ELSE NULL END AS to_addr,
                     t.rize AS amount, t.usd, t.price_usd, t.fills AS meta, 1 AS ord ${from_}`,
      });
    }

    // --- liquidity moves, one line per transaction
    if (want("liquidity")) {
      const w = [];
      if (cid) w.push(`e.chain_id = ${add(cid)}`);
      if (address) w.push(`e.provider = ${add(address)}`);
      if (from) w.push(`e.block_time >= ${add(from)}::timestamptz`);
      if (to) w.push(`e.block_time <= ${add(to)}::timestamptz`);
      const hv = [];
      const ins = "COALESCE(SUM(e.rize) FILTER (WHERE e.rize > 0), 0)";
      const outs = "COALESCE(-SUM(e.rize) FILTER (WHERE e.rize < 0), 0)";
      if (min) hv.push(`GREATEST(${ins}, ${outs}) >= ${add(min)}::numeric`);
      if (max) hv.push(`GREATEST(${ins}, ${outs}) <= ${add(max)}::numeric`);
      const body = `FROM liquidity_events e ${w.length ? "WHERE " + w.join(" AND ") : ""}
                    GROUP BY e.chain_id, e.tx_hash ${hv.length ? "HAVING " + hv.join(" AND ") : ""}`;
      branches.push({
        ord: 2, count: `SELECT 1 ${body}`,
        sql: `SELECT 'liquidity'::text AS etype, e.chain_id, NULL::int AS chain2_id, NULL::text AS name1, NULL::text AS name2,
                     MIN(e.block_number) AS block_number, MIN(e.block_time) AS block_time, 0 AS sub, e.tx_hash,
                     (array_agg(e.provider) FILTER (WHERE e.provider IS NOT NULL))[1] AS from_addr, NULL::bytea AS to_addr,
                     GREATEST(${ins}, ${outs}) AS amount, NULL::numeric AS usd, NULL::numeric AS price_usd,
                     jsonb_build_object('pool', encode((array_agg(e.pool_key ORDER BY e.seq))[1], 'hex'),
                                        'dir', CASE WHEN ${ins} >= ${outs} THEN 'add' ELSE 'remove' END,
                                        'pools', COUNT(DISTINCT e.pool_key), 'moves', COUNT(*)) AS meta,
                     2 AS ord ${body}`,
      });
    }

    // --- CCIP messages (one line per message, burn and mint folded in)
    if (want("ccip")) {
      const tm = "COALESCE(m.sent_at, m.received_at)";
      const w = [];
      if (cid) { const p = add(cid); w.push(`(m.source_chain_id = ${p} OR m.dest_chain_id = ${p})`); }
      if (address) { const p = add(address); w.push(`(m.sender = ${p} OR m.receiver = ${p})`); }
      if (from) w.push(`${tm} >= ${add(from)}::timestamptz`);
      if (to) w.push(`${tm} <= ${add(to)}::timestamptz`);
      if (min) w.push(`m.amount >= ${add(min)}::numeric`);
      if (max) w.push(`m.amount <= ${add(max)}::numeric`);
      const from_ = `FROM ccip_messages m ${w.length ? "WHERE " + w.join(" AND ") : ""}`;
      branches.push({
        ord: 3, count: `SELECT 1 ${from_}`,
        sql: `SELECT 'ccip'::text AS etype, m.source_chain_id AS chain_id, m.dest_chain_id AS chain2_id,
                     m.raw->'sourceNetworkInfo'->>'name' AS name1, m.raw->'destNetworkInfo'->>'name' AS name2,
                     0::bigint AS block_number, ${tm} AS block_time, 0 AS sub, m.message_id AS tx_hash,
                     m.sender AS from_addr, m.receiver AS to_addr,
                     COALESCE(m.amount, (SELECT SUM(x.amount) FROM transfers x WHERE x.chain_id = m.source_chain_id
                                            AND x.tx_hash = m.source_tx AND x.to_addr = ${zero})) AS amount,
                     NULL::numeric AS usd, NULL::numeric AS price_usd,
                     jsonb_build_object('status', m.status, 'source_tx', encode(m.source_tx, 'hex'),
                                        'dest_tx', encode(m.dest_tx, 'hex')) AS meta,
                     3 AS ord ${from_}`,
      });
    }

    // --- what is left of the transfers: tx, mint, burn, in cex, out cex
    const rest = ["tx", "mint", "burn", "in_cex", "out_cex"].filter(want);
    if (rest.length) {
      const kind = `CASE WHEN transfers.from_addr = ${zero} THEN 'mint' WHEN transfers.to_addr = ${zero} THEN 'burn'
                         WHEN transfers.to_addr IN ${CEX} THEN 'in_cex' WHEN transfers.from_addr IN ${CEX} THEN 'out_cex'
                         ELSE 'tx' END`;
      const w = [];
      if (rest.length < 5) w.push(`${kind} = ANY(${add(rest)}::text[])`);
      if (cid) w.push(`transfers.chain_id = ${add(cid)}`);
      if (address) { const p = add(address); w.push(`(transfers.from_addr = ${p} OR transfers.to_addr = ${p})`); }
      if (from) w.push(`transfers.block_time >= ${add(from)}::timestamptz`);
      if (to) w.push(`transfers.block_time <= ${add(to)}::timestamptz`);
      if (min) w.push(`transfers.amount >= ${add(min)}::numeric`);
      if (max) w.push(`transfers.amount <= ${add(max)}::numeric`);
      w.push(PLUMBING);
      w.push(`NOT EXISTS (SELECT 1 FROM ccip_messages c WHERE c.source_chain_id = transfers.chain_id AND c.source_tx = transfers.tx_hash)`);
      w.push(`NOT EXISTS (SELECT 1 FROM ccip_messages c WHERE c.dest_chain_id = transfers.chain_id AND c.dest_tx = transfers.tx_hash)`);
      const from_ = `FROM transfers WHERE ${w.join(" AND ")}`;
      branches.push({
        ord: 4, count: `SELECT 1 ${from_}`,
        sql: `SELECT ${kind} AS etype, transfers.chain_id, NULL::int AS chain2_id, NULL::text AS name1, NULL::text AS name2,
                     transfers.block_number, transfers.block_time, transfers.log_index AS sub, transfers.tx_hash,
                     transfers.from_addr, transfers.to_addr, transfers.amount, NULL::numeric AS usd, NULL::numeric AS price_usd, NULL::jsonb AS meta,
                     4 AS ord ${from_}`,
      });
    }

    if (!branches.length) return res.status(400).json({ error: "bad_types" });
    const ORDER = (d, t = "") => `${t}block_time ${d}, ${t}chain_id ${d}, ${t}block_number ${d}, ${t}sub ${d}, ${t}tx_hash ${d}, ${t}ord ${d}`;
    const pageSql = (dir, lim, off) => `
      SELECT u.etype, u.chain_id, u.chain2_id, u.name1, u.name2, u.block_number, u.block_time, u.sub, u.tx_hash,
             encode(u.from_addr,'hex') AS from_hex, encode(u.to_addr,'hex') AS to_hex, u.amount::text AS amount_raw,
             u.usd::text AS usd, u.price_usd::text AS price_usd, u.meta,
             fl.label AS from_label, fl.category AS from_category, fl.logo AS from_logo,
             tl.label AS to_label, tl.category AS to_category, tl.logo AS to_logo
        FROM (
          SELECT * FROM (
            ${branches.map((b) => `(SELECT * FROM (${b.sql}) b${b.ord} ORDER BY ${ORDER(dir)} LIMIT ${lim + off})`).join("\n            UNION ALL\n            ")}
          ) all_rows ORDER BY ${ORDER(dir)} LIMIT ${lim} OFFSET ${off}) u
        LEFT JOIN LATERAL (SELECT label, category, logo FROM labels WHERE address = u.from_addr AND chain_id IN (0, u.chain_id)
                            ORDER BY chain_id DESC LIMIT 1) fl ON true
        LEFT JOIN LATERAL (SELECT label, category, logo FROM labels WHERE address = u.to_addr
                              AND chain_id IN (0, COALESCE(NULLIF(u.chain2_id, 0), u.chain_id)) ORDER BY chain_id DESC LIMIT 1) tl ON true
       ORDER BY ${ORDER("DESC", "u.")}`;
    const countSql = "SELECT " + branches.map((b, i) => `(SELECT COUNT(*) FROM (${b.count}) c${i}) AS c${i}`).join(", ");

    const offset = (page - 1) * limit;
    const FAST = Number(process.env.EXPLORIZE_FAST_OFFSET || 25000);
    let total = null, rows;
    const getTotal = async () => (await query(countSql, params)).rows[0];
    const sumRow = (r) => Object.values(r).reduce((s, v) => s + Number(v), 0);
    if (offset <= FAST) {
      const [r, c] = await Promise.all([
        query(pageSql("DESC", limit, offset), params),
        getTotal().then(sumRow, () => null),     // a slow count must not take the page down with it
      ]);
      rows = r.rows; total = c;
    } else {
      total = sumRow(await getTotal());
      const end = Math.min(page * limit, total);
      if (end <= offset) rows = [];
      else if (total - end < offset) rows = (await query(pageSql("ASC", end - offset, total - end), params)).rows;
      else rows = (await query(pageSql("DESC", limit, offset), params)).rows;
    }

    const L = await getLookups();
    const items = rows.map((r) => {
      const net1 = r.etype === "ccip" ? netInfo(r.chain_id, r.name1) : null;
      const net2 = r.etype === "ccip" ? netInfo(r.chain2_id, r.name2) : null;
      const key = net1 ? net1.key : CHAIN_BY_ID[r.chain_id];
      const it = {
        type: r.etype,
        chain: key, chain_name: net1 ? (net1.indexed ? CHAINS[key].name : net1.name) : key ? CHAINS[key].name : String(r.chain_id),
        block: Number(r.block_number), time: r.block_time.toISOString(),
        tx: r.etype === "ccip" ? null : "0x" + Buffer.from(r.tx_hash).toString("hex"),
        log_index: r.etype === "tx" || r.etype === "mint" || r.etype === "burn" || r.etype === "in_cex" || r.etype === "out_cex" ? r.sub : null,
        from: r.from_hex ? "0x" + r.from_hex : null, to: r.to_hex ? "0x" + r.to_hex : null,
        from_label: labelOf(r.from_label, r.from_category, r.from_logo), to_label: labelOf(r.to_label, r.to_category, r.to_logo),
        amount: formatUnits(r.amount_raw == null ? "0" : r.amount_raw),
        usd: r.usd == null ? null : Number(r.usd),
        price_usd: r.price_usd == null ? null : Number(r.price_usd),
      };
      if (net2) {
        it.chain2 = net2.key; it.chain2_name = net2.indexed ? CHAINS[net2.key].name : net2.name;
        const m = r.meta || {};
        it.message_id = "0x" + Buffer.from(r.tx_hash).toString("hex");
        it.url = "https://ccip.chain.link/msg/" + it.message_id;
        it.status = m.status || null;
        it.source_tx = m.source_tx ? "0x" + m.source_tx : null; it.dest_tx = m.dest_tx ? "0x" + m.dest_tx : null;
      } else if (r.etype === "buy" || r.etype === "sell" || r.etype === "arb") {
        const seen = new Set(), venues = [];
        for (const f of r.meta || []) {
          const info = poolInfo(L, r.chain_id, f.pool);
          if (!seen.has(info.key)) { seen.add(info.key); venues.push(info); }
        }
        it.venues = venues;
      } else if (r.etype === "liquidity") {
        const m = r.meta || {};
        it.pool = poolInfo(L, r.chain_id, "0x" + m.pool);
        it.direction = m.dir; it.pools = m.pools; it.moves = m.moves;
      }
      return it;
    });
    res.setHeader("Cache-Control", "public, s-maxage=20, stale-while-revalidate=20");
    return res.status(200).json({
      ok: true, page, limit, total, pages: total == null ? null : Math.max(1, Math.ceil(total / limit)),
      count: items.length, mode: "reconciled", types, items,
    });
  }

  async function answer(reconciled) {
  // WHERE clause with numbered parameters (only the filters that are present)
  const params = [];
  const add = (v) => { params.push(v); return "$" + params.length; };
  const where = [];
  if (chainKey !== "all") where.push(`chain_id = ${add(CHAINS[chainKey].id)}`);
  if (address) { const p = add(address); where.push(`(from_addr = ${p} OR to_addr = ${p})`); }
  if (type === "mint") where.push(`from_addr = ${add(ZERO)}`);
  if (type === "burn") where.push(`to_addr = ${add(ZERO)}`);
  if (type === "transfer") { const z = add(ZERO); where.push(`from_addr <> ${z} AND to_addr <> ${z}`); }
  if (from) where.push(`block_time >= ${add(from)}::timestamptz`);
  if (to) where.push(`block_time <= ${add(to)}::timestamptz`);
  if (min) where.push(`amount >= ${add(min)}::numeric`);
  if (max) where.push(`amount <= ${add(max)}::numeric`);
  const whereSql = where.length ? "WHERE " + where.join(" AND ") : "";
  const countParams = params.slice();

  // Page query. dir = "DESC" reads from the newest row; "ASC" reads from the oldest row (used for the far end,
  // so the last pages are as fast as the first ones). The outer ORDER BY always shows newest first.
  const buildPage = (dir, lim, off) => {
    const pp = params.slice();
    const lp = "$" + (pp.push(lim), pp.length);
    const op = "$" + (pp.push(off), pp.length);
    const sql = `
    SELECT p.chain_id, p.block_number, p.block_time, p.log_index,
           encode(p.tx_hash, 'hex')   AS tx_hex,
           encode(p.from_addr, 'hex') AS from_hex,
           encode(p.to_addr, 'hex')   AS to_hex,
           p.amount::text             AS amount_raw,
           fl.label AS from_label, fl.category AS from_category, fl.logo AS from_logo,
           tl.label AS to_label,   tl.category AS to_category, tl.logo AS to_logo
      FROM (SELECT chain_id, block_number, block_time, tx_hash, log_index, from_addr, to_addr, amount
              FROM transfers
              ${whereSql}
             ORDER BY block_time ${dir}, chain_id ${dir}, block_number ${dir}, log_index ${dir}, tx_hash ${dir}
             LIMIT ${lp} OFFSET ${op}) p
      LEFT JOIN LATERAL (SELECT label, category, logo FROM labels
                          WHERE address = p.from_addr AND chain_id IN (0, p.chain_id)
                          ORDER BY chain_id DESC LIMIT 1) fl ON true
      LEFT JOIN LATERAL (SELECT label, category, logo FROM labels
                          WHERE address = p.to_addr AND chain_id IN (0, p.chain_id)
                          ORDER BY chain_id DESC LIMIT 1) tl ON true
     ORDER BY p.block_time DESC, p.chain_id DESC, p.block_number DESC, p.log_index DESC, p.tx_hash DESC`;
    return { sql, params: pp };
  };
  const countSql = `SELECT COUNT(*)::bigint AS n FROM transfers ${whereSql}`;
  const FAST_OFFSET = Number(process.env.EXPLORIZE_FAST_OFFSET || 25000);
  const offset = (page - 1) * limit;

  {
    let total, rows;
    if (offset <= FAST_OFFSET) {
      // near the top: page and count in parallel
      const pq = buildPage("DESC", limit, offset);
      const [r, c] = await Promise.all([query(pq.sql, pq.params), query(countSql, countParams)]);
      total = Number(c.rows[0].n);
      rows = r.rows;
    } else {
      // deep page: count first, then read from whichever end is closer
      const c = await query(countSql, countParams);
      total = Number(c.rows[0].n);
      const end = Math.min(page * limit, total);
      if (end <= offset) rows = [];
      else if (total - end < offset) {
        const pq = buildPage("ASC", end - offset, total - end);
        rows = (await query(pq.sql, pq.params)).rows;
      } else {
        const pq = buildPage("DESC", limit, offset);
        rows = (await query(pq.sql, pq.params)).rows;
      }
    }
    const zeroHex = "0".repeat(40);
    const items = rows.map((row) => {
      const key = CHAIN_BY_ID[row.chain_id];
      return {
        chain: key,
        chain_name: key ? CHAINS[key].name : String(row.chain_id),
        block: Number(row.block_number),
        time: row.block_time.toISOString(),
        tx: "0x" + row.tx_hex,
        log_index: row.log_index,
        from: "0x" + row.from_hex,
        to: "0x" + row.to_hex,
        from_label: labelOf(row.from_label, row.from_category, row.from_logo),
        to_label: labelOf(row.to_label, row.to_category, row.to_logo),
        kind: row.from_hex === zeroHex ? "mint" : row.to_hex === zeroHex ? "burn" : "transfer",
        amount: formatUnits(row.amount_raw),
      };
    });
    res.setHeader("Cache-Control", "public, s-maxage=20, stale-while-revalidate=20");
    return res.status(200).json({
      ok: true,
      page,
      limit,
      total,
      pages: Math.max(1, Math.ceil(total / limit)),
      count: items.length,
      mode: reconciled ? "reconciled" : "raw",
      items,
    });
  }
  }
};
