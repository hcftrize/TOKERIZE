// GET /api/explorize/transfers?chain=all|base|ethereum|bsc|polygon&type=all|transfer|mint|burn
//                             &address=0x...&limit=25&cursor=...
// Newest first. Pagination uses a cursor (keyset), so deep pages stay fast.
const { CHAINS, CHAIN_BY_ID, ZERO, query, fail, parseAddress, formatUnits } = require("./_db");

function encodeCursor(row) {
  const parts = [row.block_time.toISOString(), row.chain_id, String(row.block_number), row.log_index, row.tx_hex];
  return Buffer.from(JSON.stringify(parts)).toString("base64url");
}
function decodeCursor(text) {
  try {
    const [time, chainId, block, logIndex, txHex] = JSON.parse(Buffer.from(String(text), "base64url").toString());
    if (isNaN(Date.parse(time)) || !Number.isInteger(chainId) || !/^\d+$/.test(String(block)) ||
        !Number.isInteger(logIndex) || !/^[0-9a-f]{64}$/.test(txHex)) return null;
    return { time, chainId, block: String(block), logIndex, tx: Buffer.from(txHex, "hex") };
  } catch (_) {
    return null;
  }
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
  const limit = Math.min(Math.max(parseInt(q.limit, 10) || 25, 1), 100);
  let cursor = null;
  if (q.cursor) {
    cursor = decodeCursor(q.cursor);
    if (!cursor) return res.status(400).json({ error: "bad_cursor" });
  }

  // Build the WHERE clause with numbered parameters (only the filters that are present).
  const params = [];
  const add = (v) => { params.push(v); return "$" + params.length; };
  const where = [];
  if (chainKey !== "all") where.push(`chain_id = ${add(CHAINS[chainKey].id)}`);
  if (address) { const p = add(address); where.push(`(from_addr = ${p} OR to_addr = ${p})`); }
  if (type === "mint") where.push(`from_addr = ${add(ZERO)}`);
  if (type === "burn") where.push(`to_addr = ${add(ZERO)}`);
  if (type === "transfer") { const z = add(ZERO); where.push(`from_addr <> ${z} AND to_addr <> ${z}`); }
  if (cursor) {
    const t = add(cursor.time);
    where.push(`block_time <= ${t}::timestamptz`);
    where.push(`(block_time, chain_id, block_number, log_index, tx_hash) < ` +
      `(${t}::timestamptz, ${add(cursor.chainId)}::int, ${add(cursor.block)}::bigint, ${add(cursor.logIndex)}::int, ${add(cursor.tx)}::bytea)`);
  }
  const limitParam = add(limit + 1);

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
              ${where.length ? "WHERE " + where.join(" AND ") : ""}
             ORDER BY block_time DESC, chain_id DESC, block_number DESC, log_index DESC, tx_hash DESC
             LIMIT ${limitParam}) p
      LEFT JOIN LATERAL (SELECT label, category FROM labels
                          WHERE address = p.from_addr AND chain_id IN (0, p.chain_id)
                          ORDER BY chain_id DESC LIMIT 1) fl ON true
      LEFT JOIN LATERAL (SELECT label, category FROM labels
                          WHERE address = p.to_addr AND chain_id IN (0, p.chain_id)
                          ORDER BY chain_id DESC LIMIT 1) tl ON true
     ORDER BY p.block_time DESC, p.chain_id DESC, p.block_number DESC, p.log_index DESC, p.tx_hash DESC`;

  try {
    const r = await query(sql, params);
    const more = r.rows.length > limit;
    const rows = more ? r.rows.slice(0, limit) : r.rows;
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
      count: items.length,
      next_cursor: more ? encodeCursor({ ...rows[rows.length - 1], tx_hex: rows[rows.length - 1].tx_hex }) : null,
      items,
    });
  } catch (err) {
    return fail(res, err);
  }
};
