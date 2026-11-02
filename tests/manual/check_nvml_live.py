#!/usr/bin/env python3
"""Observe existing workloads; verify history coverage and collector resource use.

No GPU work is launched. Use --seconds 65 to warm a complete 30-second window.
"""
import argparse
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=65)
    args = parser.parse_args()
    config = json.loads((ROOT / 'config.json').read_text())
    url = 'http://127.0.0.1:%d/api/status' % config['port']
    end = time.monotonic() + args.seconds
    records, seen, resources = {}, set(), {}
    while time.monotonic() < end:
        with urllib.request.urlopen(url, timeout=5) as response:
            status = json.load(response)
        for server in status['servers']:
            if server['status'] != 'online':
                continue
            stats = server.get('nvml_collector')
            if stats and stats.get('uptime_seconds', 0) > 0:
                rows = resources.setdefault(server['name'], [])
                rows.append(stats)
            identity = (server['name'], server['last_success'])
            if identity in seen:
                continue
            seen.add(identity)
            for gpu in server['gpus']:
                key = (server['name'], gpu['index'])
                record = records.setdefault(key, {'samples': 0, 'buffered': 0, 'max_coverage': 0,
                                                  'max_peak': 0, 'max_mean': 0,
                                                  'window_active_point_zero': 0})
                record['samples'] += 1
                window = gpu.get('utilization_window')
                if gpu.get('utilization_source') != 'nvml_samples' or not window:
                    continue
                record['buffered'] += 1
                assert 0 <= window['coverage'] <= 1, gpu
                if window['mean'] is not None:
                    assert 0 <= window['mean'] <= window['peak'] + 1e-10 <= 1 + 1e-10, gpu
                    record['max_mean'] = max(record['max_mean'], window['mean'])
                    record['max_peak'] = max(record['max_peak'], window['peak'])
                    if window['peak'] > 0 and gpu['utilization_gpu_exporter'] == 0:
                        record['window_active_point_zero'] += 1
                record['max_coverage'] = max(record['max_coverage'], window['coverage'])
        time.sleep(1)
    assert len(records) > 0, 'no online GPUs'
    for key, record in records.items():
        assert record['buffered'] >= 3, (key, record)
        assert record['max_coverage'] > .85, (key, record)
    for host in config['servers']:
        assert any(key[0] == host['name'] for key in records), 'missing host: ' + host['name']
    resource_summary = {}
    for host, rows in resources.items():
        first, last = rows[0], rows[-1]
        duration = last['uptime_seconds'] - first['uptime_seconds']
        resource_summary[host] = {
            'cpu_percent_one_core': 100 * (last['cpu_seconds'] - first['cpu_seconds']) / duration if duration else None,
            'peak_rss_mib': max(row['peak_rss_bytes'] for row in rows) / 1024**2,
            'max_query_ms': max(row['query_seconds'] for row in rows) * 1000,
        }
    report = {'result': 'PASS', 'duration_seconds': args.seconds,
              'gpus': [dict(host=host, index=index, **record) for (host, index), record in records.items()],
              'remote_nvml_collectors': resource_summary}
    (ROOT / '.runtime' / 'nvml-live.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
