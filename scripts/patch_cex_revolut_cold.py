"""
patch_cex_revolut_cold.py
=========================
One-time patch script — adds Revolut Cold wallet balance to all
existing cex[] data points in conviction-history.json since genesis.

Does NOT touch bonded[], whales[], unbonding[] or metadata.

Run once via GitHub Actions (workflow_dispatch) or locally:
    python patch_cex_revolut_cold.py
"""

import json, os, time, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RIZE_TOKEN       = '0x9818B6c09f5ECc843060927E8587c427C7C93583'
DECIMALS         = 1e18
OUTPUT_FILE      = Path('rize-data-hub/conviction-history.json')
ALCHEMY_URL      = os.environ.get(
    'ALCHEMY_RPC_URL',
    'https://base-mainnet.g.alchemy.com/v2/qS-QZnHMq-cqmoFkw-grY'
)
GENESIS_BLOCK    = '0x1CE2E5E'  # first governance contract tx block

REVOLUT_COLD     = '0x15Da7556D5ED888306839bed06f868AeaeDCb0d7'
REVOLUT_COLD_KEY = 'Revolut Cold'


def rpc(method, params):
    payload = json.dumps({
        'jsonrpc': '2.0', 'id': 1,
        'method': method, 'params': params
    }).encode()
    req = urllib.request.Request(
        ALCHEMY_URL, data=payload,
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f'  RPC error: {e}')
        return None


def fetch_transfers(from_addr=None, to_addr=None, label=''):
    """Fetch all ERC20 RIZE transfers for an address via alchemy_getAssetTransfers."""
    all_txs = []
    page_key = None
    while True:
        params = {
            'fromBlock'        : GENESIS_BLOCK,
            'toBlock'          : 'latest',
            'contractAddresses': [RIZE_TOKEN],
            'category'         : ['erc20'],
            'withMetadata'     : True,
            'excludeZeroValue' : True,
            'maxCount'         : '0x3e8',
            'order'            : 'asc',
        }
        if from_addr: params['fromAddress'] = from_addr
        if to_addr:   params['toAddress']   = to_addr
        if page_key:  params['pageKey']     = page_key

        res = rpc('alchemy_getAssetTransfers', [params])
        if not res or 'result' not in res:
            break
        batch = res['result'].get('transfers', [])
        all_txs.extend(batch)
        page_key = res['result'].get('pageKey')
        if not page_key:
            break
        time.sleep(0.2)

    print(f'  {label}: {len(all_txs)} transfers found')
    return all_txs


def tx_date(tx):
    ts = tx.get('metadata', {}).get('blockTimestamp', '')
    return ts[:10] if ts else None


def tx_value(tx):
    v = tx.get('value')
    return float(v) if v else 0.0


def get_live_balance(address):
    """Get current live token balance via balanceOf."""
    padded = '000000000000000000000000' + address[2:].lower()
    res = rpc('eth_call', [{'to': RIZE_TOKEN, 'data': '0x70a08231' + padded}, 'latest'])
    if not res or not res.get('result') or res['result'] == '0x':
        return 0.0
    try:
        return int(res['result'], 16) / DECIMALS
    except Exception:
        return 0.0


def main():
    # ── Load existing JSON ────────────────────────────────────────────────────
    if not OUTPUT_FILE.exists():
        print('ERROR: conviction-history.json not found.')
        return

    history = json.loads(OUTPUT_FILE.read_text(encoding='utf-8'))
    cex_points = history.get('cex', [])
    if not cex_points:
        print('ERROR: no cex[] data points found in JSON.')
        return

    print(f'Loaded {len(cex_points)} existing cex points '
          f'({cex_points[0]["date"]} → {cex_points[-1]["date"]})')

    today = date.today().isoformat()

    # ── Fetch all Revolut Cold transfers since genesis ────────────────────────
    print(f'\nFetching transfers for {REVOLUT_COLD_KEY} ({REVOLUT_COLD})...')
    inflows  = fetch_transfers(to_addr=REVOLUT_COLD,   label='inflows')
    time.sleep(0.3)
    outflows = fetch_transfers(from_addr=REVOLUT_COLD, label='outflows')

    # ── Build daily delta for Revolut Cold ───────────────────────────────────
    day_delta = defaultdict(float)
    for tx in inflows:
        d = tx_date(tx)
        if d: day_delta[d] += tx_value(tx)
    for tx in outflows:
        d = tx_date(tx)
        if d: day_delta[d] -= tx_value(tx)

    if not day_delta:
        print('No transfers found for Revolut Cold — nothing to patch.')
        return

    first_tx_date = min(day_delta.keys())
    print(f'\nFirst Revolut Cold transaction: {first_tx_date}')
    print(f'Total delta days with activity: {len(day_delta)}')

    # ── Reconstruct Revolut Cold daily balance ────────────────────────────────
    # Build a lookup {date: cumulative_balance} for all dates in cex_points
    # Walk day by day from first tx to today, tracking running balance
    all_dates = sorted({p['date'] for p in cex_points} | {today})
    start = min(first_tx_date, all_dates[0])
    end   = today

    revolut_cold_balance: dict[str, float] = {}
    running = 0.0
    cur = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    while cur <= end_date:
        k = cur.isoformat()
        running = max(0.0, running + day_delta.get(k, 0.0))
        revolut_cold_balance[k] = running
        cur += timedelta(days=1)

    # Override today with live balance (most accurate)
    live_bal = get_live_balance(REVOLUT_COLD)
    print(f'\nLive Revolut Cold balance: {live_bal:,.2f} RIZE')
    revolut_cold_balance[today] = live_bal

    # ── Patch each cex point ──────────────────────────────────────────────────
    patched = 0
    for point in cex_points:
        d = point['date']
        extra = revolut_cold_balance.get(d, 0.0)
        if extra > 0:
            old_val = point['value']
            point['value'] = round(old_val + extra, 2)
            patched += 1

    print(f'\nPatched {patched}/{len(cex_points)} cex points with Revolut Cold balance')

    # Show a few samples
    for p in cex_points[-5:]:
        d = p['date']
        extra = revolut_cold_balance.get(d, 0.0)
        print(f'  {d}: +{extra:,.0f} → total {p["value"]:,.0f} RIZE')

    # ── Save ──────────────────────────────────────────────────────────────────
    history['cex'] = cex_points
    history.setdefault('metadata', {})['updated'] = datetime.now(timezone.utc).isoformat()
    history['metadata'].setdefault('cex_addresses', [])
    if REVOLUT_COLD not in history['metadata']['cex_addresses']:
        history['metadata']['cex_addresses'].append(REVOLUT_COLD)

    OUTPUT_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print(f'\n✅ Patch complete — {OUTPUT_FILE} updated')


if __name__ == '__main__':
    main()
