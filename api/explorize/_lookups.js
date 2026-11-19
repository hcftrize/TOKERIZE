// Shared lookups for the market layer (tokens, pools, venues) and small helpers used by several endpoints.
// The leading underscore keeps Vercel from exposing this file as an endpoint (and from counting it as a function).
const { CHAIN_BY_ID, query, formatUnits } = require("./_db");

// ---- small cache for the lookup tables (they change only when the labels files change) --------------------------
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
// label object shown by the page: the name, its category and the logo file name (without extension) when there is one
const labelOf = (label, category, logo) => (label ? { label, category, logo: logo || null } : null);

// A CCIP network as the page shows it. Indexed chains keep their key; the others (Canton...) come from the API name.
const NET_NAMES = { canton: "Canton", "canton-testnet": "Canton Testnet" };
function netInfo(chainId, rawName) {
  if (CHAIN_BY_ID[chainId]) return { key: CHAIN_BY_ID[chainId], indexed: true };
  const n = String(rawName || "").toLowerCase();
  let key = "other";
  if (n.includes("canton")) key = n.includes("test") ? "canton-testnet" : "canton";
  else if (n) key = n.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "other";
  const name = NET_NAMES[key] || (rawName ? String(rawName) : "Other");
  return { key, name, indexed: false };
}

module.exports = { getLookups, tokenOf, counterList, poolInfo, chainKey, labelOf, netInfo };
