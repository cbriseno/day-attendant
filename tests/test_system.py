"""System scenarios: separate public CLI processes, real Taskwarrior/notify-send.

Run only via scripts/test_system.py: never use the desktop session bus.
Expected timelines are authored here, not derived from product candidate logic.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
TASK = os.environ.get('ASISTENTE_TASK_BIN', str(ROOT / '.runtime/taskwarrior/bin/task'))
FIX = ROOT / 'tests/fixtures'
REPORT = Path(os.environ.get('E2E_REPORT_DIR', '/tmp/asistente-e2e-report'))


@unittest.skipUnless(os.environ.get('ASISTENTE_ISOLATED_BUS') == '1', 'Use scripts/test_system.py to isolate D-Bus')
class System(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='asistente-system-')
        self.folder = Path(self.temp.name)
        self.state = self.folder / 'state'
        self.clock = self.folder / 'clock'
        self.time = datetime(2026, 10, 30, 15, tzinfo=timezone.utc)
        self.advance()
        self.env = dict(os.environ, E2E_CLOCK=str(self.clock))
        self.trace = []
        self.children = []
        self.control(mode='accept')
        self.server = None
        self.start_server()

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        self.stop_server()
        target = REPORT / self._testMethodName
        target.mkdir(parents=True, exist_ok=True)
        (target / 'commands.json').write_text(json.dumps(self.trace, indent=2))
        (target / 'dbus.jsonl').write_text((self.folder / 'dbus.jsonl').read_text() if (self.folder / 'dbus.jsonl').exists() else '')
        if (self.state / 'assistant.sqlite3').exists():
            with sqlite3.connect(self.state / 'assistant.sqlite3') as con:
                con.row_factory = sqlite3.Row
                snapshot = {table: [dict(r) for r in con.execute('SELECT * FROM ' + table)] for table in ('operations', 'notices', 'attempts', 'frames', 'state')}
            (target / 'journal.json').write_text(json.dumps(snapshot, indent=2))
            (target / 'taskwarrior.json').write_text(json.dumps(self.export(), indent=2))
        self.temp.cleanup()

    def control(self, **kwargs):
        (self.folder / 'control.json').write_text(json.dumps(kwargs))

    def start_server(self):
        ready = self.folder / 'ready'
        ready.unlink(missing_ok=True)
        self.server = subprocess.Popen([sys.executable, '-B', str(FIX / 'notifications.py'), str(self.folder)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.wait(lambda: ready.exists())

    def stop_server(self):
        if self.server and self.server.poll() is None:
            self.server.terminate()
            _, err = self.server.communicate(timeout=5)
            self.assertEqual(err, '')
        self.server = None

    def wait(self, condition):
        deadline = time.monotonic() + 12
        while not condition():
            if time.monotonic() > deadline:
                self.fail('Timed out waiting for harness condition')
            time.sleep(.025)

    def advance(self, **kwargs):
        self.time += timedelta(**kwargs)
        pending = self.clock.with_suffix('.new')
        pending.write_text(self.time.isoformat())
        pending.replace(self.clock)

    def command(self, args, extra=None, direct=False):
        argv = [sys.executable, '-B', str(ROOT / 'asistente.py' if direct else FIX / 'cli_worker.py'), '--state', str(self.state), '--task-bin', TASK, '--json', *args]
        return argv, dict(self.env, **(extra or {}))

    def runcli(self, *args, code=0, extra=None, direct=False):
        argv, env = self.command(list(args), extra, direct)
        run = subprocess.run(argv, env=env, cwd=ROOT, capture_output=True, text=True, timeout=25)
        self.trace.append({'at': self.time.isoformat(), 'argv': list(args), 'fault': extra, 'exit': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr, 'direct': direct})
        self.assertEqual(run.returncode, code, run.stdout + run.stderr)
        if code == 75:
            return None
        result = json.loads(run.stdout)
        self.assertEqual(result['ok'], code == 0)
        return result['data'] if code == 0 else result['error']

    def spawn(self, *args, extra=None):
        argv, env = self.command(list(args), extra)
        child = subprocess.Popen(argv, env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        self.trace.append({'at': self.time.isoformat(), 'spawn': list(args), 'fault': extra})
        return child

    def finish(self, child):
        out, err = child.communicate(timeout=25)
        self.trace.append({'child_exit': child.returncode, 'stdout': out, 'stderr': err})
        self.assertEqual(child.returncode, 0, out + err)
        return json.loads(out)['data']

    def add(self, title, key, *options):
        return self.runcli('task', 'add', '[FICTICIO] ' + title, '--key', key, *options)['uuid']

    def sql(self, query):
        with sqlite3.connect(self.state / 'assistant.sqlite3') as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(query)]

    def export(self):
        env = dict(os.environ, TASKRC=str(self.state / 'taskwarrior/taskrc'), TASKDATA=str(self.state / 'taskwarrior/data'))
        result = subprocess.run([TASK, 'rc.verbose=nothing', 'export'], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def messages(self, stage='accepted'):
        path = self.folder / 'dbus.jsonl'
        received, result = {}, []
        for row in map(json.loads, path.read_text().splitlines() if path.exists() else []):
            if row['stage'] == 'received':
                received[row['id']] = row
            if row['stage'] == stage:
                result.append(dict(received.get(row['id'], {}), **row))
        return result

    def test_seven_days_absence_restart_and_backward_clock(self):
        # Friday: only the imminent event may notify; the task is snoozed to Monday.
        uid = self.add('lunes', 'task', '--due', '2026-11-02T15:00Z')
        self.runcli('task', 'snooze', uid, '2026-11-02T15:00Z', '--key', 'snooze')
        self.runcli('event', 'add', '[FICTICIO] viernes', '--start', '2026-10-30T15:10Z', '--end', '2026-10-30T16:00Z', '--key', 'event')
        self.runcli('notify')
        self.assertEqual([r['body'] for r in self.messages()], ['[FICTICIO] viernes'])
        # Entire weekend without pulses. Monday: expired event omitted, one task
        # message despite due+snooze+stale. No backlog of missed invocations.
        self.advance(days=3)
        before = hashlib.sha256((self.state / 'assistant.sqlite3').read_bytes()).hexdigest()
        for _ in range(3):
            self.runcli('pulse')
        self.assertEqual(hashlib.sha256((self.state / 'assistant.sqlite3').read_bytes()).hexdigest(), before)
        self.runcli('notify')
        self.runcli('notify')
        self.assertEqual([r['body'] for r in self.messages()], ['[FICTICIO] viernes', '[FICTICIO] lunes'])
        self.advance(minutes=-60)
        self.runcli('notify')
        self.assertEqual(len(self.messages()), 2)
        self.advance(minutes=91)
        self.runcli('notify')
        self.assertEqual(len(self.messages()), 3)
        self.runcli('task', 'done', uid, '--key', 'done')
        # Four more days, restarting CLI and receiver: no obsolete delivery.
        for day in range(4):
            self.advance(days=1)
            self.stop_server(); self.start_server()
            self.runcli('notify')
            self.assertEqual(len(self.messages()), 3)
        self.assertEqual(next(r for r in self.export() if r['uuid'] == uid)['status'], 'completed')
        self.assertEqual(self.sql('PRAGMA integrity_check')[0]['integrity_check'], 'ok')

    def test_transport_budget_coalescing_and_concurrent_notifiers(self):
        for i in range(8):
            self.add('tarea ' + str(i), str(i), '--due', '2026-10-30T15:00Z', '--source', 'chat', '--observed-at', '2026-10-29T15:00Z')
        self.advance(days=3)
        children = [self.spawn('notify') for _ in range(3)]
        for child in children:
            self.finish(child)
        self.assertEqual(len(self.messages()), 3)
        self.assertEqual(len({r['body'] for r in self.messages()}), 3)
        self.runcli('notify')
        self.assertEqual(len(self.messages()), 3)
        self.advance(minutes=5)
        self.runcli('notify')
        self.assertEqual(len(self.messages()), 6)
        self.assertEqual(len({r['body'] for r in self.messages()}), 6)
        for row in self.messages('received'):
            self.assertEqual((row['app'], row['title']), ('asistente-dia', 'Asistente del día'))

    def test_late_claim_cannot_overwrite_uncertain_or_new_owner(self):
        self.add('claim', 'task', '--due', '2026-10-30T15:00Z')
        barrier = self.folder / 'barrier'
        a = self.spawn('notify', extra={'E2E_PAUSE': 'after_notify', 'E2E_BARRIER': str(barrier)})
        self.wait(lambda: barrier.exists())
        self.assertEqual(len(self.messages()), 1)
        self.advance(minutes=3)
        self.runcli('notify')
        self.assertEqual(self.sql('SELECT status,accepted_at FROM notices'), [{'status': 'uncertain', 'accepted_at': None}])
        barrier.with_suffix('.release').touch()
        self.assertEqual(self.finish(a)['attempts'][0]['status'], 'ownership_lost')
        self.assertEqual(self.sql('SELECT status,accepted_at FROM notices'), [{'status': 'uncertain', 'accepted_at': None}])
        attempt = self.sql('SELECT status,late_outcome FROM attempts')[0]
        self.assertEqual(attempt['status'], 'uncertain')
        self.assertTrue(json.loads(attempt['late_outcome'])['accepted'])
        # A new explicit owner can claim; an even later obsolete owner cannot
        # alter its attempt or acceptance timestamp (covered by same CAS).
        self.runcli('notify', '--retry-uncertain')
        self.assertEqual(len(self.messages()), 2)

    def test_claim_expired_before_transport_is_not_sent(self):
        self.add('expired', 'task', '--due', '2026-10-30T15:00Z')
        barrier = self.folder / 'barrier'
        a = self.spawn('notify', extra={'E2E_PAUSE': 'after_claim', 'E2E_BARRIER': str(barrier)})
        self.wait(lambda: barrier.exists())
        self.advance(minutes=3)
        self.runcli('notify')
        barrier.with_suffix('.release').touch()
        self.assertEqual(self.finish(a)['attempts'][0]['status'], 'ownership_lost')
        self.assertEqual(self.messages('received'), [])

    def test_late_completion_cannot_replace_new_owner(self):
        self.add('two owners', 'task', '--due', '2026-10-30T15:00Z')
        first, second = self.folder / 'first', self.folder / 'second'
        a = self.spawn('notify', extra={'E2E_PAUSE': 'after_notify', 'E2E_BARRIER': str(first)})
        self.wait(lambda: first.exists())
        self.advance(minutes=3)
        self.runcli('notify')
        b = self.spawn('notify', '--retry-uncertain', extra={'E2E_PAUSE': 'after_claim', 'E2E_BARRIER': str(second)})
        self.wait(lambda: second.exists())
        owner = self.sql('SELECT active_attempt_id FROM notices')[0]['active_attempt_id']
        first.with_suffix('.release').touch()
        self.assertEqual(self.finish(a)['attempts'][0]['status'], 'ownership_lost')
        self.assertEqual(self.sql('SELECT status,active_attempt_id,accepted_at FROM notices'), [{'status': 'sending', 'active_attempt_id': owner, 'accepted_at': None}])
        second.with_suffix('.release').touch()
        self.assertEqual(self.finish(b)['attempts'][0]['status'], 'accepted')
        self.assertEqual([r['status'] for r in self.sql('SELECT status FROM attempts ORDER BY id')], ['uncertain', 'accepted'])
        self.assertEqual(len(self.messages()), 2)

    def test_external_conflict_preserves_data_and_blocks_writes(self):
        self.runcli('task', 'add', '[FICTICIO] original', '--key', 'conflict', code=75, extra={'E2E_CRASH': 'after_task_0'})
        uid = self.export()[0]['uuid']
        env = dict(os.environ, TASKRC=str(self.state / 'taskwarrior/taskrc'), TASKDATA=str(self.state / 'taskwarrior/data'))
        run = subprocess.run([TASK, 'rc.confirmation=off', uid, 'modify', 'description:[FICTICIO] external'], env=env, capture_output=True, timeout=15)
        self.assertEqual(run.returncode, 0)
        before = self.export()
        self.assertEqual(self.runcli('recover')['unresolved'][0]['status'], 'conflict')
        self.runcli('task', 'add', '[FICTICIO] blocked', '--key', 'blocked', code=2)
        self.assertEqual(self.export(), before)

    def test_schema_one_upgrade_preserves_idempotency_and_suppression(self):
        uid = self.add('migration', 'old-key', '--due', '2026-10-30T15:00Z')
        self.runcli('notify')
        # Recreate the exact v1 columns from a populated v2 state. The original
        # operations/results and accepted timestamp must survive the upgrade.
        with sqlite3.connect(self.state / 'assistant.sqlite3') as con:
            con.execute('ALTER TABLE notices DROP COLUMN active_attempt_id')
            con.execute('ALTER TABLE attempts DROP COLUMN late_outcome')
            con.execute("DELETE FROM config WHERE key='notification_budget'")
            con.execute('PRAGMA user_version=1')
        self.assertEqual(self.add('migration', 'old-key', '--due', '2026-10-30T15:00Z'), uid)
        self.runcli('notify')
        self.assertEqual(len(self.messages()), 1)
        self.assertEqual(len(self.export()), 1)
        self.assertEqual(self.sql('PRAGMA user_version'), [{'user_version': 2}])

    def test_schema_one_sending_claim_becomes_uncertain_without_resend(self):
        self.add('inherited claim', 'task', '--due', '2026-10-30T15:00Z')
        self.runcli('notify', code=75, extra={'E2E_CRASH': 'after_claim'})
        with sqlite3.connect(self.state / 'assistant.sqlite3') as con:
            con.execute('ALTER TABLE notices DROP COLUMN active_attempt_id')
            con.execute('ALTER TABLE attempts DROP COLUMN late_outcome')
            con.execute("DELETE FROM config WHERE key='notification_budget'")
            con.execute('PRAGMA user_version=1')
        # The v1 process is dead, not concurrently running old code.
        notice = self.runcli('doctor')['notices_requiring_attention'][0]
        self.assertEqual(notice['status'], 'sending')
        self.assertFalse(notice['claim_expired'])
        self.advance(minutes=3)
        self.assertEqual(self.runcli('doctor')['notices_requiring_attention'][0]['delivery_state'], 'expired_unknown')
        self.assertEqual(self.sql('SELECT status FROM notices'), [{'status': 'sending'}])
        self.assertEqual(self.runcli('notify')['attempts'], [])
        self.assertEqual(self.sql('SELECT status,active_attempt_id,accepted_at FROM notices'), [{'status': 'uncertain', 'active_attempt_id': None, 'accepted_at': None}])
        self.advance(days=1)
        self.assertEqual(self.runcli('notify')['attempts'], [])
        self.assertEqual(self.messages('received'), [])
        self.assertEqual(self.sql('SELECT status FROM attempts'), [{'status': 'uncertain'}])

    def test_new_due_reason_intentionally_obeys_entity_suppression(self):
        uid = self.add('severity', 'task')
        self.advance(days=3)
        self.runcli('notify')
        self.assertEqual(self.sql('SELECT kind,status FROM notices'), [{'kind': 'stale', 'status': 'accepted'}])
        self.advance(minutes=5)
        self.runcli('task', 'update', uid, '--due', self.time.isoformat(), '--key', 'new-due')
        self.assertEqual(self.runcli('notify')['attempts'], [])
        self.assertEqual(len(self.messages()), 1)
        self.assertEqual(self.sql("SELECT kind,status,accepted_at FROM notices WHERE kind='due'"), [{'kind': 'due', 'status': 'pending', 'accepted_at': None}])
        self.advance(minutes=25)
        self.assertEqual(self.runcli('notify')['attempts'][0]['status'], 'accepted')
        self.assertEqual(len(self.messages()), 2)
        self.assertEqual(self.sql("SELECT status FROM notices WHERE kind='due'"), [{'status': 'accepted'}])

    def test_transport_rejection_timeout_and_server_absence(self):
        self.add('transport', 'task', '--due', '2026-10-30T15:00Z')
        self.control(mode='fail')
        self.assertEqual(self.runcli('notify')['attempts'][0]['status'], 'pending')
        self.assertEqual(len(self.messages('rejected')), 1)
        self.assertIsNone(self.sql('SELECT accepted_at FROM notices')[0]['accepted_at'])
        self.advance(minutes=3)
        self.stop_server()
        self.assertEqual(self.runcli('notify')['attempts'][0]['status'], 'pending')
        self.start_server()
        self.control(mode='hang')
        self.advance(minutes=5)
        self.assertEqual(self.runcli('notify')['attempts'][0]['status'], 'uncertain')
        self.advance(days=2)
        self.assertEqual(self.runcli('notify')['attempts'], [])
        self.assertEqual(self.messages(), [])

    def test_missing_notification_client_is_visible_failure(self):
        self.add('missing client', 'task', '--due', '2026-10-30T15:00Z')
        self.assertEqual(self.runcli('notify', extra={'PATH': '/nonexistent'})['attempts'][0]['status'], 'pending')
        notice = self.sql('SELECT accepted_at,error FROM notices')[0]
        self.assertIsNone(notice['accepted_at'])
        self.assertIn('No supported local notifier', notice['error'])
        self.assertEqual(self.messages('received'), [])

    def test_crashes_recover_and_concurrent_capture(self):
        for i, point in enumerate(('after_intent', 'after_task_0', 'inside_commit', 'after_commit')):
            args = ('task', 'add', '[FICTICIO] crash ' + str(i), '--key', 'crash-' + str(i))
            self.runcli(*args, code=75, extra={'E2E_CRASH': point})
            self.runcli('recover')
            self.runcli(*args)
        children = [self.spawn('task', 'add', '[FICTICIO] concurrent ' + str(i % 3), '--key', 'agent-' + str(i % 3)) for i in range(9)]
        for child in children:
            self.finish(child)
        rows = self.export()
        self.assertEqual(len(rows), 7)
        self.assertEqual(len({r['uuid'] for r in rows}), 7)
        self.assertEqual(self.sql("SELECT count(*) AS n FROM operations WHERE status!='confirmed'"), [{'n': 0}])

    def test_nested_focus_invalid_frames_and_priority(self):
        a = self.add('A', 'a', '--priority', 'high')
        b = self.add('B', 'b', '--priority', 'medium')
        c = self.add('C', 'c', '--priority', 'low')
        self.assertEqual(self.runcli('brief')['next']['uuid'], a)
        def focus(*args, **kw):
            rev = self.runcli('task', 'list')['revision']
            return self.runcli('focus', *args, '--expect', str(rev), **kw)
        for i, uid in enumerate((a,b,c)):
            focus('push', uid, '--key', 'push-' + str(i))
        self.assertEqual(focus('resume', '--key', 'resume-1')['focus'], b)
        self.assertEqual(focus('resume', '--key', 'resume-2')['focus'], a)
        focus('push', b, '--key', 'push-again')
        self.runcli('task', 'done', a, '--key', 'done-a')
        self.assertEqual(focus('resume', '--key', 'invalid', code=2)['code'], 'invalid_frame')
        self.assertIsNone(focus('resume', '--skip-invalid', '--key', 'skip')['focus'])
        self.assertFalse(any(r.get('start') for r in self.export()))

    def test_real_time_entrypoint_and_scheduler_dry_run(self):
        self.runcli('task', 'add', '[FICTICIO] reloj real', '--due', datetime.now(timezone.utc).isoformat(), '--key', 'real', direct=True)
        self.runcli('notify', direct=True)
        self.assertEqual([r['body'] for r in self.messages()], ['[FICTICIO] reloj real'])
        plan = self.runcli('schedule', '--dry-run', direct=True)
        self.assertFalse(plan['installed'])
        self.assertTrue(plan['dry_run'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
