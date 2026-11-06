import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts.nvml_history import NvmlHistory, summarize
from server import HostMonitor, load_config
from test_monitor import FIXTURE


class WindowTests(unittest.TestCase):
    def history(self):
        return NvmlHistory('test', 'unused', Path('/tmp'), threading.Event())

    def test_sample_age_includes_time_since_frame_and_expires(self):
        history = self.history()
        with patch('scripts.nvml_history.time.monotonic', return_value=10):
            history.ingest({'at': 100, 'gpus': [{'uuid': 'abc', 'samples': [[97, 0], [97.1, .8]]}]})
        with patch('scripts.nvml_history.time.monotonic', return_value=10.05):
            self.assertAlmostEqual(history.snapshot('abc')['sample_age_seconds'], 2.95)
        with patch('scripts.nvml_history.time.monotonic', return_value=13.4):
            self.assertIsNone(history.snapshot('abc'))

    def test_window_advances_without_new_frames(self):
        history = self.history()
        with patch('scripts.nvml_history.time.monotonic', return_value=10):
            history.ingest({'at': 100, 'gpus': [{'uuid': 'abc', 'samples': [[99, 0], [100, 1]]}]})
        result = history.snapshot('abc', interval=1, now=10.5)
        self.assertAlmostEqual(result['sample_age_seconds'], .5)
        self.assertAlmostEqual(result['interval']['coverage'], .5)

    def test_delayed_frames_do_not_make_old_samples_fresh(self):
        history = self.history()
        with patch('scripts.nvml_history.time.monotonic', return_value=10):
            history.ingest({'at': 100, 'gpus': [{'uuid': 'abc', 'samples': [[99, 0], [100, 0]]}]})
        with patch('scripts.nvml_history.time.monotonic', return_value=20):
            history.ingest({'at': 101, 'gpus': [{'uuid': 'abc', 'samples': [[101, 1]]}]})
            self.assertIsNone(history.snapshot('abc'))

    def test_remote_clock_rollback_resets_old_samples(self):
        history = self.history()
        with patch('scripts.nvml_history.time.monotonic', return_value=10):
            history.ingest({'at': 100, 'gpus': [{'uuid': 'abc', 'samples': [[99, 1], [100, 1]]}]})
        with patch('scripts.nvml_history.time.monotonic', return_value=11):
            history.ingest({'at': 90, 'gpus': [{'uuid': 'abc', 'samples': [[89, 0], [90, 0]]}]})
            result = history.snapshot('abc')
            self.assertEqual(result['window']['peak'], 0)
            self.assertEqual(result['sample_age_seconds'], 0)

    def test_short_burst_between_scrapes_is_retained(self):
        # A point scrape at 0s and 3s sees zero; buffered samples see a burst.
        points = [(i / 5, .8 if i == 7 else 0) for i in range(16)]
        result = summarize(points, 3, 3)
        self.assertAlmostEqual(result['mean'], .8 * .2 / 3)
        self.assertEqual(result['peak'], .8)
        self.assertEqual(result['coverage'], 1)

    def test_time_weighting_and_partial_edge(self):
        result = summarize([(0, 0), (.2, 1), (1, 0)], 1, .9)
        self.assertAlmostEqual(result['mean'], .1 / .9)
        self.assertAlmostEqual(result['coverage'], 1)

    def test_disconnection_is_unknown_not_zero_or_carried_value(self):
        result = summarize([(0, 1), (1, 1), (9, 0), (10, 0)], 10, 10)
        self.assertAlmostEqual(result['coverage'], .2)
        self.assertEqual(result['mean'], .5)
        empty = summarize([(1, 0)], 5, 3)
        self.assertIsNone(empty['mean'])
        self.assertIsNone(empty['peak'])
        self.assertEqual(empty['coverage'], 0)

    def test_deduplication_staleness_clock_skew_and_device_errors(self):
        history = NvmlHistory('test', 'unused', Path('/tmp'), threading.Event())
        payload = {'at': 100, 'gpus': [{'uuid': 'abc', 'samples': [[99, 0], [99.2, .8], [100, 0]]}]}
        with patch('scripts.nvml_history.time.monotonic', return_value=10):
            history.ingest(payload)
            history.ingest(payload)
            self.assertEqual(len(history.samples['abc']), 3)
            self.assertEqual(history.snapshot('abc')['window']['peak'], .8)
        with patch('scripts.nvml_history.time.monotonic', return_value=14):
            self.assertIsNone(history.snapshot('abc'))
        with patch('scripts.nvml_history.time.monotonic', return_value=15):
            history.ingest({'at': 105, 'gpus': [{'uuid': 'abc', 'error': 'unsupported'}]})
            self.assertIsNone(history.snapshot('abc'))
        with patch('scripts.nvml_history.time.monotonic', return_value=16):
            history.ingest({'at': 106, 'gpus': [{'uuid': 'abc', 'samples': [[105, 0], [106, .8]]}]})
            self.assertIsNotNone(history.snapshot('abc'))


class IntegrationTests(unittest.TestCase):
    def monitor(self, directory):
        return HostMonitor({'name': 'test', 'ssh_host': 'test', 'exporter_port': 19000},
                           {'interval_seconds': 3, 'collection_timeout_seconds': 10, 'history_points': 5},
                           Path(directory), threading.Event())

    def test_delayed_collection_keeps_burst_and_actual_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = self.monitor(directory)
            with patch.object(monitor, 'ensure_exporter'), patch.object(monitor, 'get_process_details', return_value=None), \
                    patch('server.urllib.request.urlopen') as urlopen, patch('server.time.monotonic') as monotonic, \
                    patch('server.time.time') as wall:
                urlopen.return_value.__enter__.return_value.read.return_value = FIXTURE.encode()
                monotonic.return_value, wall.return_value = 3, 1003
                monitor.nvml.ingest({'at': 103, 'gpus': [{'uuid': 'abc', 'samples': [[102, 0], [103, 0]]}]})
                monitor.collect_once()
                # ps/SSH delays the next successful collection by seven seconds.
                monotonic.return_value, wall.return_value = 10, 1010
                points = [[103 + i / 5, .8 if 9 <= i <= 11 else 0] for i in range(36)]
                monitor.nvml.ingest({'at': 110, 'gpus': [{'uuid': 'abc', 'samples': points}]})
                monitor.collect_once()
                gpu = monitor.state['gpus'][0]
                point = gpu['history'][-1]
                self.assertAlmostEqual(point['utilization'], .8 * .6 / 7)
                self.assertEqual(gpu['utilization_interval']['seconds'], 7)
                self.assertEqual((point['utilization_start'], point['utilization_end']), (1003, 1010))
                self.assertAlmostEqual(point['utilization_coverage'], 1)

    def test_each_history_point_keeps_its_source_across_fallback_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = self.monitor(directory)
            with patch.object(monitor, 'ensure_exporter'), patch.object(monitor, 'get_process_details', return_value=None), \
                    patch('server.urllib.request.urlopen') as urlopen, patch('server.time.monotonic') as mono:
                urlopen.return_value.__enter__.return_value.read.return_value = FIXTURE.encode()
                for now, row in [(10, {'samples': [[99, 1], [100, 1]]}),
                                 (13, {'error': 'NVML error 3', 'error_code': 3}),
                                 (16, {'samples': [[105, 0], [106, 0]]})]:
                    mono.return_value = now
                    monitor.nvml.ingest({'at': now + 90, 'gpus': [dict(uuid='abc', **row)]})
                    monitor.collect_once()
                    if now == 13:
                        gpu = monitor.state['gpus'][0]
                        self.assertEqual(gpu['utilization_history_status'], 'unsupported')
                        self.assertEqual(gpu['utilization_status'], 'available')
                        self.assertIsNone(gpu['utilization_sample_age_seconds'])
                points = monitor.state['gpus'][0]['history']
                self.assertEqual([p['utilization_source'] for p in points], ['nvml_samples', 'exporter', 'nvml_samples'])
                self.assertIsNone(points[1]['utilization_coverage'])
                self.assertEqual(points[1]['utilization_start'], points[1]['utilization_end'])
                self.assertGreater(points[2]['utilization_coverage'], 0)
                self.assertLess(points[2]['utilization_coverage'], 1)

    def test_unsupported_and_temporarily_missing_utilization_stay_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = self.monitor(directory)
            with patch.object(monitor, 'ensure_exporter'), patch.object(monitor, 'get_process_details', return_value=None), \
                    patch('server.urllib.request.urlopen') as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = FIXTURE.replace(
                    'nvidia_smi_utilization_gpu_ratio{uuid="abc"} 0',
                    'nvidia_smi_utilization_gpu_ratio{uuid="abc"} NaN').encode()
                monitor.nvml.ingest({'at': 100, 'gpus': [{'uuid': 'abc', 'error': 'unsupported', 'error_code': 3},
                                                        {'uuid': 'def', 'error': 'lost', 'error_code': 15},
                                                        {'index': 2, 'error': 'UUID unavailable', 'error_code': 15}]})
                monitor.collect_once()
                first, second = monitor.state['gpus']
                self.assertEqual(first['utilization_status'], 'unsupported')
                self.assertEqual(second['utilization_status'], 'unavailable')
                self.assertTrue(all(g['utilization_source'] == 'unavailable' for g in (first, second)))
                self.assertTrue(all(g['history'][-1]['utilization'] is None for g in (first, second)))
                self.assertEqual(monitor.nvml.diagnostics()['device_errors']['abc']['error_code'], 3)

    def test_nvml_primary_exporter_fallback_and_interval_trend(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = {'interval_seconds': 3, 'collection_timeout_seconds': 10, 'history_points': 5}
            monitor = HostMonitor({'name': 'test', 'ssh_host': 'test', 'exporter_port': 19000}, settings, Path(directory), threading.Event())
            monitor.nvml.ingest({'at': 100, 'gpus': [{'uuid': 'abc', 'samples': [[99, 0], [99.2, .8], [100, 0]]}]})
            with patch.object(monitor, 'ensure_exporter'), patch.object(monitor, 'get_process_details'), \
                    patch('server.urllib.request.urlopen') as urlopen:
                monitor.get_process_details.return_value = None
                urlopen.return_value.__enter__.return_value.read.return_value = FIXTURE.encode()
                monitor.collect_once()
                gpus = monitor.state['gpus']
                self.assertEqual(gpus[0]['utilization_source'], 'nvml_samples')
                self.assertEqual(gpus[0]['utilization_gpu_exporter'], 0)
                self.assertEqual(gpus[0]['utilization_window']['peak'], .8)
                self.assertGreater(gpus[0]['history'][-1]['utilization'], 0)
                self.assertEqual(gpus[1]['utilization_source'], 'unavailable')
                self.assertEqual(gpus[1]['utilization_status'], 'unavailable')
                self.assertEqual(monitor.state['warning_code'], 'nvml_fallback')
                monitor.nvml.ingest({'at': 105, 'gpus': []})
                monitor.collect_once()
                gpu = monitor.state['gpus'][0]
                self.assertEqual(gpu['utilization_source'], 'exporter')
                self.assertIsNone(gpu['utilization_window'])
                self.assertEqual(gpu['utilization_gpu'], 0)

    def test_backend_config_and_legacy_default(self):
        settings = json.loads(Path('config.example.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            settings.pop('utilization_backend')
            path.write_text(json.dumps(settings))
            self.assertEqual(load_config(path)['utilization_backend'], 'nvml')
            settings['utilization_backend'] = 'typo'
            path.write_text(json.dumps(settings))
            with self.assertRaisesRegex(ValueError, 'utilization_backend'):
                load_config(path)
            settings['utilization_backend'] = 'exporter'
            path.write_text(json.dumps(settings))
            monitor = HostMonitor(settings['servers'][0], load_config(path), Path(directory), threading.Event())
            self.assertIsNone(monitor.nvml)


if __name__ == '__main__':
    unittest.main()
