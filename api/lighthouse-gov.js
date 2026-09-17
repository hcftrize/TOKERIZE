// api/lighthouse-gov.js — Vercel Serverless Function
// Server-side proxy for the canton-ecosystem.html governance widget.
// Lighthouse now requires "Authorization: Bearer <key>" on every request
// (enforcement went live 2026-09-16). LIGHTHOUSE_KEY can never be embedded
// in canton-ecosystem.html's client-side JS (it would be visible to anyone
// viewing page source), so this proxy attaches it server-side instead —
// same pattern as api/coingecko.js and api/coinmarketcap.js.
//
// Usage from the browser:
//   /api/lighthouse-gov?path=/governance/stats
//   /api/lighthouse-gov?path=/governance

const LIGHTHOUSE_BASE = "https://lighthouse.cantonloop.com/api";

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  const { path, ...params } = req.query;
  if (!path) {
    return res.status(400).json({ error: "Missing path parameter" });
  }
  // Only ever proxy to Lighthouse's own governance endpoints — never let an
  // arbitrary path turn this into an open relay for the API key.
  if (!path.startsWith("/governance")) {
    return res.status(400).json({ error: "Unsupported path" });
  }

  const query = new URLSearchParams(params).toString();
  const url = `${LIGHTHOUSE_BASE}${path}${query ? "?" + query : ""}`;

  try {
    const headers = { "Accept": "application/json" };
    if (process.env.LIGHTHOUSE_KEY) {
      headers["Authorization"] = `Bearer ${process.env.LIGHTHOUSE_KEY}`;
    }
    const upstream = await fetch(url, { headers });
    const data = await upstream.json();
    res.setHeader("Cache-Control", "public, s-maxage=30, stale-while-revalidate=60");
    return res.status(upstream.status).json(data);
  } catch (err) {
    return res.status(500).json({ error: "Proxy error", detail: err.message });
  }
}
