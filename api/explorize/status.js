// GET /api/explorize/status  -> indexing state of every chain (also the connection test)
const { CHAINS, CHAIN_ORDER, CHAIN_BY_ID, query, fail } = require("./_db");

module.exports = async function handler(req, res) {
  if (req.method !== "GET") return res.status(405).json({ error: "method_not_allowed" });
  try {
    const r = await query(
      `SELECT cs.chain_id, cs.last_block, cs.last_synced_at,
              t.n AS transfers, t.first_time, t.last_time
         FROM chain_state cs
         LEFT JOIN (SELECT chain_id, COUNT(*) AS n, MIN(block_time) AS first_time, MAX(block_time) AS last_time
                      FROM transfers GROUP BY chain_id) t USING (chain_id)`);
    const byKey = {};
    for (const row of r.rows) {
      const key = CHAIN_BY_ID[row.chain_id];
      if (!key) continue;
      byKey[key] = {
        chain: key,
        name: CHAINS[key].name,
        last_block: row.last_block == null ? null : Number(row.last_block),
        last_synced_at: row.last_synced_at ? row.last_synced_at.toISOString() : null,
        transfers: Number(row.transfers || 0),
        first_transfer_at: row.first_time ? row.first_time.toISOString() : null,
        last_transfer_at: row.last_time ? row.last_time.toISOString() : null,
      };
    }
    const chains = CHAIN_ORDER.filter((k) => byKey[k]).map((k) => byKey[k]);
    res.setHeader("Cache-Control", "public, s-maxage=60, stale-while-revalidate=300");
    return res.status(200).json({
      ok: true,
      now: new Date().toISOString(),
      total_transfers: chains.reduce((s, c) => s + c.transfers, 0),
      chains,
    });
  } catch (err) {
    return fail(res, err);
  }
};
