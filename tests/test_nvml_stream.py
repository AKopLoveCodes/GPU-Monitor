import ctypes as c
import unittest
from unittest.mock import patch

from scripts.nvml_stream import Collector, NvmlError, read_samples


class FakeNvml:
    def __init__(self):
        self.lookup_errors = {}
        self.uuid_errors = {}
        self.count = 2
        self.enumerations = self.initializations = self.shutdowns = 0

    def nvmlInit_v2(self):
        self.initializations += 1
        return 0

    def nvmlShutdown(self):
        self.shutdowns += 1
        return 0

    def nvmlDeviceGetCount_v2(self, count):
        self.enumerations += 1
        count._obj.value = self.count
        return 0

    def nvmlDeviceGetHandleByIndex_v2(self, index, handle):
        handle._obj.value = 10 * self.initializations + index + 1
        return self.lookup_errors.get(index, 0)

    def nvmlDeviceGetUUID(self, handle, uuid, size):
        index = (handle.value - 1) % 10
        uuid.value = ('GPU-card%d' % index).encode()
        return self.uuid_errors.get(index, 0)


class CollectorTests(unittest.TestCase):
    def test_one_failed_device_does_not_stop_healthy_device_and_is_retried(self):
        for errors in ('lookup_errors', 'uuid_errors'):
            with self.subTest(stage=errors):
                nvml = FakeNvml()
                getattr(nvml, errors)[1] = 15
                collector = Collector(nvml)
                with patch('scripts.nvml_stream.time.monotonic', return_value=0), \
                        patch('scripts.nvml_stream.read_samples', return_value=[[100, .5]]):
                    rows = collector.poll()
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]['error_code'], 15)
                self.assertEqual(rows[1], {'uuid': 'card0', 'samples': [[100, .5]]})
                getattr(nvml, errors).clear()
                with patch('scripts.nvml_stream.time.monotonic', return_value=10), \
                        patch('scripts.nvml_stream.read_samples', return_value=[[110, 0]]):
                    rows = collector.poll()
                self.assertEqual({row['uuid'] for row in rows}, {'card0', 'card1'})
                self.assertTrue(all('samples' in row for row in rows))
                self.assertEqual(nvml.initializations, 1)

    def test_lost_handle_reinitializes_and_recovers_without_resetting_good_cursor(self):
        nvml = FakeNvml()
        collector = Collector(nvml)
        def first_read(nvml, handle, cursor):
            if handle.value == 2:
                raise NvmlError(15)
            return [[100, .5]]
        with patch('scripts.nvml_stream.time.monotonic', return_value=0), \
                patch('scripts.nvml_stream.read_samples', side_effect=first_read):
            rows = collector.poll()
        self.assertEqual(rows[1]['error_code'], 15)
        with patch('scripts.nvml_stream.time.monotonic', return_value=1), \
                patch('scripts.nvml_stream.read_samples', side_effect=first_read):
            collector.poll()
        self.assertEqual(nvml.initializations, 0, 'Recovery is rate limited')
        seen = []
        def recovered_read(nvml, handle, cursor):
            seen.append((handle.value, cursor))
            return [[110, .2]]
        with patch('scripts.nvml_stream.time.monotonic', return_value=10), \
                patch('scripts.nvml_stream.read_samples', side_effect=recovered_read):
            rows = collector.poll()
        self.assertEqual(nvml.initializations, 1)
        self.assertEqual(nvml.shutdowns, 1)
        self.assertEqual(seen, [(11, 100000000), (12, 0)])
        self.assertTrue(all('samples' in row for row in rows))

    def test_unsupported_device_does_not_force_library_reinitialization(self):
        nvml = FakeNvml()
        collector = Collector(nvml)
        with patch('scripts.nvml_stream.read_samples', side_effect=NvmlError(3)), \
                patch('scripts.nvml_stream.time.monotonic') as mono:
            for now in (0, 10, 20):
                mono.return_value = now
                rows = collector.poll()
                self.assertTrue(all(row['error_code'] == 3 for row in rows))
        self.assertEqual(nvml.initializations, 0)
        self.assertEqual(nvml.enumerations, 3)

    def test_periodic_enumeration_finds_new_devices(self):
        nvml = FakeNvml()
        collector = Collector(nvml)
        with patch('scripts.nvml_stream.read_samples', return_value=[]), \
                patch('scripts.nvml_stream.time.monotonic') as mono:
            mono.return_value = 0
            self.assertEqual(len(collector.poll()), 2)
            nvml.count = 3
            mono.return_value = 10
            self.assertEqual(len(collector.poll()), 3)

    def test_remote_clock_rollback_clears_sample_cursor(self):
        collector = Collector(FakeNvml())
        with patch('scripts.nvml_stream.time.time') as wall, \
                patch('scripts.nvml_stream.time.monotonic', return_value=0), \
                patch('scripts.nvml_stream.read_samples', return_value=[[100, 0]]):
            wall.return_value = 100
            collector.poll()
        with patch('scripts.nvml_stream.time.time', return_value=90), \
                patch('scripts.nvml_stream.time.monotonic', return_value=1), \
                patch('scripts.nvml_stream.read_samples', return_value=[]) as read:
            collector.poll()
        self.assertTrue(all(call.args[2] == 0 for call in read.call_args_list))


class ReadSamplesTests(unittest.TestCase):
    def test_ctypes_buffer_cursor_and_percentage_conversion(self):
        class Nvml:
            def nvmlDeviceGetSamples(self, handle, kind, cursor, value_type, count, buffer):
                self.last_cursor = cursor.value
                self.kind = kind
                value_type._obj.value = 1
                if buffer is None:
                    count._obj.value = 2
                else:
                    buffer[0].timestamp, buffer[0].value.ui = 99000000, 0
                    buffer[1].timestamp, buffer[1].value.ui = 100000000, 80
                    count._obj.value = 2
                return 0
        nvml = Nvml()
        self.assertEqual(read_samples(nvml, c.c_void_p(1), 98000000), [[99, 0], [100, .8]])
        self.assertEqual((nvml.last_cursor, nvml.kind), (98000000, 1))

    def test_no_new_samples_is_not_a_device_failure(self):
        class Nvml:
            def nvmlDeviceGetSamples(self, *args):
                return 6
        self.assertEqual(read_samples(Nvml(), c.c_void_p(1), 0), [])


if __name__ == '__main__':
    unittest.main()
