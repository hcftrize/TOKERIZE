// "Who" filters shared by Transfers and Trades: a wallet address, or a whole entity ("Kraken" = every address tagged Kraken).
// The leading underscore keeps Vercel from exposing this file as an endpoint (and from counting it as a function).
const { query } = require("./_db");

const ADDR = /^0x[0-9a-fA-F]{40}$/;
const likeEscape = (s) => s.replace(/[\\%_]/g, (c) => "\\" + c);

// "0xabc…" | "entity:Kraken" | "" -> null (no filter) | false (not understood) |
//   { label, addrs: Buffer[]  (addresses that hold or move the tokens),
//     pools: string[]         (pool keys as "0x…" text, to match the venue side of a trade),
//     poolBufs: Buffer[]      (the same as bytes, to match liquidity moves) }
async function resolveParty(v) {
  if (v == null || String(v).trim() === "") return null;
  const s = String(v).trim();
  if (ADDR.test(s)) {
    const hex = s.slice(2).toLowerCase();
    return { label: s.toLowerCase(), addrs: [Buffer.from(hex, "hex")], pools: ["0x" + hex], poolBufs: [Buffer.from(hex, "hex")] };
  }
  const m = /^entity:(.{1,60})$/s.exec(s);
  if (!m) return false;
  const name = m[1].trim();
  const [a, p] = await Promise.all([
    query("SELECT address FROM labels WHERE entity = $1", [name]),
    // the pools of a trading venue belong to the entity named like the venue; they hold their tokens in the pool itself, or
    // in the shared manager / vault of a singleton design
    query(`SELECT p.pool_key,
                  CASE WHEN octet_length(p.pool_key) = 20 THEN COALESCE(p.holder, p.pool_key) ELSE COALESCE(p.holder, p.manager) END AS holder
             FROM pools p JOIN venues v ON v.key = p.venue WHERE v.name = $1`, [name]),
  ]);
  const addrs = new Map(), poolBufs = [], pools = [];
  for (const r of a.rows) addrs.set(Buffer.from(r.address).toString("hex"), Buffer.from(r.address));
  for (const r of p.rows) {
    const k = Buffer.from(r.pool_key);
    poolBufs.push(k); pools.push("0x" + k.toString("hex"));
    if (r.holder) addrs.set(Buffer.from(r.holder).toString("hex"), Buffer.from(r.holder));
  }
  return { label: name, addrs: [...addrs.values()], pools, poolBufs };
}

// Search box of the quick filters: entities first (with how many addresses they have), then individual labelled wallets.
async function suggest(text) {
  const t = String(text || "").trim().slice(0, 42);
  if (t.length < 2) return { entities: [], wallets: [] };
  const like = "%" + likeEscape(t.toLowerCase()) + "%";
  const hexPrefix = /^0x[0-9a-fA-F]{2,40}$/.test(t) ? likeEscape(t.slice(2).toLowerCase()) + "%" : null;
  const [ents, venues, wallets] = await Promise.all([
    query(`SELECT entity AS name, COUNT(*)::int AS n, MIN(category) AS category, MIN(logo) AS logo FROM labels
            WHERE entity IS NOT NULL AND entity <> '' AND lower(entity) LIKE $1 ESCAPE '\\' GROUP BY entity ORDER BY n DESC, entity LIMIT 6`, [like]),
    query(`SELECT v.name, COUNT(p.*)::int AS n, MIN(v.logo) AS logo FROM venues v JOIN pools p ON p.venue = v.key
            WHERE lower(v.name) LIKE $1 ESCAPE '\\' GROUP BY v.name ORDER BY v.name LIMIT 4`, [like]),
    query(`SELECT encode(address,'hex') AS a, label, category, logo, entity FROM labels
            WHERE category NOT IN ('pool') AND (lower(label) LIKE $1 ESCAPE '\\'
                  ${hexPrefix ? "OR encode(address,'hex') LIKE $2" : ""})
            GROUP BY address, label, category, logo, entity ORDER BY label LIMIT 8`, hexPrefix ? [like, hexPrefix] : [like]),
  ]);
  const byName = new Map();
  for (const r of ents.rows) byName.set(r.name.toLowerCase(), { name: r.name, addresses: r.n, category: r.category, logo: r.logo || null });
  for (const r of venues.rows) {
    const e = byName.get(r.name.toLowerCase());
    if (e) e.addresses += r.n; else byName.set(r.name.toLowerCase(), { name: r.name, addresses: r.n, category: "dex", logo: r.logo ? r.logo.replace(/\.[a-z]+$/i, "") : null });
  }
  return {
    entities: [...byName.values()].sort((x, y) => y.addresses - x.addresses).slice(0, 6),
    wallets: wallets.rows.map((r) => ({ address: "0x" + r.a, label: r.label, category: r.category, logo: r.logo || null, entity: r.entity || null })),
  };
}

module.exports = { resolveParty, suggest };
