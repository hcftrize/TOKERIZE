// Shared helpers for governance bonds (read from the `bonds` table written by the holders job).
// The leading underscore keeps Vercel from exposing this file as an endpoint.
const { query, formatUnits } = require("./_db");

// bonds of the given owners (Buffer[]), oldest id first
async function loadBonds(owners) {
  if (!owners.length) return [];
  const r = await query(
    `SELECT nft_id, encode(owner, 'hex') AS owner_hex, bonded::text AS bonded, queued::text AS queued,
            vested_locked::text AS vested_locked, release_queue, vesting
       FROM bonds WHERE owner = ANY($1::bytea[]) ORDER BY nft_id`, [owners]);
  return r.rows;
}

// One bond row -> the shape the page uses. `nowSec` decides, entry by entry, whether a release is waiting
// (the 7-day unbonding delay is still running) or ready ("releasable": the delay is over, not yet claimed).
function shapeBond(row, nowSec) {
  const queue = (Array.isArray(row.release_queue) ? row.release_queue : []).map((e) => ({
    amount: formatUnits(e.amount),
    time: Number(e.time),
    ready: Number(e.time) <= nowSec,
    _raw: BigInt(e.amount),
  }));
  let vesting = null;
  if (row.vesting && BigInt(row.vested_locked) > 0n) {
    const v = row.vesting;
    const start = Number(v.start), cliff = Number(v.cliff), dur = Number(v.vesting);
    vesting = {
      amount: formatUnits(v.amount),               // total amount of the schedule
      locked: formatUnits(row.vested_locked),      // still locked now (part of the bonded amount)
      start,
      cliff_end: start + cliff,                    // nothing unlocks before this
      end: start + cliff + dur,                    // everything is unlocked from here, linearly in between
      initiator: v.initiator || null,
    };
  }
  return {
    id: row.nft_id,
    bonded: formatUnits(row.bonded),
    queued: formatUnits(row.queued),
    queue: queue.map(({ _raw, ...e }) => e),
    vesting,
    _queue: queue,
  };
}

// All bonds of ONE owner -> totals + pending-release summary
function summarize(rows, nowSec) {
  let bonded = 0n, queued = 0n, vestedLocked = 0n, waiting = 0n, ready = 0n, nextRelease = null;
  const bonds = rows.map((row) => {
    const b = shapeBond(row, nowSec);
    bonded += BigInt(row.bonded);
    queued += BigInt(row.queued);
    vestedLocked += BigInt(row.vested_locked);
    for (const e of b._queue) {
      if (e.ready) ready += e._raw;
      else {
        waiting += e._raw;
        if (nextRelease == null || e.time < nextRelease) nextRelease = e.time;
      }
    }
    delete b._queue;
    return b;
  });
  return {
    bond_count: bonds.length,
    bonded_active: formatUnits(bonded),
    queued: formatUnits(queued),
    locked: formatUnits(bonded + queued),          // everything the wallet still has inside the contract
    vested_locked: formatUnits(vestedLocked),
    has_vesting: vestedLocked > 0n,
    pending: { waiting: formatUnits(waiting), ready: formatUnits(ready), next_release: nextRelease },
    bonds,
  };
}

module.exports = { loadBonds, shapeBond, summarize };
