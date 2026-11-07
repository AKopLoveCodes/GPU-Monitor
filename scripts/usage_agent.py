#!/usr/bin/env python3
"""Remote NVML recorder, no third-party packages or listening ports."""
import argparse
from collections import deque
import ctypes as c
import fcntl
import json
import os
from pathlib import Path
import pwd
import signal
import time

try:
    from .nvml_stream import Collector, NvmlError, check
    from .usage_store import UsageStore
except ImportError:
    from nvml_stream import Collector, NvmlError, check
    from usage_store import UsageStore


class Process(c.Structure):
    _fields_ = [('pid', c.c_uint), ('memory', c.c_ulonglong),
                ('gpu_instance', c.c_uint), ('compute_instance', c.c_uint)]


class Utilization(c.Structure):
    _fields_ = [('gpu', c.c_uint), ('memory', c.c_uint)]


def processes(nvml, handle):
    # v2/v3 share nvmlProcessInfo_t; do not fall back to the smaller legacy ABI.
    fn = getattr(nvml, 'nvmlDeviceGetComputeRunningProcesses_v3', None)
    if fn is None:
        fn = nvml.nvmlDeviceGetComputeRunningProcesses_v2
    count = c.c_uint()
    code = fn(handle, c.byref(count), None)
    if code not in (0, 7):
        check(code)
    if not count.value:
        return []
    for _ in range(3):
        buffer = (Process * (count.value + 32))()
        count.value = len(buffer)
        code = fn(handle, c.byref(count), buffer)
        if code == 7:
            continue
        check(code)
        return list(buffer[:count.value])
    raise RuntimeError('process list changed too quickly')


def owner(pid):
    try:
        return pwd.getpwuid(Path('/proc/%d' % pid).stat().st_uid).pw_name
    except (OSError, KeyError):
        return None


class Recorder:
    def __init__(self, users):
        self.nvml = c.CDLL('libnvidia-ml.so.1')
        check(self.nvml.nvmlInit_v2())
        self.collector = Collector(self.nvml)
        self.users = set(users)
        self.previous_at = None
        self.samples = {}

    def poll(self):
        samples = self.collector.poll()
        at = time.time()
        by_uuid = {g.get('uuid'): g for g in samples}
        rows = []
        for index, (handle, uuid) in self.collector.devices.items():
            name = c.create_string_buffer(128)
            code = self.nvml.nvmlDeviceGetName(handle, name, len(name))
            row = {'uuid': uuid, 'index': index, 'name': name.value.decode(errors='replace') if code == 0 else 'NVIDIA GPU',
                   'known': False, 'mine': False, 'shared': False, 'memory': None,
                   'util': None, 'util_coverage': 0}
            points = self.samples.setdefault(uuid, deque())
            for stamp, value in by_uuid.get(uuid, {}).get('samples', []):
                if not points or stamp > points[-1][0]:
                    points.append((stamp, value))
            while points and points[0][0] < at - 5:
                points.popleft()
            if self.previous_at is not None:
                weighted = covered = 0
                for before, after in zip(points, list(points)[1:]):
                    delta = after[0] - before[0]
                    overlap = max(0, min(at, after[0]) - max(self.previous_at, before[0]))
                    if 0 < delta <= 1.5:
                        covered += overlap
                        weighted += overlap * after[1]
                if covered:
                    row.update(util=weighted / covered, util_coverage=min(1, covered / max(.001, at - self.previous_at)))
                elif by_uuid.get(uuid, {}).get('error_code') == 3:
                    # Explicit point-sample fallback; unsupported remains null.
                    util = Utilization()
                    if self.nvml.nvmlDeviceGetUtilizationRates(handle, c.byref(util)) == 0:
                        row.update(util=util.gpu / 100, util_coverage=1)
            try:
                procs = processes(self.nvml, handle)
                owners = [(p, owner(p.pid)) for p in procs]
                row['known'] = all(user is not None for _, user in owners)
                mine = [p for p, user in owners if user in self.users]
                row['mine'] = bool(mine)
                row['shared'] = bool(mine) and any(user not in self.users for _, user in owners)
                if all(p.memory != 2**64 - 1 for p in mine):
                    row['memory'] = sum(p.memory for p in mine)
            except (RuntimeError, AttributeError):
                pass
            rows.append(row)
        # Devices that failed enumeration must invalidate all-device coverage.
        for error in self.collector.errors:
            rows.append({'uuid': error.get('uuid', 'unavailable-%s' % error.get('index')), 'index': error.get('index', -1),
                         'name': 'Unavailable GPU', 'known': False, 'mine': False, 'shared': False, 'memory': None})
        self.previous_at = at
        return {'at': at, 'gpus': rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args()
    root = Path(args.root)
    config = json.loads((root / 'config.json').read_text())
    recorder = Recorder(config['own_users'])
    if args.probe:
        frame = recorder.poll()
        if not frame['gpus'] or not any(g['known'] for g in frame['gpus']):
            raise RuntimeError('NVML GPU/process discovery is unavailable')
        print(json.dumps({'ok': True, 'gpus': len(frame['gpus'])}))
        recorder.nvml.nvmlShutdown()
        return
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(root / 'agent.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = UsageStore(root / 'usage.sqlite3')
        running = True
        def stop(*_):
            nonlocal running
            running = False
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        deadline = time.monotonic()
        try:
            while running:
                try:
                    store.ingest(recorder.poll())
                except (RuntimeError, OSError) as exc:
                    # Reset integration across failures; keep retention/heartbeat alive.
                    store.previous = None
                    store.flush(status='unavailable')
                    print(str(exc), flush=True)
                deadline += 1
                if deadline < time.monotonic():
                    deadline = time.monotonic()
                time.sleep(max(0, deadline - time.monotonic()))
        finally:
            store.close()
            recorder.nvml.nvmlShutdown()


if __name__ == '__main__':
    os.umask(0o077)
    main()
