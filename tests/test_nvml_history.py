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
        history.ingest({'at': 101, 'gpus': [{'uuid': 'abc', 'error': 'unsupported'}]})
        self.assertIsNone(history.snapshot('abc'))
        history.ingest(payload)
        self.assertIsNotNone(history.snapshot('abc'))


class IntegrationTests(unittest.TestCase):
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
                self.assertEqual(gpus[1]['utilization_source'], 'exporter')
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
