import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from server import HostMonitor, MonitorError, control_socket_path, decode_snapshot, load_config, parse_metrics, parse_ps

FIXTURE = '''# HELP nvidia_smi_memory_used_bytes GPU memory
nvidia_smi_last_collect_success 1
nvidia_smi_last_collect_success_timestamp_seconds 1793564602
nvidia_smi_compute_apps_last_collect_success 1
nvidia_smi_gpu_info{uuid="abc",index="0",name="RTX 4090",driver_version="595",cuda_version="13.2"} 1
nvidia_smi_gpu_info{uuid="def",index="1",name="RTX 4090"} 1
nvidia_smi_memory_total_bytes{uuid="abc"} 25757220864
nvidia_smi_memory_used_bytes{uuid="abc"} 23397924864
nvidia_smi_memory_free_bytes{uuid="abc"} 1855979520
nvidia_smi_utilization_gpu_ratio{uuid="abc"} 0
nvidia_smi_temperature_gpu{uuid="abc"} NaN
nvidia_smi_compute_apps{uuid="abc"} 1
nvidia_smi_compute_apps{uuid="def"} 0
nvidia_smi_compute_app_used_memory_bytes{uuid="abc",pid="123",process_name="python"} 23387439104
nvidia_smi_compute_app_info{uuid="abc",pid="123",process_name="python"} 1
'''


class MetricsTests(unittest.TestCase):
    def test_gpu_and_processes_are_joined_by_uuid(self):
        gpus, _ = decode_snapshot(FIXTURE)
        self.assertEqual([gpu['index'] for gpu in gpus], [0, 1])
        self.assertEqual(gpus[0]['process_count'], 1)
        self.assertEqual(gpus[0]['processes'][0]['memory_bytes'], 23387439104)
        self.assertEqual(gpus[1]['processes'], [])
        self.assertEqual(gpus[1]['process_count'], 0)
        self.assertIsNone(gpus[0]['temperature_c'])
        json.dumps(gpus, allow_nan=False)

    def test_free_memory_keeps_driver_reservation(self):
        gpus, _ = decode_snapshot(FIXTURE)
        gpu = gpus[0]
        self.assertEqual(gpu['memory_free_bytes'], 1855979520)
        self.assertLess(gpu['memory_free_bytes'], gpu['memory_total_bytes'] - gpu['memory_used_bytes'])

    def test_failed_process_query_is_unknown_not_idle(self):
        body = FIXTURE.replace('nvidia_smi_compute_apps_last_collect_success 1', 'nvidia_smi_compute_apps_last_collect_success 0')
        gpus, _ = decode_snapshot(body)
        self.assertTrue(all(gpu['process_count'] is None for gpu in gpus))
        self.assertTrue(all(not gpu['processes_available'] for gpu in gpus))

    def test_failed_or_empty_collection_rejected(self):
        with self.assertRaises(RuntimeError):
            decode_snapshot(FIXTURE.replace('nvidia_smi_last_collect_success 1', 'nvidia_smi_last_collect_success 0'))
        with self.assertRaises(RuntimeError):
            decode_snapshot('nvidia_smi_last_collect_success 1\n')

    def test_escaped_process_labels(self):
        samples = parse_metrics(r'x{process_name="a\\b\"c\nnext"} 1')
        self.assertEqual(samples[0][1]['process_name'], 'a\\b"c\nnext')

    def test_process_command_preserves_spaces_and_percent(self):
        details = parse_ps(' 123 demo 105.2 3.1 1234 python train.py --name a b\n')
        self.assertEqual(details[123]['command'], 'python train.py --name a b')
        self.assertEqual(details[123]['cpu_percent'], 105.2)


class WorkerTests(unittest.TestCase):
    def test_failure_marks_old_data_stale_and_recovery_clears_error(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {'name': 'Test', 'ssh_host': 'Test', 'exporter_port': 19000}
            settings = {'interval_seconds': 3, 'collection_timeout_seconds': 10, 'history_points': 2, 'utilization_backend': 'exporter'}
            stop = threading.Event()
            monitor = HostMonitor(config, settings, Path(directory), stop)
            monitor.state.update(status='online', last_success=100, gpus=[{'index': 0}])
            def fail():
                stop.set()
                raise OSError('SSH disconnected')
            with patch.object(monitor, 'collect_once', side_effect=fail):
                monitor.run()
            self.assertEqual(monitor.snapshot()['status'], 'offline')
            self.assertTrue(monitor.snapshot()['gpus'][0]['stale'])
            self.assertEqual(monitor.snapshot()['last_success'], 100)
            with patch.object(monitor, 'ensure_exporter'), patch.object(monitor, 'get_process_details', return_value=None), patch('server.urllib.request.urlopen') as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = FIXTURE.encode()
                for _ in range(3):
                    monitor.collect_once()
            with patch('server.time.time', return_value=1793564603):
                recovered = monitor.snapshot()
            self.assertEqual(recovered['status'], 'online')
            self.assertIsNone(recovered['error'])
            self.assertFalse(recovered['gpus'][0]['stale'])
            self.assertEqual(len(recovered['gpus'][0]['history']), 2)

    def test_unresponsive_exporter_is_recycled_before_the_next_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = HostMonitor({'name': 'Test', 'ssh_host': 'Test', 'exporter_port': 19000},
                                  {'interval_seconds': 3, 'collection_timeout_seconds': 10, 'history_points': 2},
                                  Path(directory), threading.Event())
            with patch.object(monitor, 'ensure_exporter'), patch.object(monitor, 'stop_exporter') as stopped, \
                    patch('server.urllib.request.urlopen', side_effect=OSError('connection reset')):
                with self.assertRaises(MonitorError) as caught:
                    monitor.collect_once()
            self.assertEqual(caught.exception.code, 'exporter_unavailable')
            stopped.assert_called_once()


class ConfigTests(unittest.TestCase):
    def test_rejects_conflicting_ports_and_shell_options(self):
        base = json.loads((Path(__file__).resolve().parents[1] / 'config.example.json').read_text())
        cases = []
        config = copy.deepcopy(base); config['servers'][0]['exporter_port'] = 19000; config['servers'][1]['exporter_port'] = 19000; cases.append(config)
        config = copy.deepcopy(base); config['servers'][0]['ssh_host'] = '-oProxyCommand=bad'; cases.append(config)
        config = copy.deepcopy(base); config['listen'] = '0.0.0.0'; cases.append(config)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            for config in cases:
                path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_rejects_invalid_own_users(self):
        base = json.loads((Path(__file__).resolve().parents[1] / 'config.example.json').read_text())
        cases = []
        config = copy.deepcopy(base); config['own_users'] = 'demo'; cases.append(config)
        config = copy.deepcopy(base); config['own_users'] = ['bad name']; cases.append(config)
        config = copy.deepcopy(base); config['own_users'] = ['a'] * 17; cases.append(config)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            for config in cases:
                path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_auto_exporter_ports_and_bad_server_shapes(self):
        base = json.loads((Path(__file__).resolve().parents[1] / 'config.example.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(base))
            settings = load_config(path)
            self.assertNotIn('exporter_port', settings['servers'][0])
            malformed = copy.deepcopy(base)
            malformed['servers'] = [{'name': 'only-a-name'}]
            path.write_text(json.dumps(malformed))
            with self.assertRaisesRegex(ValueError, 'name and ssh_host'):
                load_config(path)

    def test_control_socket_uses_a_short_digest_for_long_names(self):
        path = control_socket_path(Path('/tmp'), 'host-' + 'very-long-name-' * 20)
        self.assertLessEqual(len(path.encode()), 90)
        self.assertRegex(Path(path).name, r'^ssh-[0-9a-f]{20}$')

    def test_missing_config_explains_how_to_create_it(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'config.example.json'):
                load_config(Path(directory) / 'config.json')

    def test_collection_failures_carry_localizable_codes(self):
        with self.assertRaises(MonitorError) as caught:
            decode_snapshot(FIXTURE.replace('nvidia_smi_last_collect_success 1', 'nvidia_smi_last_collect_success 0'))
        self.assertEqual(caught.exception.code, 'collect_failed')
        with self.assertRaises(MonitorError) as empty:
            decode_snapshot('nvidia_smi_last_collect_success 1\n')
        self.assertEqual(empty.exception.code, 'no_gpu')

    def test_stale_snapshot_exposes_error_code(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = HostMonitor({'name': 'Test', 'ssh_host': 'Test', 'exporter_port': 19000},
                                  {'interval_seconds': 3, 'collection_timeout_seconds': 10, 'history_points': 2},
                                  Path(directory), threading.Event())
            monitor.state.update(status='online', last_success=1, gpus=[{'index': 0}])
            snapshot = monitor.snapshot()
            self.assertEqual(snapshot['error_code'], 'stale')
            self.assertTrue(snapshot['gpus'][0]['stale'])


if __name__ == '__main__':
    unittest.main()
