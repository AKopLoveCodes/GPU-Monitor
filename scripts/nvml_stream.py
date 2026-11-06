#!/usr/bin/env python3
"""Stream buffered GPU utilization over stdout; standard-library-only remote helper.

Executed over SSH with `python3 -u -c <source>`. No remote files or packages are
installed. The driver owns the sample buffer; a timestamp cursor avoids repeats.
"""
import ctypes as c
import json
import resource
import time


class Value(c.Union):
    _fields_ = [("ui", c.c_uint), ("ull", c.c_ulonglong), ("d", c.c_double)]


class Sample(c.Structure):
    _fields_ = [("timestamp", c.c_ulonglong), ("value", Value)]


class NvmlError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__("NVML error %d" % code)


def check(code):
    if code != 0:
        raise NvmlError(code)


def read_samples(nvml, handle, cursor):
    size, value_type = c.c_uint(), c.c_uint()
    args = (handle, 1, c.c_ulonglong(cursor), c.byref(value_type), c.byref(size))
    code = nvml.nvmlDeviceGetSamples(*args, None)
    if code == 6:  # NVML_ERROR_NOT_FOUND: no new samples yet
        return []
    check(code)
    if not size.value:
        return []
    # Leave room for samples arriving between the size query and the read.
    size.value += 32
    buffer = (Sample * size.value)()
    check(nvml.nvmlDeviceGetSamples(*args, buffer))
    if value_type.value != 1:  # NVML_VALUE_TYPE_UNSIGNED_INT
        raise RuntimeError("unexpected NVML sample value type")
    return [[sample.timestamp / 1e6, sample.value.ui / 100]
            for sample in buffer[:size.value]]


class Collector:
    """Isolate device errors and refresh handles with bounded recovery cost."""

    def __init__(self, nvml):
        self.nvml = nvml
        self.devices = {}
        self.errors = []
        self.cursors = {}
        self.refresh_at = 0
        self.reinitialize = False
        self.last_wall_time = None

    def error_row(self, exc, index=None, uuid=None):
        code = getattr(exc, "code", None)
        if code in (1, 9, 15, 999):
            self.reinitialize = True
        row = {"error": str(exc), "error_code": code}
        if index is not None:
            row["index"] = index
        if uuid is not None:
            row["uuid"] = uuid
        return row

    def refresh(self):
        if self.reinitialize:
            self.nvml.nvmlShutdown()
            check(self.nvml.nvmlInit_v2())
            self.reinitialize = False
        count = c.c_uint()
        check(self.nvml.nvmlDeviceGetCount_v2(c.byref(count)))
        devices, errors = {}, []
        for index in range(count.value):
            try:
                handle, uuid = c.c_void_p(), c.create_string_buffer(96)
                check(self.nvml.nvmlDeviceGetHandleByIndex_v2(index, c.byref(handle)))
                check(self.nvml.nvmlDeviceGetUUID(handle, uuid, len(uuid)))
                identity = uuid.value.decode()
                devices[index] = (handle, identity[4:] if identity.startswith("GPU-") else identity)
            except NvmlError as exc:
                previous = self.devices.get(index)
                errors.append(self.error_row(exc, index, previous[1] if previous else None))
        self.devices, self.errors = devices, errors
        present = {uuid for _, uuid in devices.values()}
        self.cursors = {uuid: cursor for uuid, cursor in self.cursors.items() if uuid in present}

    def poll(self):
        wall_time = time.time()
        if self.last_wall_time is not None and wall_time < self.last_wall_time - 1:
            self.cursors.clear()
        self.last_wall_time = wall_time
        now = time.monotonic()
        if now >= self.refresh_at:
            self.refresh()
            self.refresh_at = now + 10
        rows = list(self.errors)
        for index, (handle, uuid) in self.devices.items():
            try:
                samples = read_samples(self.nvml, handle, self.cursors.get(uuid, 0))
                if samples:
                    self.cursors[uuid] = round(samples[-1][0] * 1e6)
                rows.append({"uuid": uuid, "samples": samples})
            except RuntimeError as exc:
                rows.append(self.error_row(exc, index, uuid))
        return rows


def main():
    nvml = c.CDLL("libnvidia-ml.so.1")
    check(nvml.nvmlInit_v2())
    try:
        collector = Collector(nvml)
        boot = time.monotonic()
        while True:
            started = time.monotonic()
            rows = collector.poll()
            usage = resource.getrusage(resource.RUSAGE_SELF)
            stats = {"uptime_seconds": time.monotonic() - boot,
                     "cpu_seconds": usage.ru_utime + usage.ru_stime,
                     "peak_rss_bytes": usage.ru_maxrss * 1024,
                     "query_seconds": time.monotonic() - started}
            print(json.dumps({"at": time.time(), "gpus": rows, "collector": stats}, separators=(",", ":")), flush=True)
            time.sleep(max(.05, 1 - (time.monotonic() - started)))
    finally:
        nvml.nvmlShutdown()


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
