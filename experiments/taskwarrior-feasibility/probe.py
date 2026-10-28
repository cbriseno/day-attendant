#!/usr/bin/env python3
"""Disposable Taskwarrior feasibility lab, not the assistant implementation.

All mutable task data lives in temporary directories. The SQLite sidecar stores
operation intent and focus frames, never authoritative task records. Run with a
Taskwarrior 3.5.0 binary. Workers deliberately die at transaction boundaries.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid


class Lab:
    def __init__(self, root, binary):
        self.root = Path(root)
        self.binary = str(Path(binary).resolve())
        self.root.mkdir(parents=True, exist_ok=True)
        self.rc = self.root / 'taskrc'
        if not self.rc.exists():
            self.rc.write_text('confirmation=off\nnews.version=3.5.0\n', encoding='utf8')
        with self.db() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS operations(
                    op TEXT PRIMARY KEY, kind TEXT, payload TEXT, finished INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS frames(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, op TEXT UNIQUE,
                    target TEXT, previous TEXT);
            ''')

    def db(self):
        return sqlite3.connect(self.root / 'aux.sqlite', timeout=10)

    def task(self, *args, input_text=None, tz='UTC'):
        env = os.environ.copy()
        env.update(TASKRC=str(self.rc), TASKDATA=str(self.root / 'tasks'), TZ=tz)
        run = subprocess.run(
            [self.binary, 'rc.confirmation=off', 'rc.verbose=nothing', *args],
            input=input_text, env=env, capture_output=True, text=True, timeout=20)
        if run.returncode:
            raise RuntimeError(f'task {args}: {run.returncode}: {run.stderr} {run.stdout}')
        return run.stdout

    def rows(self, *filters, tz='UTC'):
        return json.loads(self.task(*filters, 'export', tz=tz))

    def get(self, uid):
        rows = self.rows(f'uuid:{uid}')
        return rows[0] if rows else None

    def actionable(self, uid):
        row = self.get(uid)
        return bool(row and row['status'] == 'pending'
                    and not self.rows(f'uuid:{uid}', '+WAITING')
                    and not self.rows(f'uuid:{uid}', '+BLOCKED'))

    @contextmanager
    def lock(self):
        with (self.root / 'coordinator.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def crash(point, requested):
        if point == requested:
            os._exit(73)

    def intent(self, op, kind, payload):
        with self.db() as con:
            con.execute('INSERT OR IGNORE INTO operations(op,kind,payload) VALUES(?,?,?)',
                        (op, kind, json.dumps(payload)))
            row = con.execute('SELECT kind,payload FROM operations WHERE op=?', (op,)).fetchone()
            if row != (kind, json.dumps(payload)):
                raise ValueError('Operation key reused with different payload')

    def apply(self, op, kind, payload, crash_at=None):
        self.crash('after_intent', crash_at)
        if kind == 'create':
            if not self.get(payload['uuid']):
                # Import preserves a predetermined UUID. Never replay an import
                # over an existing task: it could overwrite subsequent edits.
                self.task('import', '-', input_text=json.dumps([{
                    'uuid': payload['uuid'], 'description': payload['description'],
                    'status': 'pending', 'entry': '20261027T120000Z'}]))
            self.crash('after_task', crash_at)
        elif kind == 'focus':
            target = self.get(payload['target'])
            if not self.actionable(payload['target']):
                raise ValueError('Focus target must remain actionable during recovery')
            previous = payload['previous']
            if previous:
                row = self.get(previous)
                if row and row.get('start'):
                    self.task(previous, 'stop')
            self.crash('after_stop', crash_at)
            if not self.get(payload['target']).get('start'):
                self.task(payload['target'], 'start')
            self.crash('after_start', crash_at)
            with self.db() as con:
                con.execute('INSERT OR IGNORE INTO frames(op,target,previous) VALUES(?,?,?)',
                            (op, payload['target'], previous))
            self.crash('after_frame', crash_at)
        elif kind == 'resume':
            target = self.get(payload['target'])
            if target and target.get('start'):
                self.task(payload['target'], 'stop')
            self.crash('after_stop', crash_at)
            previous = payload['previous']
            row = self.get(previous) if previous else None
            if row and self.actionable(previous) and not row.get('start'):
                self.task(previous, 'start')
            self.crash('after_start', crash_at)
            with self.db() as con:
                con.execute('DELETE FROM frames WHERE seq=?', (payload['seq'],))
            self.crash('after_frame', crash_at)
        with self.db() as con:
            con.execute('UPDATE operations SET finished=1 WHERE op=?', (op,))
        self.crash('after_finish', crash_at)

    def recover(self):
        with self.db() as con:
            pending = con.execute('SELECT op,kind,payload FROM operations WHERE finished=0 ORDER BY rowid').fetchall()
        for op, kind, payload in pending:
            self.apply(op, kind, json.loads(payload))

    def create(self, uid, description, crash_at=None):
        op = f'create:{uid}'
        payload = {'uuid': uid, 'description': description}
        with self.lock():
            self.recover()
            self.intent(op, 'create', payload)
            self.apply(op, 'create', payload, crash_at)

    def focus(self, target, crash_at=None):
        with self.lock():
            self.recover()
            active = [r['uuid'] for r in self.rows('status:pending') if r.get('start')]
            if len(active) > 1:
                raise ValueError('Multiple active tasks are outside this lab contract')
            if active == [target]:
                return
            payload = {'target': target, 'previous': active[0] if active else None}
            op = str(uuid.uuid4())
            self.intent(op, 'focus', payload)
            self.apply(op, 'focus', payload, crash_at)

    def resume(self, crash_at=None):
        with self.lock():
            self.recover()
            with self.db() as con:
                row = con.execute('SELECT seq,target,previous FROM frames ORDER BY seq DESC LIMIT 1').fetchone()
            if not row:
                return
            payload = dict(zip(['seq', 'target', 'previous'], row))
            op = str(uuid.uuid4())
            self.intent(op, 'resume', payload)
            self.apply(op, 'resume', payload, crash_at)


class Feasibility(unittest.TestCase):
    binary = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='asistente-tw-probe-')
        self.lab = Lab(self.temp.name, self.binary)

    def tearDown(self):
        self.temp.cleanup()

    def add(self, title):
        uid = str(uuid.uuid4())
        self.lab.create(uid, '[FICTICIO] ' + title)
        return uid

    def worker(self, action, *values):
        return [sys.executable, '-B', str(Path(__file__).resolve()), '--task-bin', self.binary,
                '--worker', action, '--root', str(self.lab.root), *values]

    def assert_clean(self, lab=None):
        lab = lab or self.lab
        with lab.db() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM operations WHERE finished=0').fetchone()[0], 0)
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_json_identity_survives_edit_completion(self):
        uid = self.add('identity')
        self.lab.task(uid, 'modify', 'priority:H', 'description:[FICTICIO] edited')
        self.assertEqual(self.lab.get(uid)['description'], '[FICTICIO] edited')
        self.lab.task(uid, 'done')
        self.assertEqual(self.lab.get(uid)['uuid'], uid)
        self.assertEqual(self.lab.get(uid)['status'], 'completed')

    def test_wait_and_explicit_dst_instants(self):
        uid = self.add('time')
        self.lab.task(uid, 'modify', 'wait:20300101T120000Z', 'due:20261101T073000Z')
        row = self.lab.get(uid)
        # 3.5 exports stored status 'pending'; +WAITING is derived from wait.
        # Confirmed in upstream src/Task.cpp and doc/man/task.1.in, not merely
        # changed to match output after the first failing run.
        self.assertEqual(row['status'], 'pending')
        self.assertEqual([r['uuid'] for r in self.lab.rows('+WAITING')], [uid])
        self.assertEqual(row['due'], '20261101T073000Z')
        self.assertEqual(self.lab.rows(f'uuid:{uid}', tz='America/Edmonton')[0]['due'], row['due'])
        self.assertEqual(self.lab.rows(f'uuid:{uid}', tz='Asia/Tokyo')[0]['due'], row['due'])
        self.lab.task(uid, 'modify', 'wait:', 'due:20261101T083000Z')
        self.assertEqual(self.lab.get(uid)['status'], 'pending')
        self.assertEqual(self.lab.get(uid)['due'], '20261101T083000Z')
        self.assertEqual(self.lab.rows('+WAITING'), [])

    def test_expired_wait_is_actionable(self):
        uid = self.add('expired wait')
        self.lab.task(uid, 'modify', 'wait:20200101T120000Z')
        self.assertIn('wait', self.lab.get(uid))
        self.assertEqual(self.lab.rows('+WAITING'), [])
        self.lab.focus(uid)
        self.assertTrue(self.lab.get(uid).get('start'))

    def test_concurrent_same_creation_is_idempotent(self):
        uid = str(uuid.uuid4())
        processes = [subprocess.Popen(self.worker('create', uid), stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True) for _ in range(6)]
        for proc in processes:
            out, err = proc.communicate(timeout=30)
            self.assertEqual(proc.returncode, 0, out + err)
        self.assertEqual(len(self.lab.rows()), 1)
        self.assert_clean()

    def test_dependencies(self):
        a, b = self.add('prerequisite'), self.add('dependent')
        self.lab.task(b, 'modify', f'depends:{a}')
        self.assertEqual(self.lab.get(b)['depends'], [a])
        self.assertIn(b, [r['uuid'] for r in self.lab.rows('+BLOCKED')])
        self.lab.task(a, 'done')
        self.assertNotIn(b, [r['uuid'] for r in self.lab.rows('+BLOCKED')])

    def test_repeated_creation_preserves_later_edits(self):
        uid = self.add('retry')
        self.lab.task(uid, 'modify', 'priority:H')
        self.lab.create(uid, '[FICTICIO] retry')
        self.assertEqual(len(self.lab.rows()), 1)
        self.assertEqual(self.lab.get(uid)['priority'], 'H')

    def test_creation_crash_boundaries(self):
        for point in ['after_intent', 'after_task', 'after_finish']:
            with self.subTest(point=point):
                uid = str(uuid.uuid4())
                run = subprocess.run(self.worker('create', uid, point), capture_output=True, text=True, timeout=20)
                self.assertEqual(run.returncode, 73, run.stderr)
                with self.lab.lock():
                    self.lab.recover()
                    self.lab.recover()
                self.assertEqual(len(self.lab.rows(f'uuid:{uid}')), 1)
                self.assert_clean()

    def test_focus_crash_boundaries(self):
        a, b = self.add('base'), self.add('urgent')
        self.lab.focus(a)
        for point in ['after_intent', 'after_stop', 'after_start', 'after_frame', 'after_finish']:
            with self.subTest(point=point):
                run = subprocess.run(self.worker('focus', b, point), capture_output=True, text=True, timeout=20)
                self.assertEqual(run.returncode, 73, run.stderr)
                with self.lab.lock():
                    self.lab.recover()
                    self.lab.recover()
                self.assertTrue(self.lab.get(b).get('start'))
                self.assertFalse(self.lab.get(a).get('start'))
                with self.lab.db() as con:
                    self.assertEqual(con.execute('SELECT count(*) FROM frames').fetchone()[0], 2)
                self.lab.resume()
                self.assertTrue(self.lab.get(a).get('start'))
                self.assert_clean()

    def test_resume_crash_boundaries(self):
        a, b = self.add('base'), self.add('urgent')
        self.lab.focus(a)
        for point in ['after_intent', 'after_stop', 'after_start', 'after_frame', 'after_finish']:
            with self.subTest(point=point):
                self.lab.focus(b)
                run = subprocess.run(self.worker('resume', point), capture_output=True, text=True, timeout=20)
                self.assertEqual(run.returncode, 73, run.stderr)
                with self.lab.lock():
                    self.lab.recover()
                    self.lab.recover()
                self.assertTrue(self.lab.get(a).get('start'))
                self.assertFalse(self.lab.get(b).get('start'))
                with self.lab.db() as con:
                    self.assertEqual(con.execute('SELECT count(*) FROM frames').fetchone()[0], 1)
                self.assert_clean()

    def test_nested_focus_and_completed_paused_task(self):
        a, b, c = self.add('A'), self.add('B'), self.add('C')
        self.lab.focus(a); self.lab.focus(b); self.lab.focus(c)
        self.lab.resume()
        self.assertTrue(self.lab.get(b).get('start'))
        self.lab.task(a, 'done')
        self.lab.resume()
        self.assertFalse(self.lab.get(a).get('start'))
        self.assertEqual(self.lab.get(a)['status'], 'completed')
        self.assert_clean()

    def test_concurrent_creation_and_readers(self):
        ids = [str(uuid.uuid4()) for _ in range(12)]
        processes = [subprocess.Popen(self.worker('create', uid), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for uid in ids]
        snapshots = [self.lab.rows() for _ in range(8)]
        for proc in processes:
            out, err = proc.communicate(timeout=30)
            self.assertEqual(proc.returncode, 0, out + err)
        self.assertEqual({r['uuid'] for r in self.lab.rows()}, set(ids))
        self.assertTrue(all(isinstance(s, list) for s in snapshots))
        self.assert_clean()

    def test_concurrent_distinct_field_updates(self):
        uid = self.add('fields')
        args = [[uid, 'modify', 'priority:H'], [uid, 'modify', '+review']]
        env = os.environ.copy()
        env.update(TASKRC=str(self.lab.rc), TASKDATA=str(self.lab.root / 'tasks'))
        processes = [subprocess.Popen([self.binary, 'rc.confirmation=off', 'rc.verbose=nothing', *a],
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for a in args]
        for proc in processes:
            out, err = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 0, out + err)
        row = self.lab.get(uid)
        self.assertEqual(row['priority'], 'H')
        self.assertIn('review', row['tags'])

    def test_coherent_quiescent_backup_restore(self):
        a, b = self.add('backup A'), self.add('backup B')
        self.lab.focus(a); self.lab.focus(b)
        with tempfile.TemporaryDirectory(prefix='asistente-tw-restore-') as d:
            backup = Path(d) / 'restored'
            with self.lab.lock():
                self.lab.recover()
                shutil.copytree(self.lab.root, backup)
            restored = Lab(backup, self.binary)
            self.assertEqual({r['uuid'] for r in restored.rows()}, {a, b})
            restored.resume()
            self.assertTrue(restored.get(a).get('start'))
            self.assert_clean(restored)


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.cases.append({'case': test.id().split('.')[-1], 'status': 'pass'})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.cases.append({'case': test.id().split('.')[-1], 'status': 'fail'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-bin', required=True)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--worker', choices=['create', 'focus', 'resume'])
    parser.add_argument('--root')
    parser.add_argument('values', nargs='*')
    args = parser.parse_args()
    if args.worker:
        lab = Lab(args.root, args.task_bin)
        if args.worker == 'create':
            uid = args.values[0]
            lab.create(uid, '[FICTICIO] worker', args.values[1] if len(args.values) > 1 else None)
        elif args.worker == 'focus':
            lab.focus(*args.values)
        else:
            lab.resume(*args.values)
        return 0
    Feasibility.binary = str(Path(args.task_bin).resolve())
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(Feasibility))
    if args.report:
        args.report.write_text(json.dumps({
            'at': datetime.now(timezone.utc).isoformat(),
            'taskwarrior': subprocess.check_output([args.task_bin, '--version'], text=True).strip(),
            'duration_seconds': round(time.monotonic() - started, 3),
            'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
            'cases': result.cases,
            'subtest_failures': [str(t) for t, _ in result.failures],
            'errors_detail': [(str(t), trace) for t, trace in result.errors],
            'failure_detail': [(str(t), trace) for t, trace in result.failures],
        }, ensure_ascii=False, indent=2) + '\n')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
