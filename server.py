#!/usr/bin/env python3
"""Local, read-only GPU dashboard backed by official nvidia_gpu_exporter."""
import argparse
from collections import defaultdict, deque
import copy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from scripts.ssh_smi import ssh_options

ROOT = Path(__file__).resolve().parent
FIELDS = "index,uuid,name,driver_version,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu,power.draw,power.limit,fan.speed"
SAMPLE = re.compile(r'^([a-zA-Z_:][\w:]*)(?:\{(.*)\})?\s+([^\s]+)(?:\s+\S+)?$')
LABEL = re.compile(r'([a-zA-Z_]\w*)="((?:\\.|[^"\\])*)"')


class MonitorError(RuntimeError):
    """A collection failure carrying a stable code the web UI localizes."""

    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code


def parse_metrics(body):
    """Parse the exporter's scalar samples; normalize non-finite values to None."""
    samples = []
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        match = SAMPLE.match(line)
        if not match:
            continue
        name, raw_labels, raw_value = match.groups()
        labels = {key: json.loads('"' + value + '"') for key, value in LABEL.findall(raw_labels or "")}
        value = float(raw_value)
        samples.append((name, labels, value if math.isfinite(value) else None))
    return samples


def decode_snapshot(body):
    samples = parse_metrics(body)
    scalars = {name: value for name, labels, value in samples if not labels}
    if scalars.get("nvidia_smi_last_collect_success") != 1:
        raise MonitorError("collect_failed", "nvidia_smi_last_collect_success != 1; the remote nvidia-smi query failed")
    gpus, processes = {}, {}
    for name, labels, value in samples:
        if name == "nvidia_smi_gpu_info":
            uuid = labels["uuid"]
            gpus[uuid] = dict(uuid=uuid, index=int(labels["index"]), name=labels["name"],
                              driver_version=labels.get("driver_version"), cuda_version=labels.get("cuda_version"),
                              processes=[], process_count=None)
    if not gpus:
        raise MonitorError("no_gpu", "the remote host reported no GPU in nvidia_smi_gpu_info")
    metric_fields = {
        "memory_total_bytes": "memory_total_bytes", "memory_used_bytes": "memory_used_bytes",
        "memory_free_bytes": "memory_free_bytes", "utilization_gpu_ratio": "utilization_gpu",
        "utilization_memory_ratio": "utilization_memory", "temperature_gpu": "temperature_c",
        "power_draw_watts": "power_watts", "power_limit_watts": "power_limit_watts",
        "fan_speed_ratio": "fan_ratio", "compute_apps": "process_count",
    }
    for name, labels, value in samples:
        gpu = gpus.get(labels.get("uuid"))
        if gpu is None:
            continue
        key = name.removeprefix("nvidia_smi_")
        if key in metric_fields:
            gpu[metric_fields[key]] = value
        if key in ("compute_app_info", "compute_app_used_memory_bytes"):
            pid = int(labels["pid"])
            identity = (labels["uuid"], pid)
            proc = processes.setdefault(identity, {"pid": pid, "name": labels.get("process_name", ""), "memory_bytes": None})
            if key == "compute_app_used_memory_bytes":
                proc["memory_bytes"] = value
    for (uuid, pid), proc in processes.items():
        gpus[uuid]["processes"].append(proc)
    process_ok = scalars.get("nvidia_smi_compute_apps_last_collect_success") == 1
    for gpu in gpus.values():
        gpu["processes"].sort(key=lambda proc: -(proc["memory_bytes"] or 0))
        gpu["processes_available"] = process_ok
        if not process_ok:
            gpu["process_count"] = None
    return sorted(gpus.values(), key=lambda gpu: gpu["index"]), scalars


def parse_ps(output):
    result = {}
    for line in output.splitlines():
        cols = line.strip().split(None, 5)
        if len(cols) < 6:
            continue
        try:
            pid, user, cpu, memory, elapsed, command = cols
            result[int(pid)] = {"user": user, "cpu_percent": float(cpu), "host_memory_percent": float(memory),
                                "elapsed_seconds": int(elapsed), "command": command[:2048]}
        except ValueError:
            continue
    return result


class HostMonitor:
    def __init__(self, config, settings, runtime, stop_event):
        self.config, self.settings, self.runtime = config, settings, runtime
        self.stop = stop_event
        self.lock = threading.Lock()
        self.process = None
        self.log = None
        self.control = str(runtime / ("ssh-" + config["name"]))
        self.history = defaultdict(lambda: deque(maxlen=settings["history_points"]))
        self.raw_metrics = ""
        self.state = {"name": config["name"], "ssh_host": config["ssh_host"], "status": "connecting",
                      "error": None, "error_code": None, "warning": None, "warning_code": None,
                      "last_success": None, "gpus": []}

    def snapshot(self):
        with self.lock:
            state = copy.deepcopy(self.state)
        stale_after = max(12, self.settings["interval_seconds"] * 3)
        if state["status"] == "online" and time.time() - (state["last_success"] or 0) > stale_after:
            state.update(status="offline", error_code="stale",
                         error="last successful sample is older than the staleness window")
        for gpu in state["gpus"]:
            gpu["stale"] = state["status"] != "online"
        return state

    def ensure_exporter(self):
        if self.process is not None and self.process.poll() is None:
            return
        # Never attach to or stop an unrelated process using this port.
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", self.config["exporter_port"]))
        if self.log:
            self.log.close()
        self.log = open(self.runtime / (self.config["name"] + "-exporter.log"), "a", buffering=1)
        wrapper = shlex.join([sys.executable, str(ROOT / "scripts/ssh_smi.py"), self.config["ssh_host"], self.control])
        self.process = subprocess.Popen([
            str(ROOT / ".tools/nvidia_gpu_exporter"),
            "--web.listen-address=127.0.0.1:" + str(self.config["exporter_port"]),
            "--nvidia-smi-command=" + wrapper, "--query-field-names=" + FIELDS,
            "--collect.compute-apps", "--collect.timeout=" + str(self.settings["collection_timeout_seconds"]) + "s",
            "--no-shutdown-on-error", "--log.level=warn"], stdout=self.log, stderr=self.log)
        # Startup itself queries GPU metadata over SSH. Bound the wait independently.
        deadline = time.monotonic() + self.settings["collection_timeout_seconds"] + 2
        while not self.stop.is_set() and time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise MonitorError("exporter_crashed",
                                   "the exporter exited early; see .runtime/" + self.config["name"] + "-exporter.log")
            try:
                with socket.create_connection(("127.0.0.1", self.config["exporter_port"]), timeout=.2):
                    return
            except OSError:
                self.stop.wait(.15)
        raise MonitorError("exporter_timeout", "the exporter did not accept connections in time; retrying")

    def get_process_details(self, gpus):
        """Enrich exporter processes with ps output; return a (code, detail) warning or None."""
        pids = sorted({proc["pid"] for gpu in gpus for proc in gpu["processes"]})
        if not pids:
            return None
        command = shlex.join(["ps", "-ww", "-p", ",".join(map(str, pids)), "-o", "pid=,user:32=,pcpu=,pmem=,etimes=,args="])
        completed = subprocess.run(["ssh", *ssh_options(self.control), self.config["ssh_host"], command],
                                   capture_output=True, text=True, timeout=8)
        if completed.returncode not in (0, 1):
            raise MonitorError("process_details_failed", "remote ps query failed with exit code %d" % completed.returncode)
        details = parse_ps(completed.stdout)
        for gpu in gpus:
            for proc in gpu["processes"]:
                proc.update(details.get(proc["pid"], {}))
        if any(proc["pid"] not in details for gpu in gpus for proc in gpu["processes"]):
            return "process_details_partial", "some GPU processes exited or their owner and command are not readable"
        return None

    def collect_once(self):
        self.ensure_exporter()
        url = "http://127.0.0.1:%d/metrics" % self.config["exporter_port"]
        with urllib.request.urlopen(url, timeout=self.settings["collection_timeout_seconds"] + 3) as response:
            body = response.read().decode()
        gpus, scalars = decode_snapshot(body)
        collected_at = scalars.get("nvidia_smi_last_collect_success_timestamp_seconds") or time.time()
        warning = warning_code = None
        if not all(gpu["processes_available"] for gpu in gpus):
            warning_code = "process_collect_failed"
            warning = "the exporter could not list compute processes, so idleness cannot be judged"
        try:
            partial = self.get_process_details(gpus)
            if partial:
                warning_code, warning = partial
        except (OSError, subprocess.TimeoutExpired, MonitorError):
            warning_code, warning = "process_details_unavailable", "process owners and commands are temporarily unavailable; GPU metrics are unaffected"
        for gpu in gpus:
            self.history[gpu["uuid"]].append({"at": collected_at, "utilization": gpu.get("utilization_gpu"),
                                               "memory_used_bytes": gpu.get("memory_used_bytes")})
            gpu["history"] = list(self.history[gpu["uuid"]])
        with self.lock:
            self.raw_metrics = body
            self.state.update(status="online", error=None, error_code=None, warning=warning, warning_code=warning_code,
                              last_success=collected_at,
                              collection_seconds=scalars.get("nvidia_smi_last_collect_duration_seconds"), gpus=gpus)

    def run(self):
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.collect_once()
            except Exception as exc:
                with self.lock:
                    self.state.update(status="offline", error=str(exc)[:500],
                                      error_code=getattr(exc, "code", "collect_error"))
            self.stop.wait(max(.2, self.settings["interval_seconds"] - (time.monotonic() - started)))

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        try:
            subprocess.run(["ssh", "-o", "ControlPath=" + self.control, "-O", "exit", self.config["ssh_host"]],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if self.log:
            self.log.close()


def load_config(path):
    path = Path(path)
    if not path.exists():
        raise ValueError("no %s found: copy config.example.json to config.json and edit the SSH hosts" % path.name)
    settings = json.loads(path.read_text())
    missing = [key for key in ("listen", "port", "interval_seconds", "collection_timeout_seconds",
                               "history_points", "servers") if key not in settings]
    if missing:
        raise ValueError("config.json is missing keys: " + ", ".join(missing) + " (see config.example.json)")
    if settings["listen"] != "127.0.0.1":
        raise ValueError("this dashboard only supports listening on 127.0.0.1")
    if settings["interval_seconds"] < 1 or not 1 <= settings["collection_timeout_seconds"] <= 30:
        raise ValueError("interval_seconds must be at least 1 and collection_timeout_seconds must be 1-30")
    if not 2 <= settings["history_points"] <= 1200:
        raise ValueError("history_points must be between 2 and 1200")
    own_users = settings.setdefault("own_users", [])
    if (not isinstance(own_users, list) or len(own_users) > 16
            or any(not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", user) for user in own_users)):
        raise ValueError("own_users must be a list of at most 16 Unix user names")
    names, ports = set(), {settings["port"]}
    for host in settings["servers"]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", host["name"]) or host["name"] in names:
            raise ValueError("server names must be unique and contain only letters, digits, underscore or hyphen")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]*", host["ssh_host"]):
            raise ValueError("ssh_host must be an SSH config alias or user@host")
        if host["exporter_port"] in ports:
            raise ValueError("every listen port must be unique")
        names.add(host["name"])
        ports.add(host["exporter_port"])
    if not names or any(not isinstance(port, int) or not 1024 <= port <= 65535 for port in ports):
        raise ValueError("at least one server is required and ports must be 1024-65535")
    return settings


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, monitors, settings, instance, **kwargs):
        self.monitors, self.settings, self.instance = monitors, settings, instance
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        super().end_headers()

    def do_GET(self):
        # Local API is read-only, contains process commands, and rejects foreign Host values.
        allowed = {"127.0.0.1:" + str(self.settings["port"]), "localhost:" + str(self.settings["port"])}
        if self.headers.get("Host") not in allowed:
            self.send_error(403)
            return
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            self.respond_json({"app": "gpu-monitor", "instance": self.instance, "generated_at": time.time(),
                               "interval_seconds": self.settings["interval_seconds"],
                               "stale_after_seconds": max(12, self.settings["interval_seconds"] * 3),
                               "history_seconds": self.settings["interval_seconds"] * self.settings["history_points"],
                               "own_users": self.settings.get("own_users", []),
                               "servers": [monitor.snapshot() for monitor in self.monitors]})
        elif path == "/healthz":
            self.respond_json({"app": "gpu-monitor", "instance": self.instance, "status": "ok"})
        elif path in ("/", "/index.html", "/app.js", "/i18n.js", "/theme.js", "/style.css", "/favicon.svg",
                      "/assets/background-dark.jpg", "/assets/background-light.jpg"):
            super().do_GET()
        else:
            self.send_error(404)

    def respond_json(self, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--instance", default="foreground")
    args = parser.parse_args()
    settings = load_config(args.config)
    runtime = ROOT / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    runtime.chmod(0o700)
    stop = threading.Event()
    monitors = [HostMonitor(host, settings, runtime, stop) for host in settings["servers"]]
    factory = partial(Handler, monitors=monitors, settings=settings, instance=args.instance)
    httpd = ThreadingHTTPServer((settings["listen"], settings["port"]), factory)
    httpd.timeout = .5
    threads = [threading.Thread(target=monitor.run, daemon=True) for monitor in monitors]
    for thread in threads:
        thread.start()
    def on_signal(signum, frame):
        stop.set()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    print("GPU Monitor: http://127.0.0.1:%d" % settings["port"], flush=True)
    try:
        while not stop.is_set():
            httpd.handle_request()
    finally:
        stop.set()
        httpd.server_close()
        for thread in threads:
            thread.join(timeout=settings["collection_timeout_seconds"] + 12)
        for monitor in monitors:
            monitor.close()


if __name__ == "__main__":
    main()
