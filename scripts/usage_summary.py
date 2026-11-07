"""Prepare bounded chart data from remote minute records, without averaging averages."""
import math
import time


def summarize(hosts, start, end, now=None):
    now = time.time() if now is None else now
    start = max(start, math.ceil((now - 7 * 86400) / 60) * 60)
    end = min(end, now)
    step = 300 if end - start <= 86400 else 900
    result = {'start': start, 'end': end, 'step': step, 'hosts': [], 'devices': [], 'series': [], 'sessions': []}
    masks, series = {}, {}
    gpu_seconds = coverage = expected = 0
    memory_peak = None
    for host, data in hosts:
        result['hosts'].append({'name': host, 'status': data['status'], 'fetched_at': data.get('fetched_at'),
                                'error': data.get('error'), 'configured_users': data.get('configured_users', []), 'users': data.get('users', []), 'metadata': data.get('metadata', {})})
        devices = {d['id']: dict(d, host=host, seconds=0, shared=0, util_sum=0, util_seconds=0, memory=None) for d in data.get('devices', [])}
        expected += max(0, end - start) * len(devices)
        last_sessions = {}
        for row in data.get('points', []):
            at, gpu, covered, used, shared, util_sum, util_seconds, memory, first, last = row
            if not start <= at < end or gpu not in devices:
                continue
            # Minute-resolution query boundaries are aligned in the browser.
            d = devices[gpu]
            d['seconds'] += used
            d['shared'] += shared
            d['util_sum'] += util_sum
            d['util_seconds'] += util_seconds
            coverage += covered
            gpu_seconds += used
            if memory is not None:
                d['memory'] = max(d['memory'] or 0, memory)
                memory_peak = max(memory_peak or 0, memory)
            bucket = at // step * step
            point = series.setdefault(bucket, [bucket, 0, 0, 0, 0])
            point[1] += used
            point[2] += covered
            point[3] += util_sum
            point[4] += util_seconds
            if used and first is not None and last is not None:
                previous = last_sessions.get(gpu)
                # Never merge across an unknown minute; show the observation bounds only.
                if previous and bool(shared) == bool(previous['shared']) and at == previous['_bucket'] + 60 and first - previous['end'] <= 2.5:
                    previous.update(end=last, seconds=previous['seconds'] + used, shared=previous['shared'] + shared,
                                    _bucket=at, util_sum=previous['util_sum'] + util_sum,
                                    util_seconds=previous['util_seconds'] + util_seconds)
                    if memory is not None:
                        previous['memory'] = max(previous['memory'] or 0, memory)
                else:
                    previous = {'host': host, 'uuid': d['uuid'], 'index': d['index'], 'name': d['name'],
                                'start': first, 'end': last, 'seconds': used, 'shared': shared,
                                'memory': memory, 'util_sum': util_sum, 'util_seconds': util_seconds, '_bucket': at}
                    result['sessions'].append(previous)
                    last_sessions[gpu] = previous
        result['devices'].extend(d for d in devices.values())
        for row in data.get('activity', []):
            if start <= row[0] < end:
                masks[row[0]] = masks.get(row[0], 0) | int(row[4], 16)
    result['series'] = sorted(series.values())
    result['active'] = [[at, bin(mask).count('1')] for at, mask in sorted(masks.items())]
    result['summary'] = {'gpu_hours': gpu_seconds / 3600, 'active_hours': sum(r[1] for r in result['active']) / 3600,
                         'gpu_count': sum(d['seconds'] > 0 for d in result['devices']), 'memory_peak': memory_peak,
                         'coverage': min(1, coverage / expected) if expected else None}
    for session in result['sessions']:
        session.pop('_bucket')
    result['sessions'].sort(key=lambda s: s['start'], reverse=True)
    return result
