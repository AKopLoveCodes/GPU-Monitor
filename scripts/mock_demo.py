#!/usr/bin/env python3
"""Serve the web UI with synthetic GPU data; no SSH, GPU or exporter needed.

Useful for reviewing the dashboard, taking screenshots and UI development:

    python3 scripts/mock_demo.py            # http://127.0.0.1:8799
    python3 scripts/mock_demo.py --port 9000

Append ?lang=en or ?theme=dark to the URL to preview a language or theme.
"""
import argparse
import json
import random
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTERVAL = 3
HISTORY_POINTS = 100


def make_history(now, total_bytes, base_used, utilization):
    points = []
    used = base_used
    for step in range(HISTORY_POINTS):
        at = now - (HISTORY_POINTS - 1 - step) * INTERVAL
        used = max(0, min(total_bytes, used + random.uniform(-.01, .012) * total_bytes))
        points.append({"at": at, "utilization": round(max(0, min(1, utilization + random.uniform(-.05, .05))), 3),
                       "memory_used_bytes": round(used)})
    return points


def make_gpu(index, profile):
    total = 24 * 1024 ** 3
    now = time.time()
    gpu = {"uuid": "GPU-%08x-0000-0000-0000-%012d" % (random.getrandbits(32), index), "index": index,
           "name": "NVIDIA GeForce RTX 4090", "driver_version": "570.133.20", "cuda_version": "12.8",
           "memory_total_bytes": total, "processes": [], "process_count": 0, "processes_available": True,
           "utilization_memory": profile["utilization"] * .8, "temperature_c": profile["temp"],
           "power_watts": profile["power"], "power_limit_watts": 450, "fan_ratio": .45}
    gpu["memory_used_bytes"] = profile["used"]
    gpu["memory_free_bytes"] = total - profile["used"] - 180 * 1024 ** 2
    gpu["utilization_gpu"] = profile["utilization"]
    gpu["history"] = make_history(now, total, profile["used"], profile["utilization"])
    for position, proc in enumerate(profile["processes"]):
        gpu["processes"].append({"pid": 400000 + index * 100 + position, "name": proc["command"].split()[0],
                                 "memory_bytes": proc["memory_bytes"], "user": proc["user"],
                                 "cpu_percent": proc["cpu_percent"], "host_memory_percent": 2.4,
                                 "elapsed_seconds": proc["elapsed_seconds"], "command": proc["command"]})
    gpu["process_count"] = len(gpu["processes"])
    return gpu


GB = 1024 ** 3
PROFILES = [
    {"used": int(21.8 * GB), "utilization": .62, "temp": 61, "power": 305,
     "processes": [{"user": "demo", "memory_bytes": int(21.5 * GB), "cpu_percent": 96.4, "elapsed_seconds": 3 * 86400 + 5400,
                    "command": "python train.py --config runs/resnet50.yaml --batch 256"}]},
    {"used": int(.4 * GB), "utilization": 0, "temp": 34, "power": 19, "processes": []},
    {"used": int(15.7 * GB), "utilization": .38, "temp": 55, "power": 240,
     "processes": [{"user": "liwei", "memory_bytes": int(15.2 * GB), "cpu_percent": 71.0, "elapsed_seconds": 26 * 3600,
                    "command": "python serve_vllm.py --model qwen2-7b --tensor-parallel-size 1"}]},
    {"used": int(20.5 * GB), "utilization": .91, "temp": 72, "power": 420,
     "processes": [{"user": "anna", "memory_bytes": int(20.1 * GB), "cpu_percent": 88.2, "elapsed_seconds": 7200,
                    "command": "torchrun --nproc_per_node 1 finetune.py --lora"}]},
    {"used": int(.3 * GB), "utilization": 0, "temp": 31, "power": 17, "processes": []},
    {"used": int(.5 * GB), "utilization": 0, "temp": 33, "power": 18, "processes": []},
    {"used": int(9.2 * GB), "utilization": .12, "temp": 44, "power": 90,
     "processes": [{"user": "demo", "memory_bytes": int(8.9 * GB), "cpu_percent": 12.5, "elapsed_seconds": 1500,
                    "command": "python eval.py --checkpoint last.ckpt"}]},
    {"used": int(.3 * GB), "utilization": 0, "temp": 32, "power": 16, "processes": []},
]


def make_server(name, ssh_host, offline=False):
    now = time.time()
    server = {"name": name, "ssh_host": ssh_host, "status": "offline" if offline else "online",
              "error": "the remote nvidia-smi query failed; retrying over SSH" if offline else None,
              "error_code": "collect_failed" if offline else None, "warning": None, "warning_code": None,
              "last_success": now - (90 if offline else 1), "collection_seconds": .42,
              "gpus": [make_gpu(index, profile) for index, profile in enumerate(PROFILES)]}
    if offline:
        for gpu in server["gpus"]:
            gpu["stale"] = True
    return server


def make_status():
    return {"app": "gpu-monitor", "instance": "mock", "generated_at": time.time(), "interval_seconds": INTERVAL,
            "stale_after_seconds": max(12, INTERVAL * 3), "history_seconds": INTERVAL * HISTORY_POINTS,
            "own_users": ["demo"], "servers": [make_server("gpu-01", "gpu-01"), make_server("gpu-02", "gpu-02")]}


STATIC = {"/", "/index.html", "/app.js", "/i18n.js", "/theme.js", "/style.css", "/favicon.svg",
          "/assets/background-dark.jpg", "/assets/background-light.jpg"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            body = json.dumps(make_status(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path in STATIC:
            super().do_GET()
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()
    random.seed(7)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler))
    print("GPU Monitor demo (synthetic data): http://127.0.0.1:%d  (?lang=en, ?theme=dark)" % args.port, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
