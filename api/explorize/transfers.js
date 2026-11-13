// GET /api/explorize/transfers
//   chain=all|base|ethereum|bsc|polygon   type=all|transfer|mint|burn   address=0x...
//   from=2026-11-01 | 2026-11-01T08:30      (UTC, inclusive)      to=... (UTC, inclusive)
//   min=1000  max=50000                      (amount in RIZE, inclusive)
//   page=1 (1-based)   limit=25 (max 100)
// Newest first. Answers with the page of rows plus the exact total, so the page can show "1 / N".
const { CHAINS, CHAIN_BY_ID, ZERO, query, fail, parseAddress, formatUnits } = require("./_db");

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
  const limit = Math.min(Math.max(parseInt(q.limit, 10) || 25, 1), 100);
  const page = Math.min(Math.max(parseInt(q.page, 10) || 1, 1), MAX_PAGE);

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
           fl.label AS from_label, fl.category AS from_category,
           tl.label AS to_label,   tl.category AS to_category
      FROM (SELECT chain_id, block_number, block_time, tx_hash, log_index, from_addr, to_addr, amount
              FROM transfers
              ${whereSql}
             ORDER BY block_time ${dir}, chain_id ${dir}, block_number ${dir}, log_index ${dir}, tx_hash ${dir}
             LIMIT ${lp} OFFSET ${op}) p
      LEFT JOIN LATERAL (SELECT label, category FROM labels
                          WHERE address = p.from_addr AND chain_id IN (0, p.chain_id)
                          ORDER BY chain_id DESC LIMIT 1) fl ON true
      LEFT JOIN LATERAL (SELECT label, category FROM labels
                          WHERE address = p.to_addr AND chain_id IN (0, p.chain_id)
                          ORDER BY chain_id DESC LIMIT 1) tl ON true
     ORDER BY p.block_time DESC, p.chain_id DESC, p.block_number DESC, p.log_index DESC, p.tx_hash DESC`;
    return { sql, params: pp };
  };
  const countSql = `SELECT COUNT(*)::bigint AS n FROM transfers ${whereSql}`;
  const FAST_OFFSET = Number(process.env.EXPLORIZE_FAST_OFFSET || 25000);
  const offset = (page - 1) * limit;

  try {
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
        from_label: row.from_label ? { label: row.from_label, category: row.from_category } : null,
        to_label: row.to_label ? { label: row.to_label, category: row.to_category } : null,
        kind: row.from_hex === zeroHex ? "mint" : row.to_hex === zeroHex ? "burn" : "transfer",
        amount: formatUnits(row.amount_raw),
      };
    });
    res.setHeader("Cache-Control", "public, s-maxage=15, stale-while-revalidate=60");
    return res.status(200).json({
      ok: true,
      page,
      limit,
      total,
      pages: Math.max(1, Math.ceil(total / limit)),
      count: items.length,
      items,
    });
  } catch (err) {
    return fail(res, err);
  }
};
