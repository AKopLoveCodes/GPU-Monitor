"""SSH-side deployment dispatcher. Executed with a JSON payload on stdin."""
import fcntl
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import time

UNIT = 'gpu-monitor-agent.service'
ROOT = Path.home() / '.local/share/gpu-monitor'


def run(argv, required=True):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if required and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'command failed')
    return result


def lingering():
    result = run(['loginctl', 'show-user', str(os.getuid()), '--property=Linger', '--value'], False) if shutil.which('loginctl') else None
    return bool(result and result.returncode == 0 and result.stdout.strip() == 'yes')


def capabilities():
    manager = run(['systemctl', '--user', 'show-environment'], False) if shutil.which('systemctl') else None
    gpu_count = None
    nvml_error = None
    try:
        nvml = c.CDLL('libnvidia-ml.so.1')
        code = nvml.nvmlInit_v2()
        if code != 0:
            raise RuntimeError('NVML initialization error %d' % code)
        try:
            count = c.c_uint()
            code = nvml.nvmlDeviceGetCount_v2(c.byref(count))
            if code != 0:
                raise RuntimeError('NVML enumeration error %d' % code)
            gpu_count = count.value
        finally:
            nvml.nvmlShutdown()
    except (OSError, RuntimeError, AttributeError) as exc:
        nvml_error = str(exc)
    return {'gpu_count': gpu_count, 'nvml_error': nvml_error, 'python_supported': sys.version_info >= (3, 8),
            'python': sys.version.split()[0], 'systemd': bool(manager and manager.returncode == 0),
            'linger': lingering(), 'root': str(ROOT), 'user': pwd.getpwuid(os.getuid()).pw_name}


def state_file():
    path = ROOT / 'deployment.json'
    return json.loads(path.read_text()) if path.exists() else {'linger_changed': False}


def set_linger(enabled):
    user = pwd.getpwuid(os.getuid()).pw_name
    result = run(['loginctl', '--no-ask-password', 'enable-linger' if enabled else 'disable-linger', user], False)
    if result.returncode or lingering() != enabled:
        raise RuntimeError('Cannot configure persistent user service. Ask the administrator to run: loginctl ' +
                           ('enable-linger ' if enabled else 'disable-linger ') + user + '. ' + result.stderr.strip())


def status():
    from usage_store import query
    now = time.time()
    result = query(ROOT / 'usage.sqlite3', now - 120, now)
    result.pop('points', None)
    result.pop('activity', None)
    result.update(capabilities())
    result['bytes'] = sum(p.stat().st_size for p in ROOT.glob('usage.sqlite3*'))
    result['deployment'] = state_file()
    return result


def wait_healthy(started):
    from usage_store import query
    for _ in range(25):
        result = query(ROOT / 'usage.sqlite3', started - 60, time.time())
        if result['status'] == 'recording' and float(result['metadata'].get('heartbeat', 0)) >= started and result['devices']:
            return
        time.sleep(1)
    details = run(['journalctl', '--user', '-u', UNIT, '-n', '8', '--no-pager', '-o', 'cat'], False)
    raise RuntimeError('Agent did not produce a healthy sample: ' + details.stdout.strip()[-1500:])


def install(payload):
    caps = capabilities()
    if not caps['systemd']:
        raise RuntimeError('A working user systemd manager is required for continuous recording')
    if not caps.get('python_supported', True) or caps.get('nvml_error') or caps.get('gpu_count', 1) == 0:
        raise RuntimeError('Remote Python 3.8+ and a working NVIDIA NVML driver are required: ' + str(caps.get('nvml_error') or caps.get('python')))
    users = payload['users']
    if not users or len(users) > 16 or any(not isinstance(u, str) or not re.fullmatch(r'[A-Za-z0-9._-]+', u) for u in users):
        raise ValueError('configure own_users before installing the agent')
    sources = payload['sources']
    if set(sources) != {'usage_agent.py', 'usage_store.py', 'nvml_stream.py'}:
        raise ValueError('invalid agent bundle')
    for name, source in sources.items():
        compile(source, name, 'exec')
    config = ROOT / 'config.json'
    old_config = config.read_text() if config.exists() else None
    if old_config is not None and set(json.loads(old_config)['own_users']) != set(users):
        raise ValueError('own_users changed; uninstall the agent to clear old personal history before reinstalling')
    digest = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()[:16]
    release = ROOT / 'releases' / digest
    release.mkdir(parents=True, mode=0o700, exist_ok=True)
    for name, source in sources.items():
        (release / name).write_text(source)
    current = ROOT / 'current'
    old_release = os.readlink(current) if current.is_symlink() else None
    unit = Path.home() / '.config/systemd/user' / UNIT
    old_unit = unit.read_text() if unit.exists() else None
    deployment = state_file()
    changed_linger = False
    def switch(target):
        link = ROOT / 'current.next'
        link.unlink(missing_ok=True)
        link.symlink_to(target)
        os.replace(link, current)
    try:
        temporary = ROOT / 'config.next'
        temporary.write_text(json.dumps({'own_users': users}))
        os.replace(temporary, config)
        run([sys.executable, str(release / 'usage_agent.py'), '--root', str(ROOT), '--probe'])
        if not caps['linger']:
            set_linger(True)
            changed_linger = True
        (ROOT / 'deployment.json').write_text(json.dumps({'linger_changed': deployment['linger_changed'] or changed_linger}))
        switch(str(release))
        unit.parent.mkdir(parents=True, exist_ok=True)
        quote = lambda s: '"' + str(s).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
        unit.write_text('[Unit]\nDescription=GPU Monitor personal usage recorder\n\n[Service]\n'
                        'ExecStart=' + quote(sys.executable) + ' ' + quote(current / 'usage_agent.py') + ' --root ' + quote(ROOT) + '\n'
                        'Restart=on-failure\nRestartSec=5\nUMask=0077\nStandardOutput=null\nStandardError=journal\nLogRateLimitIntervalSec=60s\nLogRateLimitBurst=3\n\n[Install]\nWantedBy=default.target\n')
        run(['systemctl', '--user', 'daemon-reload'])
        run(['systemctl', '--user', 'enable', UNIT])
        started = time.time()
        run(['systemctl', '--user', 'restart', UNIT])
        sys.path.insert(0, str(release))
        wait_healthy(started)
        for directory in (ROOT / 'releases').iterdir():
            if str(directory) not in (str(release), old_release):
                shutil.rmtree(directory)
        return status()
    except BaseException:
        run(['systemctl', '--user', 'stop', UNIT], False)
        if old_config is not None:
            config.write_text(old_config)
        else:
            config.unlink(missing_ok=True)
        if old_release:
            switch(old_release)
        else:
            current.unlink(missing_ok=True)
        if old_unit is not None:
            unit.write_text(old_unit)
        else:
            run(['systemctl', '--user', 'disable', UNIT], False)
            unit.unlink(missing_ok=True)
        run(['systemctl', '--user', 'daemon-reload'], False)
        if old_release and old_unit:
            (ROOT / 'deployment.json').write_text(json.dumps(deployment))
            run(['systemctl', '--user', 'restart', UNIT], False)
        else:
            # A failed first install must leave no code/database behind.
            for name in ('config.next', 'deployment.json', 'usage.sqlite3', 'usage.sqlite3-journal', 'agent.lock'):
                (ROOT / name).unlink(missing_ok=True)
            shutil.rmtree(ROOT / 'releases', ignore_errors=True)
        if changed_linger:
            set_linger(False)
        raise


def cleanup(action):
    unit = Path.home() / '.config/systemd/user' / UNIT
    installed = (ROOT / 'current').exists() and unit.exists()
    if unit.exists():
        run(['systemctl', '--user', 'stop', UNIT])
    # Refuse to erase a live recorder, including partially installed services.
    active = run(['systemctl', '--user', 'is-active', UNIT], False)
    if active.stdout.strip() in ('active', 'activating', 'deactivating'):
        raise RuntimeError('Recorder is still running; data was not deleted')
    if action == 'clear-data':
        for name in ('usage.sqlite3', 'usage.sqlite3-journal', 'usage.sqlite3-wal', 'usage.sqlite3-shm'):
            (ROOT / name).unlink(missing_ok=True)
        if installed:
            started = time.time()
            run(['systemctl', '--user', 'start', UNIT])
            wait_healthy(started)
        return {'status': 'cleared', 'recording': installed}
    deployment = state_file()
    if unit.exists():
        run(['systemctl', '--user', 'disable', '--now', UNIT])
        unit.unlink(missing_ok=True)
        run(['systemctl', '--user', 'daemon-reload'])
        run(['systemctl', '--user', 'reset-failed', UNIT], False)
    # Only revert lingering if this installation enabled it.
    if deployment.get('linger_changed'):
        set_linger(False)
    if ROOT.exists():
        shutil.rmtree(ROOT)
    return {'status': 'not_installed', 'data_retained': False, 'linger_restored': True}


def main():
    os.umask(0o077)
    payload = json.load(sys.stdin)
    action = payload['action']
    if action == 'check':
        return capabilities()
    current = ROOT / 'current'
    if action in ('install', 'upgrade', 'uninstall', 'rollback', 'clear-data'):
        if action not in ('install', 'upgrade') and not ROOT.exists():
            return {'status': 'not_installed', 'data_retained': False}
        ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(ROOT / 'deploy.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if action in ('install', 'upgrade'):
                return install(payload)
            if current.exists():
                sys.path.insert(0, str(current))
            return cleanup(action)
    if not current.exists():
        return {'status': 'not_installed', 'devices': [], 'points': [], 'activity': [], 'metadata': {}}
    sys.path.insert(0, str(current))
    if action == 'status':
        return status()
    if action == 'query':
        from usage_store import query
        result = query(ROOT / 'usage.sqlite3', payload['start'], payload['end'], payload.get('step', 60))
        result['users'] = json.loads((ROOT / 'config.json').read_text())['own_users']
        return result
    raise ValueError('unknown action')


if __name__ == '__main__':
    try:
        print(json.dumps(main(), allow_nan=False, separators=(',', ':')))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(1)
