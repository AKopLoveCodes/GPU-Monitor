#!/usr/bin/env python3
"""Read-only end-to-end check against the actual SSH GPU inventory."""
from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
from pathlib import Path
import shlex
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / 'config.json').read_text())
URL = 'http://127.0.0.1:%d' % CONFIG['port']


def get_status():
    with urllib.request.urlopen(URL + '/api/status', timeout=5) as response:
        return json.load(response)


def check_host(host):
    command = shlex.join(['nvidia-smi', '--query-gpu=index,uuid,name,memory.total', '--format=csv,noheader,nounits'])
    completed = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', host['ssh_host'], command],
                               capture_output=True, text=True, check=True, timeout=20)
    expected = {int(row[0]): [field.strip() for field in row] for row in csv.reader(io.StringIO(completed.stdout))}
    snapshot = next(server for server in get_status()['servers'] if server['name'] == host['name'])
    assert snapshot['status'] == 'online', snapshot
    assert time.time() - snapshot['last_success'] < 12, 'Stale sample'
    assert {gpu['index'] for gpu in snapshot['gpus']} == set(expected)
    for gpu in snapshot['gpus']:
        row = expected[gpu['index']]
        assert 'GPU-' + gpu['uuid'] == row[1]
        assert gpu['name'] == row[2]
        assert gpu['memory_total_bytes'] == float(row[3]) * 1024 ** 2
        assert 0 <= gpu['utilization_gpu'] <= 1
        assert 0 <= gpu['memory_free_bytes'] <= gpu['memory_total_bytes']
        assert gpu['processes_available'], 'Per-process GPU query failed'
        assert gpu['process_count'] == len(gpu['processes'])
        for proc in gpu['processes']:
            assert isinstance(proc['pid'], int) and proc['pid'] > 0
            assert proc['memory_bytes'] is not None
    return {'server': host['name'], 'gpus': len(expected), 'processes': sum(len(g['processes']) for g in snapshot['gpus']),
            'process_details': sum('user' in p and 'command' in p for g in snapshot['gpus'] for p in g['processes']),
            'last_success': snapshot['last_success'], 'result': 'PASS'}


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=len(CONFIG['servers'])) as pool:
        results = list(pool.map(check_host, CONFIG['servers']))
    before = get_status()
    time.sleep(CONFIG['interval_seconds'] + 2)
    after = get_status()
    for old, new in zip(before['servers'], after['servers']):
        assert new['last_success'] > old['last_success'], 'Collection did not refresh'
        assert len(new['gpus'][0]['history']) >= 2
    for path in ['/', '/app.js', '/style.css', '/favicon.svg', '/healthz']:
        with urllib.request.urlopen(URL + path) as response:
            assert response.status == 200
    print(json.dumps({'inventory': results, 'live_refresh': 'PASS', 'http_assets': 'PASS'}, ensure_ascii=False, indent=2))
