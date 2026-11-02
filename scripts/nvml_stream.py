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


def check(code):
    if code != 0:
        raise RuntimeError("NVML error %d" % code)


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


def main():
    nvml = c.CDLL("libnvidia-ml.so.1")
    check(nvml.nvmlInit_v2())
    try:
        count = c.c_uint()
        check(nvml.nvmlDeviceGetCount_v2(c.byref(count)))
        devices = []
        for index in range(count.value):
            handle, uuid = c.c_void_p(), c.create_string_buffer(96)
            check(nvml.nvmlDeviceGetHandleByIndex_v2(index, c.byref(handle)))
            check(nvml.nvmlDeviceGetUUID(handle, uuid, len(uuid)))
            devices.append((handle, uuid.value.decode()[4:]))
        cursors = {}
        boot = time.monotonic()
        while True:
            started = time.monotonic()
            rows = []
            for handle, uuid in devices:
                try:
                    samples = read_samples(nvml, handle, cursors.get(uuid, 0))
                    if samples:
                        cursors[uuid] = round(samples[-1][0] * 1e6)
                    rows.append({"uuid": uuid, "samples": samples})
                except RuntimeError as exc:
                    rows.append({"uuid": uuid, "error": str(exc)})
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
