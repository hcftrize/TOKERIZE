"""
scrape_conviction.py
====================
Daily script — runs at 08:00 UTC via cron-job.org → GitHub Actions.
Appends one data point per day to conviction-history.json.

Fetches:
  - bonded  : balanceOf(governance contract)
  - cex     : sum balanceOf(all CEX addresses)
  - whales  : transfers > 5M RIZE in last 28h (keeps 30d rolling window)
              + backfills any missing whale tx in last 30d
              + backfills any missing bonded points in last 7d
"""

import json, os, time, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RIZE_TOKEN   = '0x9818B6c09f5ECc843060927E8587c427C7C93583'
GOV_CONTRACT = '0x5a134098bDBEb05Da9eAc35439c5624547ed26eE'
DECIMALS     = 1e18
WHALE_MIN    = 5_000_000
OUTPUT_FILE  = Path('rize-data-hub/conviction-history.json')
ALCHEMY_URL  = os.environ.get(
    'ALCHEMY_RPC_URL',
    'https://base-mainnet.g.alchemy.com/v2/qS-QZnHMq-cqmoFkw-grY'
)

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

BASE_BLOCK_TIME = 2
BLOCKS_PER_DAY  = 86400 // BASE_BLOCK_TIME  # ~43200


# ── Core RPC ──────────────────────────────────────────────────────────────────
def rpc(method, params, url=None):
    endpoint = url or ALCHEMY_URL
    payload  = json.dumps({
        'jsonrpc': '2.0', 'id': 1,
        'method': method, 'params': params
    }).encode()
    req = urllib.request.Request(
        endpoint, data=payload,
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f'  RPC error ({endpoint}): {e}')
        return None


def get_balance(address):
    padded = '000000000000000000000000' + address[2:].lower()
    res = rpc('eth_call', [{'to': RIZE_TOKEN, 'data': '0x70a08231' + padded}, 'latest'])
    if not res or not res.get('result') or res['result'] == '0x':
        return 0.0
    try:
        return int(res['result'], 16) / DECIMALS
    except Exception:
        return 0.0


def get_current_block():
    res = rpc('eth_blockNumber', [])
    if res and res.get('result'):
        return int(res['result'], 16)
    return 0


# ── Whale helpers ─────────────────────────────────────────────────────────────
def _label(addr):
    if not addr: return 'Unknown'
    a = addr.lower()
    if a == GOV_CONTRACT.lower(): return 'Governance'
    for name, ca in CEX_ADDRESSES.items():
        if a == ca.lower(): return name
    return addr[:6] + '…' + addr[-4:]


def _fetch_whale_transfers(from_block_hex, to_block='latest'):
    """Fetch RIZE transfers >= WHALE_MIN in a block range."""
    params = {
        'fromBlock'        : from_block_hex,
        'toBlock'          : to_block,
        'contractAddresses': [RIZE_TOKEN],
        'category'         : ['erc20'],
        'withMetadata'     : True,
        'excludeZeroValue' : True,
        'maxCount'         : '0x3e8',
        'order'            : 'desc',
    }
    res = rpc('alchemy_getAssetTransfers', [params])
    if not res or 'result' not in res:
        return []

    whales = []
    for tx in res['result'].get('transfers', []):
        v = float(tx.get('value') or 0)
        if v < WHALE_MIN:
            continue
        ts = tx.get('metadata', {}).get('blockTimestamp', '')
        whales.append({
            'date'      : ts[:10] if ts else date.today().isoformat(),
            'amount'    : round(v, 2),
            'from'      : tx.get('from', ''),
            'to'        : tx.get('to', ''),
            'from_label': _label(tx.get('from', '')),
            'to_label'  : _label(tx.get('to', '')),
            'tx'        : tx.get('hash', ''),
        })
    return whales


def fetch_recent_whales(current_block):
    """Fetch whale transfers from last 28h (overlap buffer between daily runs)."""
    from_blk = hex(current_block - int(BLOCKS_PER_DAY * 1.17))  # ~28h
    return _fetch_whale_transfers(from_blk)


def backfill_missing_whales(history, current_block):
    """
    Fetch all whale transfers in last 30 days and add any missing ones.
    Deduplicates by tx hash. Silent if nothing is missing.
    """
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    from_blk = hex(current_block - BLOCKS_PER_DAY * 30)

    print('  Backfilling whale transfers for last 30 days...')
    all_transfers = _fetch_whale_transfers(from_blk)

    existing_hashes = {w['tx'] for w in history.get('whales', [])}
    added = 0
    for w in all_transfers:
        if w['tx'] not in existing_hashes and w.get('date', '') >= cutoff:
            history.setdefault('whales', []).append(w)
            existing_hashes.add(w['tx'])
            added += 1

    if added:
        print(f'  Added {added} missing whale tx(s) from backfill')
    else:
        print('  No missing whale tx — backfill clean')


# ── Bonded backfill ───────────────────────────────────────────────────────────
def _get_block_timestamp(block_num):
    res = rpc('eth_getBlockByNumber', [hex(block_num), False])
    if res and res.get('result'):
        return int(res['result']['timestamp'], 16)
    return None


def _find_block_at_end_of_day(target_date_str):
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
    iterations = 0
    while lo < hi and iterations < 30:
        mid = (lo + hi) // 2
        ts  = _get_block_timestamp(mid)
        if ts is None: break
        if ts < end_of_day: lo = mid + 1
        else: hi = mid
        iterations += 1
        time.sleep(0.1)
    return lo - 1


def _get_bonded_at_block(block_num):
    padded = '000000000000000000000000' + GOV_CONTRACT[2:].lower()
    res = rpc('eth_call', [{'to': RIZE_TOKEN, 'data': '0x70a08231' + padded}, hex(block_num)])
    if not res or not res.get('result') or res['result'] == '0x':
        return 0.0
    try:
        return int(res['result'], 16) / DECIMALS
    except Exception:
        return 0.0


def backfill_missing_bonded(history):
    bonded_dates = {p['date'] for p in history.get('bonded', [])}
    missing = [
        (date.today() - timedelta(days=i)).isoformat()
        for i in range(1, 8)
        if (date.today() - timedelta(days=i)).isoformat() not in bonded_dates
    ]
    if not missing:
        return
    print(f'\n⚠️  Backfilling {len(missing)} missing bonded point(s): {missing}')
    bonded_map = {p['date']: p['value'] for p in history.get('bonded', [])}
    for d in sorted(missing):
        block = _find_block_at_end_of_day(d)
        if not block:
            print(f'  Could not find block for {d} — skipping')
            continue
        val = _get_bonded_at_block(block)
        bonded_map[d] = round(val, 2)
        print(f'  {d}: {val:,.0f} RIZE bonded')
        time.sleep(0.3)
    history['bonded'] = [{'date': d, 'value': v} for d, v in sorted(bonded_map.items())]


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    today     = date.today().isoformat()
    cutoff30d = (date.today() - timedelta(days=30)).isoformat()

    if OUTPUT_FILE.exists():
        history = json.loads(OUTPUT_FILE.read_text(encoding='utf-8'))
        print(f'Loaded existing JSON ({len(history.get("bonded", []))} bonded points)')
    else:
        print('No JSON found — creating fresh')
        history = {'bonded': [], 'cex': [], 'whales': [], 'metadata': {}}

    history['bonded'] = [e for e in history.get('bonded', []) if e['date'] != today]
    history['cex']    = [e for e in history.get('cex',    []) if e['date'] != today]

    print(f'Fetching snapshot for {today}...')
    current_block = get_current_block()

    # 1. Bonded
    bonded = get_balance(GOV_CONTRACT)
    print(f'  Bonded    : {bonded:,.0f} RIZE')

    # 2. CEX total
    cex_total = 0.0
    for name, addr in CEX_ADDRESSES.items():
        cex_total += get_balance(addr)
        time.sleep(0.15)
    print(f'  CEX total : {cex_total:,.0f} RIZE')

    # 3. Whale movements — 28h window + 30d backfill
    print('  Fetching whale movements (28h)...')
    new_whales = fetch_recent_whales(current_block)
    print(f'  Found {len(new_whales)} whale tx(s) in last 28h')

    # Append daily points
    history.setdefault('bonded', []).append({'date': today, 'value': round(bonded, 2)})
    history.setdefault('cex',    []).append({'date': today, 'value': round(cex_total, 2)})

    # Merge today's whales
    existing_hashes = {w['tx'] for w in history.get('whales', [])}
    for w in new_whales:
        if w['tx'] not in existing_hashes:
            history.setdefault('whales', []).append(w)
            existing_hashes.add(w['tx'])

    # 4. Backfill missing whale tx in last 30 days
    backfill_missing_whales(history, current_block)

    # 5. Backfill missing bonded points (last 7 days)
    backfill_missing_bonded(history)

    # Cleanup: keep 30d rolling window, sort desc
    history['whales'] = [w for w in history['whales'] if w.get('date', '') >= cutoff30d]
    history['whales'].sort(key=lambda x: x['date'], reverse=True)

    history.setdefault('metadata', {})['updated'] = datetime.now(timezone.utc).isoformat()

    OUTPUT_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print(f'\n✅ Saved — {len(history["bonded"])} bonded, {len(history["whales"])} whale tx')


if __name__ == '__main__':
    main()
