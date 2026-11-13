// GET /api/explorize/holders?page=1&limit=50
// The RIZE Top Holders ranking (top 1000), rebuilt once a day by the holders job from the chains themselves:
//   held   = live RIZE balance on all chains     bonded = still inside the governance contract (Base)
//   total  = held + bonded                        (bonded includes RIZE queued for release)
// Whether a queued release is still waiting or already releasable is decided here, at request time.
const { query, fail, formatUnits } = require("./_db");
const { loadBonds, summarize } = require("./_bonds");

const NOT_BUILT = (res, page, limit) => {
  res.setHeader("Cache-Control", "public, s-maxage=30, stale-while-revalidate=60");
  return res.status(200).json({ ok: true, built: false, page, limit, total: 0, pages: 1, count: 0, items: [], meta: null });
};

module.exports = async function handler(req, res) {
  if (req.method !== "GET") return res.status(405).json({ error: "method_not_allowed" });
  const q = req.query || {};
  const limit = Math.min(Math.max(parseInt(q.limit, 10) || 50, 1), 100);
  const page = Math.min(Math.max(parseInt(q.page, 10) || 1, 1), 1000);
  const offset = (page - 1) * limit;
  const nowSec = Math.floor(Date.now() / 1000);

  try {
    const [metaR, countR, pageR] = await Promise.all([
      query(`SELECT updated_at, snapshot_time, base_block, holders, addresses_read, bonds_active,
                    invariant_diff::text AS invariant_diff FROM holders_meta WHERE id = 1`),
      query(`SELECT COUNT(*)::int AS n FROM holders`),
      query(`SELECT h.rank, encode(h.address, 'hex') AS addr, h.held::text AS held, h.bonded::text AS bonded,
                    h.queued::text AS queued, h.bond_count, h.total::text AS total, l.label, l.category
               FROM holders h
               LEFT JOIN LATERAL (SELECT label, category FROM labels WHERE address = h.address
                                   ORDER BY (chain_id = 0) DESC, chain_id LIMIT 1) l ON true
              ORDER BY h.rank LIMIT $1 OFFSET $2`, [limit, offset]),
    ]);
    const meta = metaR.rows[0];
    if (!meta) return NOT_BUILT(res, page, limit);
    const total = countR.rows[0].n;

    const withBonds = pageR.rows.filter((r) => r.bond_count > 0).map((r) => Buffer.from(r.addr, "hex"));
    const bondRows = await loadBonds(withBonds);
    const byOwner = {};
    for (const b of bondRows) (byOwner[b.owner_hex] = byOwner[b.owner_hex] || []).push(b);

    const items = pageR.rows.map((r) => {
      const gov = r.bond_count > 0 ? summarize(byOwner[r.addr] || [], nowSec) : null;
      return {
        rank: r.rank,
        address: "0x" + r.addr,
        label: r.label ? { label: r.label, category: r.category } : null,
        total: formatUnits(r.total),
        held: formatUnits(r.held),
        bonded: formatUnits(BigInt(r.bonded) + BigInt(r.queued)),   // all RIZE still inside the contract
        bond_count: r.bond_count,
        has_vesting: gov ? gov.has_vesting : false,
        governance: gov,
      };
    });

    res.setHeader("Cache-Control", "public, s-maxage=60, stale-while-revalidate=300");
    return res.status(200).json({
      ok: true,
      built: true,
      page,
      limit,
      total,
      pages: Math.max(1, Math.ceil(total / limit)),
      count: items.length,
      items,
      meta: {
        updated_at: meta.updated_at.toISOString(),
        snapshot_time: meta.snapshot_time ? meta.snapshot_time.toISOString() : null,
        base_block: meta.base_block == null ? null : Number(meta.base_block),
        holders_kept: meta.holders,
        bonds_active: meta.bonds_active,
        contract_adds_up: meta.invariant_diff != null && BigInt(meta.invariant_diff) === 0n,
      },
    });
  } catch (err) {
    if (err && err.code === "42P01") return NOT_BUILT(res, page, limit);   // table not created yet (job never ran)
    return fail(res, err);
  }
};
