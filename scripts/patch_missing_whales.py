"""
patch_missing_whales.py
=======================
One-time patch — fetches all RIZE transfers > 5M from the last 30 days
and adds any missing ones to conviction-history.json whales[].
Deduplicates by tx hash. Does NOT touch bonded[], cex[] or metadata.
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

BLOCKS_PER_DAY = 86400 // 2  # ~43200


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
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f'  RPC error: {e}')
        return None


def label(addr):
    if not addr: return 'Unknown'
    a = addr.lower()
    if a == GOV_CONTRACT.lower(): return 'Governance'
    for name, ca in CEX_ADDRESSES.items():
        if a == ca.lower(): return name
    return addr[:6] + '…' + addr[-4:]


def main():
    if not OUTPUT_FILE.exists():
        print('ERROR: conviction-history.json not found.')
        return

    history = json.loads(OUTPUT_FILE.read_text(encoding='utf-8'))
    cutoff  = (date.today() - timedelta(days=30)).isoformat()
    today   = date.today().isoformat()

    print(f'Loaded JSON — {len(history.get("whales", []))} existing whale tx')
    print(f'Fetching all RIZE transfers >5M from last 30 days...')

    # Get current block
    res = rpc('eth_blockNumber', [])
    if not res or not res.get('result'):
        print('ERROR: could not get current block')
        return
    current_block = int(res['result'], 16)
    from_blk = hex(current_block - BLOCKS_PER_DAY * 30)

    # Fetch all transfers in range
    all_transfers = []
    page_key = None
    page = 0
    while True:
        params = {
            'fromBlock'        : from_blk,
            'toBlock'          : 'latest',
            'contractAddresses': [RIZE_TOKEN],
            'category'         : ['erc20'],
            'withMetadata'     : True,
            'excludeZeroValue' : True,
            'maxCount'         : '0x3e8',
            'order'            : 'desc',
        }
        if page_key:
            params['pageKey'] = page_key

        res = rpc('alchemy_getAssetTransfers', [params])
        if not res or 'result' not in res:
            break

        batch = res['result'].get('transfers', [])
        for tx in batch:
            v = float(tx.get('value') or 0)
            if v >= WHALE_MIN:
                all_transfers.append(tx)

        page_key = res['result'].get('pageKey')
        page += 1
        print(f'  Page {page}: {len(batch)} transfers fetched, {len(all_transfers)} whales so far')
        if not page_key:
            break
        time.sleep(0.2)

    print(f'\nTotal whale transfers found: {len(all_transfers)}')

    # Build whale entries
    existing_hashes = {w['tx'] for w in history.get('whales', [])}
    added = 0
    for tx in all_transfers:
        h = tx.get('hash', '')
        if h in existing_hashes:
            continue
        ts = tx.get('metadata', {}).get('blockTimestamp', '')
        d  = ts[:10] if ts else today
        if d < cutoff:
            continue
        entry = {
            'date'      : d,
            'amount'    : round(float(tx.get('value') or 0), 2),
            'from'      : tx.get('from', ''),
            'to'        : tx.get('to', ''),
            'from_label': label(tx.get('from', '')),
            'to_label'  : label(tx.get('to', '')),
            'tx'        : h,
        }
        history.setdefault('whales', []).append(entry)
        existing_hashes.add(h)
        added += 1

    # Cleanup and sort
    history['whales'] = [w for w in history['whales'] if w.get('date', '') >= cutoff]
    history['whales'].sort(key=lambda x: x['date'], reverse=True)
    history.setdefault('metadata', {})['updated'] = datetime.now(timezone.utc).isoformat()

    OUTPUT_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print(f'\n✅ Done — added {added} missing whale tx | total: {len(history["whales"])} entries')


if __name__ == '__main__':
    main()
