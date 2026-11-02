#!/usr/bin/env python3
"""Run ~0.4s of CUDA work on one verified empty GPU, then check buffered history.

Requires explicit --host/--gpu selection. No third-party Python packages needed.
The CUDA context is always destroyed; existing GPU processes are never touched.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
REMOTE = r'''
import ctypes as c,json,subprocess,time,uuid
target=TARGET_UUID
row=subprocess.check_output(['nvidia-smi','--id=GPU-'+target,'--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True)
assert float(row.strip()) < 100, 'GPU is occupied; test refused'
apps=subprocess.check_output(['nvidia-smi','--query-compute-apps=gpu_uuid,pid','--format=csv,noheader'],text=True)
assert 'GPU-'+target not in apps, 'GPU has a compute process; test refused'
d=c.CDLL('libcuda.so.1')
def check(rc):
 if rc: raise RuntimeError('CUDA error %d' % rc)
check(d.cuInit(0)); count=c.c_int(); check(d.cuDeviceGetCount(c.byref(count)))
class UUID(c.Structure): _fields_=[('bytes',c.c_ubyte*16)]
device=None
for i in range(count.value):
 dev=c.c_int(); check(d.cuDeviceGet(c.byref(dev),i)); ident=UUID()
 check(d.cuDeviceGetUuid(c.byref(ident),dev))
 if str(uuid.UUID(bytes=bytes(ident.bytes))) == target: device=dev; break
assert device is not None, 'CUDA/NVML UUID mismatch'
ptx=b""".version 6.4
.target sm_52
.address_size 64
.visible .entry pulse(.param .u64 duration) {
 .reg .u64 begin,now,delta,limit;
 .reg .pred done;
 ld.param.u64 limit,[duration];
 mov.u64 begin,%clock64;
loop:
 mov.u64 now,%clock64;
 sub.u64 delta,now,begin;
 setp.ge.u64 done,delta,limit;
 @!done bra loop;
 ret;
}
"""
context=c.c_void_p(); module=c.c_void_p(); function=c.c_void_p()
check(d.cuCtxCreate_v2(c.byref(context),0,device))
try:
 check(d.cuModuleLoadData(c.byref(module),c.c_char_p(ptx)))
 check(d.cuModuleGetFunction(c.byref(function),module,b'pulse'))
 duration=c.c_ulonglong(2_000_000)
 params=(c.c_void_p*1)(c.cast(c.byref(duration),c.c_void_p))
 started=time.monotonic(); launches=0
 while time.monotonic()-started < .4:
  check(d.cuLaunchKernel(function,1,1,1,32,1,1,0,None,params,None))
  check(d.cuCtxSynchronize()); launches+=1
 print(json.dumps({'busy_seconds':time.monotonic()-started,'launches':launches}))
finally:
 if module.value: d.cuModuleUnload(module)
 d.cuCtxDestroy_v2(context)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True, help='configured server display name')
    parser.add_argument('--gpu', type=int, required=True)
    args = parser.parse_args()
    config = json.loads((ROOT / 'config.json').read_text())
    url = 'http://127.0.0.1:%d/api/status' % config['port']
    def gpu():
        with urllib.request.urlopen(url, timeout=5) as response:
            status = json.load(response)
        host = next(server for server in status['servers'] if server['name'] == args.host)
        return host, next(gpu for gpu in host['gpus'] if gpu['index'] == args.gpu)
    host, before = gpu()
    assert host['status'] == 'online' and before['processes_available']
    assert before['process_count'] == 0 and before['memory_used_bytes'] < 100 * 1024**2
    assert before['utilization_source'] == 'nvml_samples'
    source = REMOTE.replace('TARGET_UUID', repr(before['uuid']))
    result = subprocess.run(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6',
                             host['ssh_host'], shlex.join(['python3', '-c', source])],
                            capture_output=True, text=True, timeout=25, check=True)
    pulse = json.loads(result.stdout)
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        _, after = gpu()
        window = after.get('utilization_window')
        if window and window['peak'] > 0 and after['utilization_gpu_exporter'] == 0:
            report = {'result': 'PASS', 'pulse': pulse, 'point_utilization': 0,
                      'window': window, 'memory_after_bytes': after['memory_used_bytes'],
                      'process_count_after': after['process_count']}
            (ROOT / '.runtime/nvml-burst.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))
            return
        time.sleep(.5)
    raise AssertionError('short pulse was not visible in the history window')


if __name__ == '__main__':
    main()
