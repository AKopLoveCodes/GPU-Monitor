"""OpenSSH transport and a bounded, memory-only history cache."""
import copy
import json
import math
from pathlib import Path
import shlex
import subprocess
import threading
import time

from scripts.ssh_smi import DEFAULT_CONNECT_TIMEOUT, ssh_options


def remote(host, action, control, *, connect_timeout=DEFAULT_CONNECT_TIMEOUT, **payload):
    source = Path(__file__).with_name('agent_remote.py').read_text()
    body = json.dumps(dict(payload, action=action))
    command_timeout = 150 if action in ('install', 'upgrade', 'clear-data', 'uninstall', 'rollback') else connect_timeout + 14
    completed = subprocess.run(['ssh', *ssh_options(control, connect_timeout=connect_timeout), host,
                                shlex.join(['python3', '-c', source])],
                               # SSH diagnostics may use the remote locale or be truncated
                               # on disconnect. They must not kill the background sync.
                               input=body, capture_output=True, text=True, encoding='utf-8', errors='replace',
                               timeout=command_timeout)
    try:
        result = json.loads(completed.stdout)
    except ValueError:
        raise RuntimeError('SSH/remote Python failed: ' + completed.stderr.strip()[:240])
    if completed.returncode or 'error' in result:
        raise RuntimeError(result.get('error', 'remote command failed'))
    return result


class UsageClient:
    def __init__(self, host, control, stop, connect_timeout=DEFAULT_CONNECT_TIMEOUT):
        self.host, self.control, self.stop = host, control, stop
        self.connect_timeout = connect_timeout
        self.lock = threading.Lock()
        self.request_lock = threading.Lock()
        self.generation = 0
        self.wake = threading.Event()
        self.state = {'status': 'connecting', 'devices': [], 'points': [], 'activity': [], 'metadata': {}}

    def reset(self, status='connecting'):
        with self.lock:
            self.generation += 1
            self.state = {'status': status, 'devices': [], 'points': [], 'activity': [], 'metadata': {}}
        self.wake.set()

    def run(self):
        while not self.stop.is_set():
            self.wake.clear()
            self.refresh()
            for _ in range(30):
                if self.stop.wait(1) or self.wake.is_set():
                    break

    def refresh(self):
        with self.request_lock:
            self._refresh()

    def _refresh(self):
        try:
            now = time.time()
            with self.lock:
                previous = copy.deepcopy(self.state)
                generation = self.generation
            since = max(now - 7 * 86400, previous.get('fetched_at', 0) - 120)
            data = remote(self.host, 'query', self.control, connect_timeout=self.connect_timeout,
                          start=since, end=now, step=60)
            if (data['status'] != 'not_installed' and previous.get('users') == data.get('users')
                    and previous.get('metadata', {}).get('database_id') == data.get('metadata', {}).get('database_id')):
                cutoff = max((int((now - 7 * 86400) // 60) + 1) * 60, data.get('retained_from') or 0)
                for key, identity in [('points', lambda r: (r[0], r[1])), ('activity', lambda r: r[0])]:
                    merged = {identity(r): r for r in previous.get(key, []) if cutoff <= r[0] < since}
                    merged.update({identity(r): r for r in data[key]})
                    data[key] = sorted(merged.values())
                devices = {d['id']: d for d in previous.get('devices', [])}
                devices.update({d['id']: d for d in data['devices']})
                data['devices'] = list(devices.values())
            if previous.get('metadata', {}).get('database_id') != data.get('metadata', {}).get('database_id') and since > now - 7 * 86400:
                data = remote(self.host, 'query', self.control, connect_timeout=self.connect_timeout,
                              start=now - 7 * 86400, end=now, step=60)
            data['fetched_at'] = time.time()
            with self.lock:
                if generation == self.generation:
                    self.state = data
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            with self.lock:
                if generation == self.generation:
                    self.state.update(status='unreachable', error=str(exc)[:300])

    def snapshot(self):
        with self.lock:
            cutoff = math.ceil((time.time() - 7 * 86400) / 60) * 60
            for key in ('points', 'activity'):
                self.state[key] = [r for r in self.state.get(key, []) if r[0] >= cutoff]
            present = {r[1] for r in self.state.get('points', [])}
            self.state['devices'] = [d for d in self.state.get('devices', []) if d['id'] in present]
            return copy.deepcopy(self.state)
