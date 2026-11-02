#!/usr/bin/env python3
"""Terminate only this app's local NVML SSH child; verify fallback and recovery."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / 'config.json').read_text())
URL = 'http://127.0.0.1:%d/api/status' % CONFIG['port']


def snapshot():
    with urllib.request.urlopen(URL, timeout=5) as response:
        return json.load(response)['servers']


def wait_for(predicate, seconds=35):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        servers = snapshot()
        if predicate(servers):
            return servers
        time.sleep(.5)
    raise AssertionError('NVML fallback/recovery timed out')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stall', action='store_true', help='pause the SSH child to test the stream watchdog')
    args = parser.parse_args()
    state = json.loads((ROOT / '.runtime/service.json').read_text())
    before = wait_for(lambda servers: all(server['status'] == 'online' and server['gpus']
                      and all(gpu.get('utilization_source') == 'nvml_samples' for gpu in server['gpus'])
                      for server in servers))
    host = before[0]
    assert all(gpu['utilization_source'] == 'nvml_samples' for gpu in host['gpus'])
    children = subprocess.check_output(['pgrep', '-P', str(state['pid'])], text=True).split()
    candidates = []
    for child in children:
        command = subprocess.check_output(['ps', '-p', child, '-o', 'command='], text=True)
        if ('nvmlDeviceGetSamples' in command and 'ssh ' in command
                and ' ' + host['ssh_host'] + ' ' in command):
            candidates.append(int(child))
    assert len(candidates) == 1, 'could not identify the app-owned NVML SSH child'
    started = time.monotonic()
    os.kill(candidates[0], signal.SIGSTOP if args.stall else signal.SIGTERM)
    try:
        def fallback(servers):
            current = next(server for server in servers if server['name'] == host['name'])
            return current['status'] == 'online' and all(gpu['utilization_source'] == 'exporter' for gpu in current['gpus'])
        during = wait_for(fallback)
        fallback_seconds = time.monotonic() - started
        for server in during:
            if server['name'] != host['name']:
                assert server['status'] == 'online'
                assert all(gpu['utilization_source'] == 'nvml_samples' for gpu in server['gpus'])
        def recovered(servers):
            current = next(server for server in servers if server['name'] == host['name'])
            return current['status'] == 'online' and all(gpu['utilization_source'] == 'nvml_samples' for gpu in current['gpus'])
        wait_for(recovered)
        report = {'result': 'PASS', 'mode': 'stall' if args.stall else 'disconnect', 'fallback_seconds': fallback_seconds,
                  'recovery_seconds': time.monotonic() - started, 'other_host_unaffected': True}
        filename = 'nvml-recovery-stall.json' if args.stall else 'nvml-recovery.json'
        (ROOT / '.runtime' / filename).write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        if args.stall:
            check = subprocess.run(['ps', '-p', str(candidates[0]), '-o', 'ppid=,command='], capture_output=True, text=True)
            if (check.returncode == 0 and check.stdout.split()[0] == str(state['pid'])
                    and 'nvmlDeviceGetSamples' in check.stdout):
                try:
                    os.kill(candidates[0], signal.SIGCONT)
                except ProcessLookupError:
                    pass



if __name__ == '__main__':
    main()
