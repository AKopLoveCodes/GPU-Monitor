import json
from functools import partial
from http.server import ThreadingHTTPServer
import tempfile
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts.agent_manager import AgentManager
from server import Handler


class Cache:
    def __init__(self):
        self.resets = []
    def reset(self, status='connecting'):
        self.resets.append(status)
    def refresh(self):
        pass


class Monitor:
    def __init__(self, name):
        self.config = {'name': name, 'ssh_host': name}
        self.control = '/tmp/test-control'
        self.usage = Cache()


class JobTests(unittest.TestCase):
    def test_duplicate_submission_rejected_and_partial_failure_is_visible(self):
        monitors = [Monitor('a'), Monitor('b')]
        manager = AgentManager(monitors, {})
        release = threading.Event()
        def operate(config, settings, control, action, progress):
            release.wait(2)
            if config['name'] == 'b':
                raise RuntimeError('permission denied')
            return {'status': 'recording'}
        with patch('scripts.agent_manager.operate', side_effect=operate):
            job = manager.submit('install', ['a', 'b'])
            with self.assertRaises(RuntimeError):
                manager.submit('install', ['a'])
            release.set()
            deadline = time.monotonic() + 3
            while manager.snapshot()[0]['status'] == 'running' and time.monotonic() < deadline:
                time.sleep(.01)
        result = manager.snapshot()[0]
        self.assertEqual(result['id'], job['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['results']['a']['step'], 'done')
        self.assertIn('permission denied', result['results']['b']['error'])
        self.assertEqual(monitors[0].usage.resets, ['connecting'])
        manager.forget(['a', 'b'])
        self.assertEqual(manager.snapshot(), [])

    def test_unknown_servers_and_actions_never_execute(self):
        manager = AgentManager([Monitor('a')], {})
        for action, servers in [('install', ['x']), ('install', []), ('install', ['a', 'a']), ('shell', ['a'])]:
            with self.assertRaises(ValueError):
                manager.submit(action, servers)


class DeploymentApiTests(unittest.TestCase):
    def setUp(self):
        class Jobs:
            def __init__(self):
                self.calls = []
            def submit(self, action, servers):
                self.calls.append((action, servers))
                if servers != ['a']:
                    raise ValueError()
                return {'id': 'test', 'status': 'running'}
            def snapshot(self):
                return []
            def forget(self, names):
                self.calls.append(('forget', names))
        self.jobs = Jobs()
        self.monitor = Monitor('a')
        settings = {'port': 0}
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, monitors=[self.monitor], settings=settings, instance='test', agents=self.jobs))
        settings['port'] = self.httpd.server_port
        self.url = 'http://127.0.0.1:%d' % self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()

    def request(self, path='/api/agents', extra=None, payload=None):
        headers = {'Content-Type': 'application/json', 'X-GPU-Monitor': '1'}
        headers.update(extra or {})
        return Request(self.url + path, data=json.dumps(payload or {'action': 'install', 'servers': ['a']}).encode(), headers=headers, method='POST')

    def test_json_job_returns_immediately_and_cross_origin_is_rejected(self):
        with urlopen(self.request(extra={'Origin': self.url})) as response:
            self.assertEqual(response.status, 202)
            self.assertEqual(json.load(response)['status'], 'running')
        for headers in ({'Origin': 'https://untrusted.example'}, {'X-GPU-Monitor': ''}, {'Sec-Fetch-Site': 'cross-site'}):
            with self.assertRaises(HTTPError) as error:
                urlopen(self.request(extra=headers))
            self.assertEqual(error.exception.code, 403)
        self.assertEqual(len(self.jobs.calls), 1)

    def test_script_cache_reset_forgets_data_and_completed_jobs(self):
        with urlopen(self.request('/api/usage/cache', payload={'servers': ['a']})) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.monitor.usage.resets, ['connecting'])
        self.assertIn(('forget', ['a']), self.jobs.calls)


class CleanupTests(unittest.TestCase):
    def test_clear_keeps_code_and_rollback_removes_deployment(self):
        from scripts import agent_remote
        class Result:
            stdout = 'inactive'
            returncode = 3
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'data'; root.mkdir()
            home = Path(directory)/'home'; home.mkdir()
            (root/'usage.sqlite3').write_bytes(b'test database')
            (root/'deployment.json').write_text('{"linger_changed":false}')
            with patch.object(agent_remote, 'ROOT', root), patch.object(agent_remote.Path, 'home', return_value=home), patch.object(agent_remote, 'run', return_value=Result()), patch.object(agent_remote, 'set_linger') as linger:
                self.assertEqual(agent_remote.cleanup('clear-data')['status'], 'cleared')
                self.assertFalse((root/'usage.sqlite3').exists())
                self.assertTrue((root/'deployment.json').exists())
                self.assertEqual(agent_remote.cleanup('rollback')['status'], 'not_installed')
                self.assertFalse(root.exists())
                linger.assert_not_called()
