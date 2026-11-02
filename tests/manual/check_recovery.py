#!/usr/bin/env python3
"""Test private SSH reconnection and a stalled local exporter; always resume it."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from server import control_socket_path

CONFIG = json.loads((ROOT / 'config.json').read_text())
HOST = CONFIG['servers'][0]
URL = 'http://127.0.0.1:%d/api/status' % CONFIG['port']


def snapshot():
    with urllib.request.urlopen(URL, timeout=3) as response:
        return {server['name']: server for server in json.load(response)['servers']}


if __name__ == '__main__':
    control = control_socket_path(ROOT / '.runtime', HOST['name'])
    before = snapshot()
    assert all(server['status'] == 'online' for server in before.values())
    subprocess.run(['ssh', '-S', str(control), '-O', 'exit', HOST['ssh_host']], capture_output=True, text=True, check=True)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        current = snapshot()[HOST['name']]
        if current['status'] == 'online' and current['last_success'] > before[HOST['name']]['last_success'] and all(gpu['processes_available'] for gpu in current['gpus']):
            break
        time.sleep(.5)
    else:
        raise AssertionError('SSH reconnect failed')
    print('PASS: private SSH transport automatically re-established.', flush=True)
    pids = subprocess.run(['lsof', '-t', '-nP', '-iTCP:%d' % HOST['exporter_port'], '-sTCP:LISTEN'], capture_output=True, text=True, check=True).stdout.split()
    assert len(pids) == 1
    pid = int(pids[0])
    command = subprocess.run(['ps', '-p', str(pid), '-o', 'args='], capture_output=True, text=True, check=True).stdout
    assert str(ROOT / '.tools/nvidia_gpu_exporter') in command, 'Not an app-owned exporter'
    before = snapshot()
    print('Stalling only local exporter; remote GPU workloads are untouched.', flush=True)
    try:
        os.kill(pid, signal.SIGSTOP)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            data = snapshot()
            failed = data[HOST['name']]
            if failed['status'] == 'offline':
                assert failed['gpus'] and all(gpu['stale'] for gpu in failed['gpus'])
                for name, server in data.items():
                    if name != HOST['name']:
                        assert server['status'] == 'online'
                        assert server['last_success'] > before[name]['last_success']
                print('PASS: unavailable collector marked offline/stale; other host still collecting.', flush=True)
                break
            time.sleep(.5)
        else:
            raise AssertionError('Collection failure was not detected')
    finally:
        os.kill(pid, signal.SIGCONT)
        print('Local exporter resumed.', flush=True)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        restored = snapshot()[HOST['name']]
        if restored['status'] == 'online' and restored['last_success'] > before[HOST['name']]['last_success'] and all(gpu['processes_available'] for gpu in restored['gpus']):
            assert all(not gpu['stale'] for gpu in restored['gpus'])
            print('PASS: automatic recovery and fresh GPU/process readings.', flush=True)
            break
        time.sleep(.5)
    else:
        raise AssertionError('Transport did not recover automatically')
