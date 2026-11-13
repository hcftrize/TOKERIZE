// Shared helpers for the Explorize API (read-only access to the Explorize database).
// The leading underscore keeps Vercel from exposing this file as an endpoint.
const { Pool } = require("pg");

const CHAINS = {
  base:     { id: 8453, name: "Base" },
  ethereum: { id: 1,    name: "Ethereum" },
  bsc:      { id: 56,   name: "BNB Chain" },
  polygon:  { id: 137,  name: "Polygon" },
};
const CHAIN_ORDER = ["base", "ethereum", "bsc", "polygon"];
const CHAIN_BY_ID = Object.fromEntries(Object.entries(CHAINS).map(([k, v]) => [v.id, k]));
const ZERO = Buffer.alloc(20);
const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;

let pool = null;
function getPool() {
  if (pool) return pool;
  const url = process.env.EXPLORIZE_DATABASE_URL;
  if (!url) {
    const e = new Error("EXPLORIZE_DATABASE_URL is not set");
    e.code = "NO_DB_URL";
    throw e;
  }
  pool = new Pool({ connectionString: url, max: 3, idleTimeoutMillis: 10000, connectionTimeoutMillis: 8000 });
  pool.on("error", () => {}); // an idle connection dropped by the server must not crash the function
  return pool;
}

// One read-only transaction per query, with a time limit (works through Neon's pooler).
async function query(sql, params = []) {
  const client = await getPool().connect();
  try {
    await client.query("BEGIN READ ONLY");
    await client.query("SET LOCAL statement_timeout = 6000");
    const r = await client.query(sql, params);
    await client.query("COMMIT");
    return r;
  } catch (e) {
    try { await client.query("ROLLBACK"); } catch (_) { /* connection already gone */ }
    throw e;
  } finally {
    client.release();
  }
}

// Turn any error into a clean JSON answer. Never echoes the connection string.
function fail(res, err) {
  const code = err && err.code ? String(err.code) : "";
  let status = 500, error = "server_error";
  if (code === "NO_DB_URL") { error = "database_not_configured"; }
  else if (code === "57014") { status = 504; error = "query_too_slow"; }
  else if (code === "28P01" || code === "28000") { status = 503; error = "database_login_failed"; }
  else if (code === "42501") { status = 503; error = "database_permission_denied"; }
  else if (["ENOTFOUND", "ECONNREFUSED", "ECONNRESET", "ETIMEDOUT"].includes(code) ||
           /timeout|terminated|connection/i.test((err && err.message) || "")) {
    status = 503; error = "database_unreachable";
  }
  res.setHeader("Cache-Control", "no-store");
  return res.status(status).json({ error, code: code || undefined });
}

function parseAddress(value) {
  if (value == null || value === "") return null;
  const s = String(value).trim();
  if (!ADDRESS_RE.test(s)) return false;
  return Buffer.from(s.slice(2), "hex");
}

// raw token units (18 decimals) -> exact decimal string, e.g. "1234.5"
function formatUnits(raw, decimals = 18) {
  let v = BigInt(raw);
  const neg = v < 0n;
  if (neg) v = -v;
  const base = 10n ** BigInt(decimals);
  const whole = v / base;
  const frac = (v % base).toString().padStart(decimals, "0").replace(/0+$/, "");
  return (neg ? "-" : "") + whole.toString() + (frac ? "." + frac : "");
}

module.exports = { CHAINS, CHAIN_ORDER, CHAIN_BY_ID, ZERO, query, fail, parseAddress, formatUnits };
