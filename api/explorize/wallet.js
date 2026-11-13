// GET /api/explorize/wallet?address=0x...
// Balance per chain rebuilt from the indexed Transfer events (received - sent).
// If the rebuilt balance is negative the transfers alone cannot explain it (routers, bridges...):
// the answer then says "error" instead of showing a wrong number.
const { CHAINS, CHAIN_ORDER, CHAIN_BY_ID, ZERO, query, fail, parseAddress, formatUnits } = require("./_db");
const { loadBonds, summarize } = require("./_bonds");

module.exports = async function handler(req, res) {
  if (req.method !== "GET") return res.status(405).json({ error: "method_not_allowed" });
  const address = parseAddress((req.query || {}).address);
  if (!address) return res.status(400).json({ error: "bad_address" });
  const hex = address.toString("hex");

  try {
    const [agg, lab] = await Promise.all([
      query(
        `SELECT chain_id,
                COALESCE(SUM(amount) FILTER (WHERE to_addr = $1), 0)::text   AS received,
                COALESCE(SUM(amount) FILTER (WHERE from_addr = $1), 0)::text AS sent,
                COUNT(*)::int AS transfers,
                MIN(block_time) AS first_seen, MAX(block_time) AS last_seen
           FROM transfers
          WHERE from_addr = $1 OR to_addr = $1
          GROUP BY chain_id`, [address]),
      query(`SELECT label, category FROM labels WHERE address = $1 ORDER BY chain_id DESC LIMIT 1`, [address]),
    ]);

    const byKey = {};
    for (const row of agg.rows) {
      const key = CHAIN_BY_ID[row.chain_id];
      if (!key) continue;
      const received = BigInt(row.received), sent = BigInt(row.sent);
      const balance = received - sent;
      byKey[key] = {
        chain: key,
        name: CHAINS[key].name,
        received: formatUnits(received),
        sent: formatUnits(sent),
        balance: balance < 0n ? null : formatUnits(balance),
        balance_status: balance < 0n ? "error" : "ok",
        _balance: balance,
        transfers: row.transfers,
        first_seen: row.first_seen.toISOString(),
        last_seen: row.last_seen.toISOString(),
      };
    }
    const chains = CHAIN_ORDER.filter((k) => byKey[k]).map((k) => byKey[k]);
    const anyError = chains.some((c) => c.balance_status === "error");
    const total = chains.reduce((s, c) => s + c._balance, 0n);
    chains.forEach((c) => delete c._balance);

    // Governance bonds + rank in the Top Holders (written once a day by the holders job).
    // Optional: if those tables do not exist yet, the wallet page simply has no governance block.
    let governance = null, rank = null;
    try {
      const nowSec = Math.floor(Date.now() / 1000);
      const [bondRows, rk] = await Promise.all([
        loadBonds([address]),
        query(`SELECT rank FROM holders WHERE address = $1`, [address]),
      ]);
      if (bondRows.length) governance = summarize(bondRows, nowSec);
      rank = rk.rows[0] ? rk.rows[0].rank : null;
    } catch (_) { /* governance data not available yet */ }

    res.setHeader("Cache-Control", "public, s-maxage=30, stale-while-revalidate=120");
    return res.status(200).json({
      ok: true,
      address: "0x" + hex,
      governance,
      rank,
      is_zero_address: address.equals(ZERO),
      label: lab.rows[0] ? { label: lab.rows[0].label, category: lab.rows[0].category } : null,
      status: chains.length === 0 ? "no_activity" : anyError ? "error" : "ok",
      total_balance: chains.length === 0 || anyError ? null : formatUnits(total),
      transfers: chains.reduce((s, c) => s + c.transfers, 0),
      chains,
    });
  } catch (err) {
    return fail(res, err);
  }
};
