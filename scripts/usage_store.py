"""Compact, bounded seven-day history. Also shipped verbatim to remote hosts."""
import math
from pathlib import Path
import sqlite3
import time
import uuid

RETENTION = 7 * 86400
VERSION = 1


class UsageStore:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), timeout=5)
        self.db.execute('PRAGMA auto_vacuum=INCREMENTAL')
        self.db.execute('PRAGMA journal_size_limit=1048576')
        self.db.execute('PRAGMA max_page_count=16384')  # 64 MiB at the default 4 KiB page size
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS devices(id INTEGER PRIMARY KEY, uuid TEXT UNIQUE, name TEXT, idx INTEGER, seen REAL);
        CREATE TABLE IF NOT EXISTS minutes(
          t INTEGER, gpu INTEGER, covered REAL, used REAL, shared REAL,
          util_sum REAL, util_seconds REAL, mem_peak INTEGER, first REAL, last REAL,
          PRIMARY KEY(t,gpu)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS activity(t INTEGER PRIMARY KEY, covered REAL, active REAL, mem_peak INTEGER, mask INTEGER);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
        ''')
        self.db.execute("INSERT OR IGNORE INTO metadata VALUES('database_id',?)", (uuid.uuid4().hex,))
        self.db.commit()
        self.previous = None
        self.pending = {}
        self.activity = {}
        self.last_flush = time.monotonic()
        self.prune(time.time())

    def prune(self, now):
        # Drop the entire boundary bucket: no stored sample may predate 168 hours.
        cutoff = math.ceil((now - RETENTION) / 60) * 60
        with self.db:
            self.db.execute('DELETE FROM minutes WHERE t < ?', (cutoff,))
            self.db.execute('DELETE FROM activity WHERE t < ?', (cutoff,))
            self.db.execute('DELETE FROM devices WHERE seen < ?', (cutoff,))
            pages = self.db.execute('PRAGMA page_count').fetchone()[0]
            free = self.db.execute('PRAGMA freelist_count').fetchone()[0]
            if pages - free > 14000:
                oldest = self.db.execute('SELECT MIN(t) FROM minutes').fetchone()[0]
                if oldest is not None:
                    self.db.execute('DELETE FROM minutes WHERE t < ?', (oldest + 86400,))
                    self.db.execute('DELETE FROM activity WHERE t < ?', (oldest + 86400,))
        self.db.execute('PRAGMA incremental_vacuum(256)')

    def ingest(self, frame, monotonic=None):
        """Integrate the previous observed state, never across a sampling gap/restart."""
        mono = time.monotonic() if monotonic is None else monotonic
        at = frame['at']
        if self.previous:
            old, old_mono = self.previous
            delta = mono - old_mono
            if 0 < delta <= 2.5 and abs((at - old['at']) - delta) < .5:
                self._interval(old, frame, old['at'], at)
        self.previous = frame, mono
        if mono - self.last_flush >= 15:
            self.flush(at)
            self.last_flush = mono

    def _interval(self, frame, current, start, end):
        current_gpus = {g['uuid']: g for g in current['gpus']}
        gpus = frame['gpus']
        while start < end:
            bucket = int(start // 60) * 60
            stop = min(end, bucket + 60)
            dt = stop - start
            for g in gpus:
                key = bucket, g['uuid']
                row = self.pending.setdefault(key, [g, 0., 0., 0., 0., 0., None, None, None])
                if not g.get('known'):
                    continue
                row[1] += dt
                if g['mine']:
                    row[2] += dt
                    row[3] += dt if g['shared'] else 0
                    latest = current_gpus.get(g['uuid'], {})
                    util = latest.get('util')
                    coverage = latest.get('util_coverage', 0)
                    if util is not None and coverage > 0:
                        row[4] += util * dt * coverage
                        row[5] += dt * coverage
                    if g.get('memory') is not None:
                        row[6] = max(row[6] or 0, g['memory'])
                    row[7] = start if row[7] is None else min(row[7], start)
                    row[8] = stop
            row = self.activity.setdefault(bucket, [0., 0., None, 0])
            known = bool(gpus) and all(g.get('known') for g in gpus)
            if known:
                row[0] += dt
            if any(g.get('known') and g['mine'] for g in gpus):
                row[1] += dt
                lo = max(0, int(start - bucket))
                hi = min(60, math.ceil(stop - bucket))
                row[3] |= ((1 << hi) - 1) ^ ((1 << lo) - 1)
            if known and all(g.get('memory') is not None for g in gpus):
                row[2] = max(row[2] or 0, sum(g['memory'] for g in gpus))
            start = stop

    def flush(self, now=None, status='recording'):
        now = time.time() if now is None else now
        cutoff = math.ceil((now - RETENTION) / 60) * 60
        self.prune(now)
        with self.db:
            for (bucket, uuid), row in self.pending.items():
                if bucket < cutoff:
                    continue
                g, covered, used, shared, util_sum, util_seconds, memory, first, last = row
                self.db.execute('INSERT INTO devices(uuid,name,idx,seen) VALUES(?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET name=excluded.name,idx=excluded.idx,seen=excluded.seen',
                                (uuid, g['name'], g['index'], now))
                gpu = self.db.execute('SELECT id FROM devices WHERE uuid=?', (uuid,)).fetchone()[0]
                self.db.execute('''INSERT INTO minutes VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(t,gpu) DO UPDATE SET
                  covered=MIN(60,covered+excluded.covered),used=MIN(60,used+excluded.used),shared=MIN(60,shared+excluded.shared),
                  util_sum=util_sum+excluded.util_sum,util_seconds=util_seconds+excluded.util_seconds,
                  mem_peak=CASE WHEN mem_peak IS NULL THEN excluded.mem_peak WHEN excluded.mem_peak IS NULL THEN mem_peak ELSE MAX(mem_peak,excluded.mem_peak) END,
                  first=COALESCE(first,excluded.first),last=COALESCE(excluded.last,last)''',
                                (bucket, gpu, covered, used, shared, util_sum, util_seconds, memory, first, last))
            for bucket, row in self.activity.items():
                if bucket >= cutoff:
                    self.db.execute('''INSERT INTO activity VALUES(?,?,?,?,?) ON CONFLICT(t) DO UPDATE SET
                      covered=MIN(60,covered+excluded.covered),active=MIN(60,active+excluded.active),mask=mask | excluded.mask,
                      mem_peak=CASE WHEN mem_peak IS NULL THEN excluded.mem_peak WHEN excluded.mem_peak IS NULL THEN mem_peak ELSE MAX(mem_peak,excluded.mem_peak) END''', (bucket, *row))
            for key, value in [('heartbeat', str(now)), ('status', status), ('version', str(VERSION))]:
                self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', (key, value))
        self.pending.clear()
        self.activity.clear()

    def close(self):
        self.flush(status='stopped')
        self.db.close()


def query(path, start, end, step=60, now=None):
    """Read-only SSH query. Totals are restricted to fully contained minute buckets."""
    now = time.time() if now is None else now
    start = max(float(start), math.ceil((now - RETENTION) / 60) * 60)
    end = min(float(end), now)
    step = max(60, min(3600, int(step) // 60 * 60))
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        raise ValueError('invalid history window')
    if not Path(path).exists():
        return {'status': 'not_installed', 'devices': [], 'points': [], 'activity': [], 'metadata': {}}
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        devices = [dict(zip(('id', 'uuid', 'name', 'index'), r)) for r in db.execute('SELECT id,uuid,name,idx FROM devices WHERE seen>=?', (start,))]
        points = [list(r) for r in db.execute('''SELECT (t / ?) * ?,gpu,SUM(covered),SUM(used),SUM(shared),SUM(util_sum),SUM(util_seconds),MAX(mem_peak),MIN(first),MAX(last)
          FROM minutes WHERE t>=? AND t<? GROUP BY 1,gpu ORDER BY 1,gpu''', (step, step, start, end))]
        activity = [[*r[:4], format(r[4], 'x')] for r in db.execute('SELECT t,covered,active,mem_peak,mask FROM activity WHERE t>=? AND t<? ORDER BY t', (start, end))]
        metadata = dict(db.execute('SELECT key,value FROM metadata'))
        retained_from = db.execute('SELECT MIN(t) FROM minutes').fetchone()[0]
        heartbeat = float(metadata.get('heartbeat', 0))
        status = 'recording' if metadata.get('status') == 'recording' and 0 <= now - heartbeat < 45 else 'stopped'
        return {'status': status, 'devices': devices, 'points': points, 'activity': activity, 'metadata': metadata, 'step': step, 'retained_from': retained_from}
    finally:
        db.close()
