// GET /api/explorize/wallet?address=0x...            -> balance per chain + governance + label
// GET /api/explorize/wallet?address=0x...&view=history  -> RIZE balance, one point per day (all chains together),
//                                                          rebuilt from the transfers (for the balance chart)
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
  if (String((req.query || {}).view || "") === "history") return history(res, address, hex);

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
      query(`SELECT label, category, logo FROM labels WHERE address = $1 ORDER BY chain_id DESC LIMIT 1`, [address]),
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
      label: lab.rows[0] ? { label: lab.rows[0].label, category: lab.rows[0].category, logo: lab.rows[0].logo || null } : null,
      status: chains.length === 0 ? "no_activity" : anyError ? "error" : "ok",
      total_balance: chains.length === 0 || anyError ? null : formatUnits(total),
      transfers: chains.reduce((s, c) => s + c.transfers, 0),
      chains,
    });
  } catch (err) {
    return fail(res, err);
  }
};

// Daily RIZE balance of the address over every indexed chain: the net of each day, accumulated, then one point per day
// from the first day to today. If the accumulated balance dips below zero (a router or bridge moved tokens the transfers
// cannot explain) the answer says so (approximate: true) and the curve is lifted so it never shows a negative balance.
async function history(res, address, hex) {
  try {
    const r = await query(
      `SELECT to_char(date_trunc('day', block_time AT TIME ZONE 'UTC'), 'YYYY-MM-DD') AS d,
              (COALESCE(SUM(amount) FILTER (WHERE to_addr = $1), 0)
               - COALESCE(SUM(amount) FILTER (WHERE from_addr = $1), 0))::text AS net
         FROM transfers WHERE from_addr = $1 OR to_addr = $1
        GROUP BY 1 ORDER BY 1`, [address]);
    const days = r.rows;
    const points = [];
    let approximate = false;
    if (days.length) {
      const DAY = 86400000;
      const start = Date.parse(days[0].d + "T00:00:00Z");
      const today = Math.floor(Date.now() / DAY) * DAY;
      const net = new Map(days.map((x) => [x.d, BigInt(x.net)]));
      let acc = 0n, floor = 0n;
      const raw = [];
      for (let t = start; t <= Math.max(today, start); t += DAY) {
        const key = new Date(t).toISOString().slice(0, 10);
        acc += net.get(key) || 0n;
        if (acc < floor) floor = acc;
        raw.push([key, acc]);
      }
      approximate = floor < 0n;
      for (const [key, v] of raw) points.push({ t: key, balance: formatUnits(v - floor) });
    }
    res.setHeader("Cache-Control", "public, s-maxage=60, stale-while-revalidate=300");
    return res.status(200).json({ ok: true, address: "0x" + hex, approximate, points });
  } catch (err) {
    return fail(res, err);
  }
}
