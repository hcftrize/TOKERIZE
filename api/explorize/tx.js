// GET /api/explorize/tx?chain=base&hash=0x...
// Every RIZE transfer inside one transaction (a swap or a bridge can emit several).
const { CHAINS, CHAIN_BY_ID, query, fail, formatUnits } = require("./_db");

module.exports = async function handler(req, res) {
  if (req.method !== "GET") return res.status(405).json({ error: "method_not_allowed" });
  const q = req.query || {};
  const chainKey = String(q.chain || "").toLowerCase();
  if (!CHAINS[chainKey]) return res.status(400).json({ error: "bad_chain" });
  const hash = String(q.hash || "").trim();
  if (!/^0x[0-9a-fA-F]{64}$/.test(hash)) return res.status(400).json({ error: "bad_hash" });

  try {
    const r = await query(
      `SELECT p.chain_id, p.block_number, p.block_time, p.log_index,
              encode(p.from_addr, 'hex') AS from_hex, encode(p.to_addr, 'hex') AS to_hex,
              p.amount::text AS amount_raw,
              fl.label AS from_label, fl.category AS from_category,
              tl.label AS to_label,   tl.category AS to_category
         FROM transfers p
         LEFT JOIN LATERAL (SELECT label, category FROM labels
                             WHERE address = p.from_addr AND chain_id IN (0, p.chain_id)
                             ORDER BY chain_id DESC LIMIT 1) fl ON true
         LEFT JOIN LATERAL (SELECT label, category FROM labels
                             WHERE address = p.to_addr AND chain_id IN (0, p.chain_id)
                             ORDER BY chain_id DESC LIMIT 1) tl ON true
        WHERE p.chain_id = $1 AND p.tx_hash = $2
        ORDER BY p.log_index`,
      [CHAINS[chainKey].id, Buffer.from(hash.slice(2), "hex")]);
    if (r.rows.length === 0) {
      res.setHeader("Cache-Control", "no-store");
      return res.status(404).json({ error: "not_found" });
    }
    const zeroHex = "0".repeat(40);
    const items = r.rows.map((row) => ({
      log_index: row.log_index,
      from: "0x" + row.from_hex,
      to: "0x" + row.to_hex,
      from_label: row.from_label ? { label: row.from_label, category: row.from_category } : null,
      to_label: row.to_label ? { label: row.to_label, category: row.to_category } : null,
      kind: row.from_hex === zeroHex ? "mint" : row.to_hex === zeroHex ? "burn" : "transfer",
      amount: formatUnits(row.amount_raw),
    }));
    const total = r.rows.reduce((s, row) => s + BigInt(row.amount_raw), 0n);
    res.setHeader("Cache-Control", "public, s-maxage=300, stale-while-revalidate=600");
    return res.status(200).json({
      ok: true,
      chain: CHAIN_BY_ID[r.rows[0].chain_id],
      chain_name: CHAINS[chainKey].name,
      tx: hash.toLowerCase(),
      block: Number(r.rows[0].block_number),
      time: r.rows[0].block_time.toISOString(),
      transfers: items.length,
      total_amount: formatUnits(total),
      items,
    });
  } catch (err) {
    return fail(res, err);
  }
};
