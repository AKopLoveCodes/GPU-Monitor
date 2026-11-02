"""SSH stream lifecycle and time-weighted utilization windows."""
from collections import deque
import json
import math
import os
from pathlib import Path
import shlex
import select
import subprocess
import threading
import time

from scripts.ssh_smi import ssh_options


def summarize(samples, end, seconds):
    """Each driver sample describes the interval ending at its timestamp.

    Exclude unknown intervals (including gaps >1.5s) rather than filling them
    with zero or carrying the preceding value across a disconnect.
    """
    start = end - seconds
    covered = weighted = 0.0
    values = []
    for previous, current in zip(samples, samples[1:]):
        at, value = current
        delta = at - previous[0]
        overlap = max(0.0, min(end, at) - max(start, previous[0]))
        if 0 < delta <= 1.5 and overlap > 0:
            covered += overlap
            weighted += overlap * value
            values.append(value)
    return {"seconds": seconds, "mean": weighted / covered if covered else None,
            "peak": max(values) if values else None,
            "coverage": min(1.0, covered / seconds), "samples": len(values)}


class NvmlHistory:
    def __init__(self, host, control, runtime, stop):
        self.host, self.control, self.runtime, self.stop = host, control, runtime, stop
        self.lock = threading.Lock()
        self.thread = self.process = None
        self.closed = threading.Event()
        self.samples = {}
        self.available = set()
        self.remote_at = self.received_at = None
        self.stats = {}

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def ingest(self, payload):
        at = float(payload["at"])
        if not math.isfinite(at):
            raise ValueError("invalid stream timestamp")
        with self.lock:
            available = set()
            for gpu in payload["gpus"]:
                uuid = gpu["uuid"]
                if "error" in gpu:
                    continue
                points = self.samples.setdefault(uuid, deque(maxlen=4000))
                for timestamp, value in gpu["samples"]:
                    if not (math.isfinite(timestamp) and math.isfinite(value) and 0 <= value <= 1):
                        raise ValueError("invalid utilization sample")
                    if timestamp > at + 1:
                        raise ValueError("sample timestamp is in the future")
                    if not points or timestamp > points[-1][0]:
                        points.append((timestamp, value))
                while points and points[0][0] < at - 65:
                    points.popleft()
                available.add(uuid)
            self.available = available
            self.remote_at, self.received_at = at, time.monotonic()
            self.stats = payload.get("collector", {})

    def snapshot(self, uuid, interval=3):
        with self.lock:
            if (self.received_at is None or time.monotonic() - self.received_at > 3.5
                    or uuid not in self.available):
                return None
            points = list(self.samples.get(uuid, ()))
            end = self.remote_at
        if not points or not 0 <= end - points[-1][0] <= 3:
            return None
        return {"latest": points[-1][1], "window": summarize(points, end, 30),
                "interval": summarize(points, end, interval),
                "sample_age_seconds": end - points[-1][0]}

    def diagnostics(self):
        with self.lock:
            return dict(self.stats, stream_age_seconds=(time.monotonic() - self.received_at
                        if self.received_at is not None else None))

    def _run(self):
        source = Path(__file__).with_name("nvml_stream.py").read_text()
        command = shlex.join(["python3", "-u", "-c", source])
        # The app never installs a remote daemon; this child exits with its SSH
        # channel. Failed starts retry slowly while the exporter remains usable.
        with open(self.runtime / (self.host + "-nvml.log"), "a") as log:
            while not self.closed.is_set() and not self.stop.is_set():
                try:
                    process = subprocess.Popen(["ssh", *ssh_options(self.control, multiplex=False), self.host, command],
                                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                               stderr=log, bufsize=0)
                    with self.lock:
                        self.process = process
                    try:
                        pending = b""
                        deadline = time.monotonic() + 12
                        while not self.closed.is_set() and not self.stop.is_set():
                            ready, _, _ = select.select([process.stdout], [], [], 1)
                            if not ready:
                                if time.monotonic() > deadline:
                                    raise ValueError("NVML stream stalled; reconnecting")
                                continue
                            chunk = os.read(process.stdout.fileno(), 65536)
                            if not chunk:
                                break
                            pending += chunk
                            if len(pending) > 2 * 1024 * 1024:
                                raise ValueError("NVML stream frame too large")
                            while b"\n" in pending:
                                line, pending = pending.split(b"\n", 1)
                                self.ingest(json.loads(line))
                                deadline = time.monotonic() + 6
                    finally:
                        self._terminate(process)
                        process.stdout.close()
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    log.write(str(exc) + "\n")
                    log.flush()
                with self.lock:
                    self.available.clear()
                if self.closed.wait(10) or self.stop.is_set():
                    break

    @staticmethod
    def _terminate(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def close(self):
        self.closed.set()
        with self.lock:
            process = self.process
        if process is not None:
            self._terminate(process)
        if self.thread is not None:
            self.thread.join(timeout=3)
