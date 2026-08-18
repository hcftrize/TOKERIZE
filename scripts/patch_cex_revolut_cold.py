"""
patch_cex_revolut_cold.py
=========================
Fills missing bonded[] data points in conviction-history.json
by querying balanceOf(GOV_CONTRACT) at the historical block for each missing date.

Does NOT touch cex[], whales[] or metadata.
"""

import json, os, time, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RIZE_TOKEN   = '0x9818B6c09f5ECc843060927E8587c427C7C93583'
GOV_CONTRACT = '0x5a134098bDBEb05Da9eAc35439c5624547ed26eE'
DECIMALS     = 1e18
OUTPUT_FILE  = Path('rize-data-hub/conviction-history.json')
ALCHEMY_URL  = os.environ.get(
    'ALCHEMY_RPC_URL',
    'https://base-mainnet.g.alchemy.com/v2/qS-QZnHMq-cqmoFkw-grY'
)

BASE_BLOCK_TIME = 2
BLOCKS_PER_DAY  = 86400 // BASE_BLOCK_TIME


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


def get_block_timestamp(block_num):
    res = rpc('eth_getBlockByNumber', [hex(block_num), False])
    if res and res.get('result'):
        return int(res['result']['timestamp'], 16)
    return None


def find_block_at_end_of_day(target_date_str):
    target     = datetime.strptime(target_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    end_of_day = int((target + timedelta(days=1)).timestamp())

    res = rpc('eth_blockNumber', [])
    if not res or not res.get('result'):
        return None
    current_block = int(res['result'], 16)

    now_ts     = int(datetime.now(timezone.utc).timestamp())
    secs_ago   = now_ts - end_of_day
    blocks_ago = secs_ago // BASE_BLOCK_TIME
    lo = max(0, current_block - blocks_ago - BLOCKS_PER_DAY)
    hi = min(current_block, current_block - blocks_ago + BLOCKS_PER_DAY)

    print(f'  Searching block for {target_date_str} (range {lo:,}→{hi:,})...')
    iterations = 0
    while lo < hi and iterations < 30:
        mid = (lo + hi) // 2
        ts  = get_block_timestamp(mid)
        if ts is None:
            break
        if ts < end_of_day:
            lo = mid + 1
        else:
            hi = mid
        iterations += 1
        time.sleep(0.1)

    block = lo - 1
    ts = get_block_timestamp(block)
    if ts:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        print(f'  → Block {block:,} | {dt}')
    return block


def get_bonded_at_block(block_num):
    padded = '000000000000000000000000' + GOV_CONTRACT[2:].lower()
    res = rpc('eth_call', [{'to': RIZE_TOKEN, 'data': '0x70a08231' + padded}, hex(block_num)])
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

    history       = json.loads(OUTPUT_FILE.read_text(encoding='utf-8'))
    bonded_points = history.get('bonded', [])
    today         = date.today().isoformat()

    print(f'Loaded JSON — {len(bonded_points)} bonded points')

    bonded_dates = {p['date'] for p in bonded_points}
    all_dates    = sorted(bonded_dates)
    if not all_dates:
        print('ERROR: no existing bonded data.')
        return

    all_expected = set()
    cur   = date.fromisoformat(all_dates[0])
    end_d = date.fromisoformat(today)
    while cur < end_d:
        all_expected.add(cur.isoformat())
        cur += timedelta(days=1)

    missing = sorted(all_expected - bonded_dates)
    print(f'Missing bonded dates: {len(missing)} → {missing}')

    if not missing:
        print('Nothing to patch.')
        return

    bonded_map = {p['date']: p['value'] for p in bonded_points}

    for d in missing:
        print(f'\nPatching {d}...')
        block = find_block_at_end_of_day(d)
        if not block:
            print(f'  Could not find block — skipping')
            continue
        bonded_val = get_bonded_at_block(block)
        bonded_map[d] = round(bonded_val, 2)
        print(f'  Bonded: {bonded_val:,.0f} RIZE')
        time.sleep(0.3)

    history['bonded'] = [{'date': d, 'value': v}
                         for d, v in sorted(bonded_map.items())]
    history.setdefault('metadata', {})['updated'] = datetime.now(timezone.utc).isoformat()

    OUTPUT_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print(f'\n✅ Done — {len(history["bonded"])} bonded points saved')


if __name__ == '__main__':
    main()
