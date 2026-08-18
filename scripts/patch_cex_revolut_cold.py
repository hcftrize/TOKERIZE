"""
patch_cex_revolut_cold.py
=========================
Two-in-one patch:
  1. Adds Revolut Cold balance to all existing cex[] points since genesis
  2. Fills any missing dates in cex[] using historical transfer data

Does NOT touch bonded[], whales[], unbonding[] or metadata.
"""

import json, os, time, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RIZE_TOKEN    = '0x9818B6c09f5ECc843060927E8587c427C7C93583'
DECIMALS      = 1e18
OUTPUT_FILE   = Path('rize-data-hub/conviction-history.json')
ALCHEMY_URL   = os.environ.get(
    'ALCHEMY_RPC_URL',
    'https://base-mainnet.g.alchemy.com/v2/qS-QZnHMq-cqmoFkw-grY'
)
GENESIS_BLOCK = '0x1CE2E5E'

CEX_ADDRESSES = {
    'Kraken Hot 1'  : '0x02Ac4617Fe004cf8Cd9c988Ff9C905b2Ec676C2d',
    'Kraken Cold 1' : '0x7DAFbA1d69F6C01AE7567Ffd7b046Ca03B706f83',
    'Kraken Cold 2' : '0xd2DD7b597Fd2435b6dB61ddf48544fd931e6869F',
    'Kraken Hot 2'  : '0xcC282E2004428939ee5149A9e7872F0B4d5d5ec7',
    'Revolut'       : '0x9b0c45d46D386cEdD98873168C36efd0DcBa8d46',
    'Revolut Cold'  : '0x15Da7556D5ED888306839bed06f868AeaeDCb0d7',
    'MEXC'          : '0x4e3ae00E8323558fA5Cac04b152238924AA31B60',
    'Bitpanda Cold' : '0x0529ea5885702715e83923c59746ae8734c553B7',
    'Bitpanda Hot'  : '0xB7C5F84455c86f9972A80e82939f7CE40b481664',
    'Ourbit'        : '0x4D59BEC2b09052c60C6149c623fb3a461fB1Fe74',
    'Gate'          : '0x0D0707963952f2fBA59dD06f2b425ace40b492Fe',
}


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

    print(f'  {label}: {len(all_txs)} transfers')
    return all_txs


def tx_date(tx):
    ts = tx.get('metadata', {}).get('blockTimestamp', '')
    return ts[:10] if ts else None


def tx_value(tx):
    v = tx.get('value')
    return float(v) if v else 0.0


def get_live_balance(address):
    padded = '000000000000000000000000' + address[2:].lower()
    res = rpc('eth_call', [{'to': RIZE_TOKEN, 'data': '0x70a08231' + padded}, 'latest'])
    if not res or not res.get('result') or res['result'] == '0x':
        return 0.0
    try:
        return int(res['result'], 16) / DECIMALS
    except Exception:
        return 0.0


def main():
    if not OUTPUT_FILE.exists():
        print('ERROR: conviction-history.json not found.')
        return

    history = json.loads(OUTPUT_FILE.read_text(encoding='utf-8'))
    existing_cex = history.get('cex', [])
    print(f'Loaded JSON — {len(existing_cex)} existing cex points')

    existing_dates = sorted({p['date'] for p in existing_cex})
    if not existing_dates:
        print('ERROR: no cex[] data found.')
        return

    today      = date.today().isoformat()
    start_date = existing_dates[0]
    end_date   = today

    print(f'Existing range: {start_date} → {existing_dates[-1]}')

    # ── Find missing dates ────────────────────────────────────────────────────
    all_expected = set()
    cur = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while cur <= end:
        all_expected.add(cur.isoformat())
        cur += timedelta(days=1)

    missing_dates = sorted(all_expected - set(existing_dates))
    print(f'Missing dates  : {len(missing_dates)}')
    if missing_dates:
        print(f'  → {missing_dates[:5]}{"..." if len(missing_dates) > 5 else ""}')

    if not missing_dates:
        print('No missing dates — nothing to fill. Exiting.')
        return

    # ── Fetch transfers for ALL addresses since genesis ───────────────────────
    print(f'\nFetching transfers for all {len(CEX_ADDRESSES)} CEX addresses...')
    day_delta_per_addr = defaultdict(lambda: defaultdict(float))

    for name, addr in CEX_ADDRESSES.items():
        ins  = fetch_transfers(to_addr=addr,   label=f'{name} in')
        time.sleep(0.2)
        outs = fetch_transfers(from_addr=addr, label=f'{name} out')
        time.sleep(0.2)
        for tx in ins:
            d = tx_date(tx)
            if d: day_delta_per_addr[addr][d] += tx_value(tx)
        for tx in outs:
            d = tx_date(tx)
            if d: day_delta_per_addr[addr][d] -= tx_value(tx)

    # ── Build full daily balance series from genesis ──────────────────────────
    print('\nBuilding full daily balance series...')
    balances = defaultdict(float)
    full_series = {}

    cur = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while cur <= end:
        k = cur.isoformat()
        for addr in day_delta_per_addr:
            balances[addr] = max(0.0, balances[addr] + day_delta_per_addr[addr].get(k, 0.0))
        full_series[k] = round(sum(balances.values()), 2)
        cur += timedelta(days=1)

    # Override today with live balance
    print('\nFetching live balances for today...')
    live_total = 0.0
    for name, addr in CEX_ADDRESSES.items():
        bal = get_live_balance(addr)
        live_total += bal
        time.sleep(0.1)
    print(f'  Live CEX total: {live_total:,.0f} RIZE')
    full_series[today] = round(live_total, 2)

    # ── Inject missing dates into existing cex[] ─────────────────────────────
    existing_map = {p['date']: p['value'] for p in existing_cex}

    for d in missing_dates:
        if d in full_series:
            existing_map[d] = full_series[d]
            print(f'  Filled {d}: {full_series[d]:,.0f} RIZE')

    # Rebuild sorted list
    new_cex = [{'date': d, 'value': v} for d, v in sorted(existing_map.items())]

    print(f'\nBefore: {len(existing_cex)} points → After: {len(new_cex)} points')

    # ── Save ──────────────────────────────────────────────────────────────────
    history['cex'] = new_cex
    history.setdefault('metadata', {})['updated'] = datetime.now(timezone.utc).isoformat()

    OUTPUT_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print(f'\n✅ Done — {OUTPUT_FILE} updated')


if __name__ == '__main__':
    main()
