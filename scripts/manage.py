#!/usr/bin/env python3
"""Start and stop only this checkout's GPU Monitor background service."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.bootstrap import install
from server import load_config

RUNTIME = ROOT / ".runtime"
STATE = RUNTIME / "service.json"


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status"], nargs="?", default="start")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
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
        if alive:
            if not healthy(state):
                raise RuntimeError("a service process exists but the web UI does not respond; run stop and check .runtime/monitor.log")
            print("GPU Monitor is already running: " + state["url"])
        else:
            settings = load_config(ROOT / "config.json")
            install()
            state = {"instance": uuid.uuid4().hex, "url": "http://127.0.0.1:%d" % settings["port"]}
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
    main()
