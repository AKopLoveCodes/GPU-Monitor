"""Shared deployment jobs for the local UI and command-line tools."""
from concurrent.futures import ThreadPoolExecutor
import copy
import threading
import time
import uuid
from pathlib import Path

from scripts.ssh_smi import DEFAULT_CONNECT_TIMEOUT
from scripts.usage_client import remote


def bundle(users):
    if not users:
        raise ValueError('configure own_users before installing the agent')
    return {'users': users, 'sources': {name: Path(__file__).with_name(name).read_text()
            for name in ('usage_agent.py', 'usage_store.py', 'nvml_stream.py')}}


def operate(host, settings, control, action, progress=lambda step: None):
    payload = {}
    connect_timeout = settings.get('ssh_connect_timeout_seconds', DEFAULT_CONNECT_TIMEOUT)
    if action in ('install', 'upgrade'):
        progress('checking')
        caps = remote(host['ssh_host'], 'check', control, connect_timeout=connect_timeout)
        if not caps.get('python_supported') or caps.get('nvml_error') or not caps.get('gpu_count'):
            raise RuntimeError('Remote Python 3.8+ and NVIDIA NVML are required: ' + str(caps.get('nvml_error') or caps.get('python')))
        payload = bundle(host.get('own_users', settings.get('own_users', [])))
    progress('deploying' if action in ('install', 'upgrade') else action)
    result = remote(host['ssh_host'], action, control, connect_timeout=connect_timeout, **payload)
    if action in ('install', 'upgrade') and result.get('status') != 'recording':
        raise RuntimeError('Recorder did not start successfully')
    progress('done')
    return result


class AgentManager:
    def __init__(self, monitors, settings):
        self.monitors, self.settings = monitors, settings
        self.lock = threading.Lock()
        self.jobs = {}

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(list(self.jobs.values()))

    def forget(self, servers):
        with self.lock:
            self.jobs = {k: v for k, v in self.jobs.items()
                         if v['status'] == 'running' or not set(v['servers']) & set(servers)}

    def submit(self, action, servers):
        if action not in ('install', 'upgrade', 'clear-data', 'rollback'):
            raise ValueError('unsupported operation')
        configured = {m.config['name']: m for m in self.monitors}
        if not isinstance(servers, list) or not servers or len(servers) != len(set(servers)) or any(s not in configured for s in servers):
            raise ValueError('select configured servers')
        with self.lock:
            if any(j['status'] == 'running' and set(j['servers']) & set(servers) for j in self.jobs.values()):
                raise RuntimeError('a deployment job is already running for this server')
            job = {'id': uuid.uuid4().hex, 'action': action, 'servers': servers, 'status': 'running', 'started_at': time.time(),
                   'results': {s: {'step': 'queued'} for s in servers}}
            # Keep a bounded amount of operational UI state, not a persistent log.
            self.jobs = {k: v for k, v in self.jobs.items() if v['status'] == 'running' or time.time() - v['started_at'] < 3600}
            if len(self.jobs) >= 32:
                oldest = next((k for k, v in self.jobs.items() if v['status'] != 'running'), None)
                if oldest:
                    self.jobs.pop(oldest)
            self.jobs[job['id']] = job
        threading.Thread(target=self._run, args=(job, configured), daemon=True).start()
        with self.lock:
            return copy.deepcopy(job)

    def _run(self, job, configured):
        def execute(name):
            monitor = configured[name]
            def progress(step):
                with self.lock:
                    job['results'][name] = {'step': step}
            try:
                result = operate(monitor.config, self.settings, monitor.control, job['action'], progress)
                monitor.usage.reset('not_installed' if job['action'] == 'rollback' else 'connecting')
                monitor.usage.refresh()
                with self.lock:
                    job['results'][name] = {'step': 'done', 'result': result}
            except Exception as exc:
                with self.lock:
                    job['results'][name] = {'step': 'failed', 'error': str(exc)[:2000]}
        with ThreadPoolExecutor(max_workers=min(4, len(job['servers']))) as pool:
            list(pool.map(execute, job['servers']))
        with self.lock:
            job['status'] = 'failed' if any(r['step'] == 'failed' for r in job['results'].values()) else 'done'
            job['finished_at'] = time.time()
