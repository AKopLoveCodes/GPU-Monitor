#!/usr/bin/env python3
"""Start and stop only this checkout's GPU Monitor background service."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import fcntl
import getpass
import hashlib
import glob
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.bootstrap import asset_for_current_platform, custom_exporter_path, install
from scripts.ssh_smi import DEFAULT_CONNECT_TIMEOUT
from server import DEFAULT_COLLECTION_TIMEOUT, control_socket_path, load_config

RUNTIME = ROOT / ".runtime"
STATE = RUNTIME / "service.json"
SSH_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]*")
UNIX_USER = re.compile(r"[A-Za-z0-9._-]+")
PS_FORMAT = "pid=,user:32=,pcpu=,pmem=,etimes=,args="


def source_signature():
    digest = hashlib.sha256()
    for path in sorted([ROOT / 'server.py', *(ROOT / 'scripts').glob('*.py')]):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None


def owned(state):
    if not state:
        return False
    completed = subprocess.run(["ps", "-p", str(state["pid"]), "-o", "args="], capture_output=True, text=True)
    return str(ROOT / "server.py") in completed.stdout and ("--instance " + state["instance"]) in completed.stdout


def healthy(state):
    try:
        with urllib.request.urlopen(state["url"] + "/healthz", timeout=1) as response:
            data = json.load(response)
        return data.get("app") == "gpu-monitor" and data.get("instance") == state["instance"]
    except (OSError, ValueError):
        return False


def available_port(preferred):
    """Prefer the familiar dashboard port; otherwise let the OS choose one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
        except OSError:
            probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def ssh_aliases(config_path=None):
    """Return concrete aliases from SSH config files without interpreting shell text."""
    path = config_path or Path.home() / ".ssh" / "config"
    aliases = []
    visited = set()

    def scan(current):
        try:
            current = Path(current).expanduser().resolve()
        except OSError:
            return
        if current in visited:
            return
        visited.add(current)
        try:
            lines = current.read_text().splitlines()
        except OSError:
            return
        for line in lines:
            try:
                fields = shlex.split(line, comments=True)
            except ValueError:
                continue
            if len(fields) < 2:
                continue
            keyword = fields[0].lower()
            if keyword == "host":
                for alias in fields[1:]:
                    if any(token in alias for token in "*!?") or not SSH_HOST.fullmatch(alias):
                        continue
                    if alias not in aliases:
                        aliases.append(alias)
            elif keyword == "include":
                for pattern in fields[1:]:
                    expanded = Path(pattern).expanduser()
                    if not expanded.is_absolute():
                        expanded = current.parent / expanded
                    for included in sorted(glob.glob(str(expanded))):
                        scan(included)

    scan(path)
    return aliases


def probe_host(host, connect_timeout=DEFAULT_CONNECT_TIMEOUT):
    """Run the two read-only remote capability checks used by the application."""
    ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}", host]
    command_timeout = connect_timeout + 6
    gpu_command = shlex.join(["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader"])
    try:
        gpu = subprocess.run([*ssh, gpu_command], capture_output=True, text=True, timeout=command_timeout)
    except (OSError, subprocess.TimeoutExpired):
        return {"host": host, "gpu": False, "ps": False, "reason": "SSH connection failed or timed out"}
    if gpu.returncode != 0 or not gpu.stdout.strip():
        return {"host": host, "gpu": False, "ps": False, "reason": "nvidia-smi did not return a GPU"}
    ps_command = shlex.join(["ps", "-ww", "-p", "1", "-o", PS_FORMAT])
    try:
        ps = subprocess.run([*ssh, ps_command], capture_output=True, text=True, timeout=command_timeout)
    except (OSError, subprocess.TimeoutExpired):
        return {"host": host, "gpu": True, "ps": False, "reason": "remote ps check timed out"}
    return {"host": host, "gpu": True, "ps": ps.returncode == 0, "reason": "" if ps.returncode == 0 else "remote ps format is unavailable"}


def server_name(host, used):
    """Create a valid, stable display name from an SSH alias or user@host."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", host.rsplit("@", 1)[-1]).strip("-_")[:48] or "gpu"
    candidate, suffix = base, 2
    while candidate in used:
        ending = "-" + str(suffix)
        candidate = base[:48 - len(ending)] + ending
        suffix += 1
    return candidate


def effective_ssh_user(host):
    """Use SSH's resolved user when available; no connection is opened by -G."""
    try:
        completed = subprocess.run(["ssh", "-G", host], capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired):
        completed = None
    if completed and completed.returncode == 0:
        for line in completed.stdout.splitlines():
            key, _, value = line.partition(" ")
            if key == "user" and UNIX_USER.fullmatch(value.strip()):
                return value.strip()
    user = getpass.getuser()
    return user if UNIX_USER.fullmatch(user) else None


def generated_config(hosts, port, own_users):
    used, servers = set(), []
    for host in hosts:
        name = server_name(host, used)
        used.add(name)
        servers.append({"name": name, "ssh_host": host})
    return {"listen": "127.0.0.1", "port": port, "interval_seconds": 3,
            "ssh_connect_timeout_seconds": DEFAULT_CONNECT_TIMEOUT,
            "collection_timeout_seconds": DEFAULT_COLLECTION_TIMEOUT, "history_points": 100,
            "own_users": sorted(set(own_users)), "servers": servers}


def write_config(path, settings):
    """Atomically write a user-private generated configuration file."""
    descriptor, temporary = tempfile.mkstemp(prefix=".config.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(settings, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def initialize(args):
    config_path = ROOT / "config.json"
    if args.add and args.force:
        raise RuntimeError("--add and --force cannot be used together")
    if args.add and not config_path.exists():
        raise RuntimeError("config.json does not exist yet; run init without --add first")
    if config_path.exists() and not args.force and not args.add:
        raise RuntimeError("config.json already exists; it was not changed (use --force only to replace it)")
    if config_path.exists() and (args.force or args.add) and owned(read_state()):
        raise RuntimeError("GPU Monitor is running; stop it before changing config.json")
    existing = load_config(config_path) if args.add else None
    if not shutil.which("ssh"):
        raise RuntimeError("OpenSSH client 'ssh' was not found in PATH")
    candidates = args.hosts or ssh_aliases()
    if not candidates:
        raise RuntimeError("no concrete aliases found in ~/.ssh/config; run: python3 scripts/manage.py init gpu-01 gpu-02")
    invalid = [host for host in candidates if not SSH_HOST.fullmatch(host)]
    if invalid:
        raise RuntimeError("hosts must be SSH aliases or user@host values containing only letters, digits, dot, underscore, @ or hyphen")
    candidates = list(dict.fromkeys(candidates))
    print("Checking %d SSH candidate(s) with read-only nvidia-smi…" % len(candidates), flush=True)
    probe = (partial(probe_host, connect_timeout=existing["ssh_connect_timeout_seconds"])
             if existing else probe_host)
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        results = list(pool.map(probe, candidates))
    selected = [result for result in results if result["gpu"]]
    for result in results:
        state = "GPU OK" if result["gpu"] else "skipped"
        detail = "; ps details unavailable" if result["gpu"] and not result["ps"] else ("; " + result["reason"] if result["reason"] else "")
        print("  %-24s %s%s" % (result["host"], state, detail))
    if not selected:
        raise RuntimeError("no SSH candidate returned an NVIDIA GPU; config.json was not created")
    if existing:
        existing_hosts = {host["ssh_host"] for host in existing["servers"]}
        selected = [result for result in selected if result["host"] not in existing_hosts]
        if not selected:
            print("All compatible hosts are already present; config.json was not changed")
            return
    if not args.yes:
        if not sys.stdin.isatty():
            raise RuntimeError("discovery succeeded but will not write config.json without a terminal; rerun init --yes")
        verb = "Add" if existing else "Create"
        answer = input("%s config.json for these %d host(s)? [Y/n] " % (verb, len(selected))).strip().lower()
        if answer not in ("", "y", "yes"):
            print("Cancelled; config.json was not changed")
            return
    per_host_users = {result['host']: effective_ssh_user(result['host']) for result in selected}
    users = [user for user in per_host_users.values() if user]
    if existing:
        used = {host["name"] for host in existing["servers"]}
        for result in selected:
            name = server_name(result["host"], used)
            used.add(name)
            existing["servers"].append({"name": name, "ssh_host": result["host"], "own_users": [per_host_users[result["host"]]] if per_host_users[result["host"]] else []})
        existing["own_users"] = sorted(set(existing.get("own_users", []) + users))
        settings = existing
    else:
        settings = generated_config([result["host"] for result in selected], available_port(8765), users)
    for host in settings["servers"]:
        if host["ssh_host"] in per_host_users:
            host["own_users"] = [per_host_users[host["ssh_host"]]] if per_host_users[host["ssh_host"]] else []
    write_config(config_path, settings)
    action = "Added %d host(s) to" if existing else "Created"
    message = (action % len(selected)) if existing else action
    print("%s config.json; local exporter ports are assigned automatically." % message)
    print("Dashboard URL: http://127.0.0.1:%d" % settings["port"])


def doctor():
    """Report local and remote prerequisites without changing configuration."""
    settings = load_config(ROOT / "config.json")
    failures = []
    print("GPU Monitor doctor")
    if not shutil.which("ssh"):
        failures.append("OpenSSH client 'ssh' was not found")
    else:
        print("  local ssh: OK")
    binary = ROOT / ".tools" / "nvidia_gpu_exporter"
    try:
        supplied = custom_exporter_path()
    except RuntimeError as exc:
        failures.append(str(exc))
        supplied = None
    if supplied:
        print("  exporter: user-supplied " + str(supplied))
    elif not failures:
        try:
            flavor, _ = asset_for_current_platform()
            print("  exporter: %s (%s)" % ("cached; verified on start" if binary.exists() else "will download on start", flavor))
        except RuntimeError as exc:
            if binary.exists():
                print("  exporter: user-supplied binary for an unsupported automatic platform")
            else:
                failures.append(str(exc))
    state = read_state()
    if owned(state):
        print("  dashboard port: already owned by this checkout")
    else:
        chosen = available_port(settings["port"])
        if chosen != settings["port"]:
            failures.append("dashboard port %d is already in use" % settings["port"])
        else:
            print("  dashboard port: OK")
    if shutil.which("ssh"):
        probe = partial(probe_host, connect_timeout=settings["ssh_connect_timeout_seconds"])
        with ThreadPoolExecutor(max_workers=min(8, len(settings["servers"]))) as pool:
            results = list(pool.map(probe, [host["ssh_host"] for host in settings["servers"]]))
        for host, result in zip(settings["servers"], results):
            socket_path = control_socket_path(RUNTIME, host["name"])
            if not result["gpu"]:
                failures.append("%s: %s" % (result["host"], result["reason"]))
                continue
            suffix = "; ps details unavailable" if not result["ps"] else ""
            print("  %-24s GPU OK%s; control socket %d bytes" % (result["host"], suffix, len(os.fsencode(socket_path))))
    if failures:
        print("Doctor found %d issue(s):" % len(failures), file=sys.stderr)
        for failure in failures:
            print("  - " + failure, file=sys.stderr)
        raise RuntimeError("doctor checks failed")
    print("Doctor: all required checks passed")


def agent_command(arguments):
    from scripts.agent_manager import operate
    parser = argparse.ArgumentParser(description="Manage the optional remote Compute Watch recorder")
    parser.add_argument('operation', choices=['check', 'install', 'upgrade', 'status', 'uninstall', 'clear-data', 'rollback'])
    parser.add_argument('hosts', nargs='*', help='configured server names or SSH aliases; default: all')
    args = parser.parse_args(arguments)
    settings = load_config(ROOT / 'config.json')
    hosts = [h for h in settings['servers'] if not args.hosts or h['name'] in args.hosts or h['ssh_host'] in args.hosts]
    if any(not any(value in (h['name'], h['ssh_host']) for h in hosts) for value in args.hosts):
        raise ValueError('unknown configured server')
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    failed = False
    for host in hosts:
        try:
            data = operate(host, settings, control_socket_path(RUNTIME, host['name']), args.operation,
                           lambda step: print(host['name'] + ': ' + step, flush=True))
            print(host['name'] + ': ' + json.dumps(data, ensure_ascii=False))
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
            failed = True
            print(host['name'] + ': ' + str(exc), file=sys.stderr)
    if args.operation in ('clear-data', 'rollback', 'uninstall'):
        try:
            request = urllib.request.Request('http://127.0.0.1:%d/api/usage/cache' % settings['port'],
                       data=json.dumps({'servers': [h['name'] for h in hosts]}).encode(),
                       headers={'Content-Type': 'application/json', 'X-GPU-Monitor': '1'}, method='POST')
            with urllib.request.urlopen(request, timeout=3):
                pass
        except OSError:
            pass
    if failed:
        raise RuntimeError('one or more remote agent operations failed')


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'agent':
        agent_command(sys.argv[2:])
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status", "init", "doctor"], nargs="?", default="start", help="start/stop/status/init/doctor; use agent --help for remote recording")
    parser.add_argument("hosts", nargs="*", help="optional SSH aliases for init")
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--force", action="store_true", help="replace config.json during init")
    parser.add_argument("--add", action="store_true", help="add newly discovered hosts to config.json during init")
    parser.add_argument("--yes", action="store_true", help="do not ask before init writes config.json")
    args = parser.parse_args()
    if args.action == "init":
        initialize(args)
        return
    if args.hosts:
        parser.error("host arguments are only valid with the init action")
    if args.force or args.yes or args.add:
        parser.error("--force, --add and --yes are only valid with the init action")
    if args.action == "doctor":
        doctor()
        return
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    RUNTIME.chmod(0o700)
    with open(RUNTIME / "manage.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_state()
        alive = owned(state)
        if args.action == "status":
            print(("running: " + state["url"]) if alive else "GPU Monitor is not running")
            return
        if args.action == "stop":
            if alive:
                os.kill(state["pid"], signal.SIGTERM)
                deadline = time.monotonic() + 40
                while owned(state) and time.monotonic() < deadline:
                    time.sleep(.3)
                if owned(state):
                    raise RuntimeError("the service is still closing SSH connections; try stop again in a moment")
            STATE.unlink(missing_ok=True)
            print("GPU Monitor stopped")
            return
        if alive and state.get('source_signature') != source_signature():
            print('Source changed; restarting the local service to load updated APIs.')
            os.kill(state['pid'], signal.SIGTERM)
            deadline = time.monotonic() + 40
            while owned(state) and time.monotonic() < deadline:
                time.sleep(.3)
            if owned(state):
                raise RuntimeError('old service is still shutting down; retry start shortly')
            alive = False
        if alive:
            if not healthy(state):
                raise RuntimeError("a service process exists but the web UI does not respond; run stop and check .runtime/monitor.log")
            print("GPU Monitor is already running: " + state["url"])
        else:
            try:
                settings = load_config(ROOT / "config.json")
            except ValueError as exc:
                if not (ROOT / "config.json").exists():
                    raise ValueError(str(exc) + "; run 'python3 scripts/manage.py init' to discover GPU hosts automatically")
                raise
            install()
            state = {"instance": uuid.uuid4().hex, "url": "http://127.0.0.1:%d" % settings["port"], "source_signature": source_signature()}
            with open(RUNTIME / "monitor.log", "a") as log:
                process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--instance", state["instance"]],
                                           cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            state["pid"] = process.pid
            STATE.write_text(json.dumps(state))
            for _ in range(40):
                if healthy(state):
                    break
                if process.poll() is not None:
                    raise RuntimeError("startup failed; see .runtime/monitor.log (the port may be in use)")
                time.sleep(.15)
            else:
                raise RuntimeError("the web UI did not come up; see .runtime/monitor.log")
            print("GPU Monitor started: " + state["url"])
        if args.open:
            webbrowser.open(state["url"])


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        sys.exit(1)
