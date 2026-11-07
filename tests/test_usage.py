import copy
import json
from pathlib import Path
import sqlite3
import shlex
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.usage_store import UsageStore, query, RETENTION
from scripts.usage_summary import summarize
from scripts.usage_client import UsageClient, remote


def frame(at, count=1, mine=True, known=True):
    return {'at': at, 'gpus': [{'uuid': str(i), 'index': i, 'name': 'Test GPU', 'known': known,
            'mine': mine, 'shared': i == 1, 'memory': 1024 if mine else 0, 'util': .5, 'util_coverage': 1} for i in range(count)]}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'usage.sqlite3'
        self.store = UsageStore(self.path)
        self.at = int(time.time() // 60) * 60 - 600

    def tearDown(self):
        self.store.db.close()
        self.directory.cleanup()

    def test_four_gpus_two_hours_and_active_union(self):
        for second in range(7201):
            self.store.ingest(frame(self.at - 7200 + second, 4), monotonic=second)
        self.store.flush(self.at)
        data = query(self.path, self.at - 7200, self.at, now=self.at)
        result = summarize([('a', data), ('b', copy.deepcopy(data))], self.at - 7200, self.at, now=self.at)
        self.assertAlmostEqual(result['summary']['gpu_hours'], 16)
        self.assertAlmostEqual(result['summary']['active_hours'], 2)
        self.assertEqual(result['summary']['gpu_count'], 8)
        self.assertEqual(result['summary']['memory_peak'], 1024)
        self.assertTrue(any(d['shared'] for d in result['devices']))
        self.assertTrue(all(abs(d['util_sum']/d['util_seconds']-.5)<.001 for d in result['devices']))

    def test_gap_restart_and_unknown_never_fabricate_usage(self):
        for at, mono, known in [(0,0,True),(1,1,True),(120,120,True),(121,121,False),(122,122,True)]:
            self.store.ingest(frame(self.at+at,known=known),monotonic=mono)
        self.store.flush(self.at+122)
        first=query(self.path,self.at,self.at+180,now=self.at+180)
        self.assertEqual(sum(r[3] for r in first['points']),2)
        self.store.db.close()
        self.store=UsageStore(self.path)
        self.store.ingest(frame(self.at+180),monotonic=200)
        self.store.flush(self.at+180)
        second=query(self.path,self.at,self.at+181,now=self.at+181)
        self.assertEqual(sum(r[3] for r in second['points']),2)

    def test_split_midnight_and_backward_clock(self):
        start=self.at+59
        self.store.ingest(frame(start),monotonic=0)
        self.store.ingest(frame(start+2),monotonic=2)
        self.store.ingest(frame(start-100),monotonic=3)
        self.store.flush(start+2)
        rows=query(self.path,self.at,start+3,now=start+3)['points']
        self.assertEqual([r[3] for r in rows],[1,1])

    def test_strict_retention_and_read_only_queries(self):
        self.store.ingest(frame(self.at),monotonic=0)
        self.store.ingest(frame(self.at+1),monotonic=1)
        self.store.flush(self.at+1)
        self.assertTrue(query(self.path,self.at,self.at+2,now=self.at+2)['points'])
        self.store.prune(self.at+RETENTION+1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM minutes').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM activity').fetchone()[0],0)
        self.assertEqual(query(self.path,self.at,self.at+RETENTION+3,now=self.at+RETENTION+2)['points'],[])

    def test_missing_load_is_not_zero_and_no_owner_is_not_idle(self):
        first=frame(self.at)
        second=frame(self.at+1)
        second['gpus'][0].update(util=None,util_coverage=0)
        self.store.ingest(first,monotonic=0)
        self.store.ingest(second,monotonic=1)
        self.store.flush(self.at+1)
        data=query(self.path,self.at,self.at+2,now=self.at+2)
        self.assertEqual(data['points'][0][6],0)
        self.assertIsNone(summarize([('a', {'status':'unreachable'})], self.at, self.at+1,now=self.at+1)['summary']['coverage'])

    def test_compact_week_storage(self):
        # Representative full-use week, including all indexes and coverage rows.
        end=self.at
        with self.store.db:
            self.store.db.execute("INSERT INTO devices VALUES(1,'a','GPU',0,?)",(end,))
            self.store.db.executemany('INSERT INTO minutes VALUES(?,?,?,?,?,?,?,?,?,?)',
                [(end-RETENTION+60*i,1,60,60,0,30,60,24*1024**3,end-RETENTION+60*i,end-RETENTION+60*(i+1)) for i in range(10080)])
            self.store.db.executemany('INSERT INTO activity VALUES(?,?,?,?,?)',
                [(end-RETENTION+60*i,60,60,24*1024**3,2**60-1) for i in range(10080)])
        self.assertLess(self.path.stat().st_size,2*1024**2)
        print('Measured one GPU / seven days: %d bytes' % self.path.stat().st_size)


class TransportTests(unittest.TestCase):
    def test_ssh_arguments_and_stdin_payload(self):
        completed=type('Result',(),{'stdout':'{"status":"recording"}','stderr':'','returncode':0})()
        with patch('scripts.usage_client.subprocess.run',return_value=completed) as run:
            remote('gpu-01','query','/tmp/control',start=1,end=2)
        args,kwargs=run.call_args
        self.assertEqual(args[0][0],'ssh')
        self.assertIn('gpu-01',args[0])
        self.assertEqual(json.loads(kwargs['input'])['action'],'query')
        self.assertEqual(shlex.split(args[0][-1])[:2], ['python3', '-c'])
        self.assertEqual(len(shlex.split(args[0][-1])), 3)

    def test_connection_failure_preserves_but_labels_cached_history(self):
        stop=threading.Event()
        client=UsageClient('test','/tmp/control',stop)
        at=int(time.time())
        client.state.update(points=[[at,1]], fetched_at=at)
        def fail(*args,**kwargs):
            stop.set()
            raise RuntimeError('offline')
        with patch('scripts.usage_client.remote',side_effect=fail):
            client.run()
        data=client.snapshot()
        self.assertEqual(data['status'],'unreachable')
        self.assertEqual(data['points'],[[at,1]])



class DeploymentTests(unittest.TestCase):
    def test_install_rejects_missing_manager_before_writing_payload(self):
        from scripts import agent_remote
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(agent_remote, 'ROOT', Path(directory)), patch.object(agent_remote, 'capabilities', return_value={'systemd': False, 'linger': False, 'user': 'demo'}):
                with self.assertRaisesRegex(RuntimeError, 'systemd'):
                    agent_remote.install({'users': ['demo']})
                self.assertFalse((Path(directory) / 'config.json').exists())

    def test_probe_failure_restores_old_config_and_release(self):
        from scripts import agent_remote
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data'; root.mkdir()
            home=Path(directory)/'home'; home.mkdir()
            old=root/'old'; old.mkdir()
            (root/'current').symlink_to(old)
            (root/'config.json').write_text('{"own_users":["demo"]}')
            unit=home/'.config/systemd/user'/agent_remote.UNIT
            unit.parent.mkdir(parents=True)
            unit.write_text('old unit')
            calls=[]
            def run(argv,required=True):
                calls.append(argv)
                if '--probe' in argv:
                    raise RuntimeError('NVML missing')
            with patch.object(agent_remote,'ROOT',root), patch.object(agent_remote.Path,'home',return_value=home), patch.object(agent_remote,'capabilities',return_value={'systemd':True,'linger':True,'user':'demo'}), patch.object(agent_remote,'run',side_effect=run):
                with self.assertRaisesRegex(RuntimeError,'NVML missing'):
                    agent_remote.install({'users':['demo'],'sources':{n:'# test' for n in ('usage_agent.py','usage_store.py','nvml_stream.py')}})
            self.assertEqual((root/'current').resolve(),old.resolve())
            self.assertEqual(unit.read_text(),'old unit')
            self.assertEqual(json.loads((root/'config.json').read_text())['own_users'],['demo'])

    def test_agent_process_abi_and_owner_failure(self):
        from scripts.usage_agent import processes, Process, owner
        import ctypes as c
        class Library:
            def nvmlDeviceGetComputeRunningProcesses_v3(self,handle,count,buffer):
                c.cast(count,c.POINTER(c.c_uint))[0]=1
                if buffer is None:
                    return 7
                buffer[0].pid=123
                buffer[0].memory=4096
                return 0
        result=processes(Library(),None)
        self.assertEqual((result[0].pid,result[0].memory),(123,4096))
        self.assertEqual(c.sizeof(Process),24)
        with patch('scripts.usage_agent.Path.stat',side_effect=PermissionError):
            self.assertIsNone(owner(123))

class ApiTests(unittest.TestCase):
    def test_history_api_and_static_resource_whitelist(self):
        from functools import partial
        from http.server import ThreadingHTTPServer
        from urllib.request import urlopen
        from urllib.error import HTTPError
        from server import Handler
        now=int(time.time()//60)*60
        class Cache:
            def snapshot(self):
                return {'status':'recording','devices':[{'id':1,'uuid':'uuid','index':0,'name':'Test GPU'}],
                        'points':[[now-60,1,60,60,0,30,60,1024,now-60,now]],
                        'activity':[[now-60,60,60,1024,format(2**60-1,'x')]], 'metadata':{}}
        class Monitor:
            config={'name':'test'}
            usage=Cache()
        settings={'port':0}
        httpd=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,monitors=[Monitor()],settings=settings,instance='test'))
        settings['port']=httpd.server_port
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:%d'%httpd.server_port
        try:
            with urlopen(url+'/api/usage?start=%d&end=%d'%(now-120,now)) as response:
                data=json.load(response)
            self.assertAlmostEqual(data['summary']['gpu_hours'],1/60)
            self.assertEqual(data['summary']['gpu_count'],1)
            with self.assertRaises(HTTPError) as error:
                urlopen(url+'/api/usage?start=NaN')
            self.assertEqual(error.exception.code,400)
            for resource in ('usage.js','usage.css'):
                with urlopen(url+'/'+resource) as response:
                    self.assertEqual(response.status,200)
            with self.assertRaises(HTTPError):
                urlopen(url+'/../config.json')
        finally:
            httpd.shutdown();httpd.server_close();thread.join()


if __name__ == "__main__":
    unittest.main()
