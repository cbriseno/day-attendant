"""Focused native-store and executable UI regressions for daily readiness."""
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

from day_attendant import archives, lifecycle, recovery
from day_attendant.conversation import Conversation, FIELDS
from day_attendant.errors import AssistantError
from day_attendant.platforms import exclusive
from day_attendant.profiles import configure
from day_attendant.service import encoded
from test_daily_domain import DailyBase
from test_connected import NOW
from test_conversation import ChatFixture

ROOT = Path(__file__).resolve().parent.parent


class ReviewRegressions(DailyBase):
    def setUp(self):
        super().setUp()
        self.output = tempfile.TemporaryDirectory(prefix='day-review-')
        self.addCleanup(self.output.cleanup)

    def interrupted(self):
        self.app.fault = lambda point: (_ for _ in ()).throw(RuntimeError('interrupted')) if point == 'after_admission' else None
        p = self.request('task_add', {'title': 'Admitted responsibility'})
        self.board.evaluate(p['id'])
        self.app.fault = lambda point: None
        key = 'proposal:' + p['id']
        self.assertTrue(self.app.store.unresolved())
        return key, recovery.inspect(self.app, key)

    def cli_recover(self):
        return subprocess.run([sys.executable, '-B', '-m', 'day_attendant', '--json', '--state', self.temp.name,
                               '--task-bin', self.app.tasks.binary, 'recover'], cwd=ROOT, capture_output=True, text=True, timeout=10)

    def audit_count(self):
        with self.app.store.connect() as con:
            return con.execute('SELECT count(*) FROM reconciliations').fetchone()[0]

    def test_live_maintenance_blocks_public_recovery_and_common_effects(self):
        key, observed = self.interrupted()
        with self.app.store.connect() as con:
            original = dict(con.execute('SELECT * FROM operations WHERE key=?', (key,)).fetchone())
        with archives.maintenance(self.app, 'review-live-owner'):
            result = self.cli_recover()
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(json.loads(result.stdout)['error']['code'], 'maintenance')
            with self.app.store.lock():
                for execute in (self.app._recover, lambda: self.app._apply(key, json.loads(original['plan']))):
                    with self.assertRaises(AssistantError) as error:
                        execute()
                    self.assertEqual(error.exception.code, 'maintenance')
            with self.app.store.connect() as con:
                self.assertEqual(dict(con.execute('SELECT * FROM operations WHERE key=?', (key,)).fetchone()), original)
            self.assertEqual(self.app.tasks.rows(), [])
        result = self.cli_recover()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['data']['unresolved'], [])
        self.assertEqual(len(self.app.tasks.rows()), 1)
        self.assertEqual(self.audit_count(), 0)

    def test_keep_observed_waiting_for_transport_rejects_concurrent_cli_recover(self):
        key, observed = self.interrupted()
        results, errors = [], []
        def resolve():
            try:
                results.append(recovery.resolve(self.app, key, 'keep_observed', observed['precondition'], self.output.name))
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=resolve)
        with exclusive(self.app.store.path / 'delivery.lock', shared=True):
            worker.start()
            deadline = time.monotonic() + 5
            while not self.app.store.state_value('maintenance') and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(self.app.store.state_value('maintenance'))
            result = self.cli_recover()
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(json.loads(result.stdout)['error']['code'], 'maintenance')
            self.assertEqual(self.app.tasks.rows(), [])
            self.assertEqual(self.audit_count(), 0)
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(results[0]['closed'])
        self.assertEqual(results[0]['resolution'], 'keep_observed')
        self.assertEqual(self.app.tasks.rows(), [])
        self.assertEqual(self.audit_count(), 1)
        self.assertTrue(archives.verify(results[0]['backup']['path'])['recovery_required'])

    def test_unrecorded_closing_actor_is_stale_before_resolution(self):
        key, observed = self.interrupted()
        self.assertEqual(self.cli_recover().returncode, 0)
        for resolution in ('keep_observed', 'complete'):
            with self.subTest(resolution=resolution), self.assertRaises(AssistantError) as error:
                recovery.resolve(self.app, key, resolution, observed['precondition'], self.output.name)
            self.assertEqual(error.exception.code, 'stale_recovery')
        self.assertEqual(self.audit_count(), 0)
        self.assertEqual(list(Path(self.output.name).glob('*.zip')), [])

    def test_unrecorded_closing_actor_is_stale_before_and_after_snapshot(self):
        native_snapshot = archives.snapshot
        for stage in ('before', 'after'):
            for resolution in ('keep_observed', 'complete'):
                with self.subTest(stage=stage, resolution=resolution):
                    key, observed = self.interrupted()
                    def close(*args, **kwargs):
                        if stage == 'before':
                            self.assertEqual(self.cli_recover().returncode, 0)
                        result = native_snapshot(*args, **kwargs)
                        if stage == 'after':
                            self.assertEqual(self.cli_recover().returncode, 0)
                        return result
                    with patch.object(archives, 'snapshot', side_effect=close), self.assertRaises(AssistantError) as error:
                        recovery.resolve(self.app, key, resolution, observed['precondition'], self.output.name)
                    self.assertEqual(error.exception.code, 'stale_recovery')
                    self.assertEqual(self.audit_count(), 0)
        self.assertEqual(len(self.app.tasks.rows()), 4)

    def test_matching_reconciliation_is_idempotent_for_both_resolutions(self):
        for resolution in ('keep_observed', 'complete'):
            with self.subTest(resolution=resolution):
                key, observed = self.interrupted()
                before = len(self.app.tasks.rows())
                result = recovery.resolve(self.app, key, resolution, observed['precondition'], self.output.name)
                count = self.audit_count()
                again = recovery.resolve(self.app, key, resolution, observed['precondition'], self.output.name)
                self.assertEqual(result, again)
                self.assertEqual(self.audit_count(), count)
                self.assertEqual(len(self.app.tasks.rows()), before + (resolution == 'complete'))
                for other, precondition in ((resolution, 'stale'), ('complete' if resolution == 'keep_observed' else 'keep_observed', observed['precondition'])):
                    with self.assertRaises(AssistantError) as error:
                        recovery.resolve(self.app, key, other, precondition, self.output.name)
                    self.assertEqual(error.exception.code, 'stale_recovery')
        self.assertEqual(len(list(Path(self.output.name).glob('*.zip'))), 2)

    def test_tick_preserves_delivery_failure_and_still_creates_daily_backup(self):
        policy = self.app.store.state_value('notification_policy')
        policy.update(weekdays=list(range(7)), start='00:00', end='23:59', quietStart='00:00', quietEnd='00:00')
        configure(self.app, {'notification_policy': policy, 'backup_directory': self.output.name}, 'review-policy')
        key, observed = self.interrupted()
        result = lifecycle.tick(self.app)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['delivery']['error']['code'], 'recovery_required')
        self.assertTrue(archives.verify(result['backup']['path'])['recovery_required'])
        self.assertEqual(self.app.tasks.rows(), [])
        self.assertEqual(self.app.store.state_value('last_tick'), result)

    def test_tick_retains_both_delivery_and_backup_failures(self):
        with patch.object(self.app, 'notify', side_effect=AssistantError('delivery_failed', 'Delivery failed')), \
             patch.object(archives, 'snapshot', side_effect=AssistantError('backup_space', 'No room')):
            result = lifecycle.tick(self.app)
        self.assertEqual(result['delivery']['error']['code'], 'delivery_failed')
        self.assertEqual(result['backup']['error']['code'], 'backup_space')
        self.assertEqual(result['status'], 'failed')

    def test_capture_persists_eight_outcomes_and_remaining_item_with_question(self):
        text = 'Capture these nine responsibilities.'
        intents = []
        for n in range(8):
            intent = dict.fromkeys(FIELDS)
            intent.update(action='task_add', title='Item ' + str(n + 1), clearDue=False,
                          userAuthorized=True, requestEvidence=text)
            intents.append(intent)
        self.board.bridge = ChatFixture({'message': 'Checking the items.', 'intent': intents[0],
            'additionalIntents': intents[1:], 'unprocessed': ['Ninth responsibility'],
            'uncertainties': ['Which deadline applies to the ninth responsibility?']})
        chat = Conversation(self.board)
        chat.submit(text, 'review-nine-items'); chat.run('review-nine-items')
        state = self.board.snapshot()
        data = json.loads(next(m['data'] for m in state['messages'] if m['role'] == 'assistant'))
        self.assertEqual(len(data['proposal_ids']), 8)
        self.assertEqual(data['unprocessed'], ['Ninth responsibility'])
        self.assertEqual(data['uncertainties'], ['Which deadline applies to the ninth responsibility?'])
        self.assertEqual(len(self.app.tasks.rows()), 8)

    def test_dependency_undo_uses_resulting_dependencies_in_both_directions(self):
        prerequisite, uid = self.add('Prerequisite'), self.add('Active front')
        self.apply('front_activate', {'uuid': uid})
        p = self.request('dependency_update', {'uuid': uid, 'dependsOn': [prerequisite]})
        self.board.evaluate(p['id']); p = self.board.proposal(p['id'])
        self.assertEqual(p['phase'], 'review')
        self.board.confirm(p['id'], p['generation'])
        fact = self.board.snapshot()['events'][-1]['id']
        undo = self.board.undo(fact, 'remove-blocker-undo')
        self.board.evaluate(undo['id'])
        self.assertEqual(self.board.proposal(undo['id'])['phase'], 'applied')
        row = self.app.tasks.get(uid)
        self.assertTrue(row['start']); self.assertNotIn('depends', row)
        fact = self.board.snapshot()['events'][-1]['id']
        with self.assertRaises(AssistantError) as error:
            self.board.undo(fact, 'restore-pending-blocker')
        self.assertEqual(error.exception.code, 'not_actionable')
        self.apply('task_done', {'uuid': prerequisite})
        undo = self.board.undo(fact, 'restore-completed-blocker')
        self.board.evaluate(undo['id'])
        self.assertEqual(self.board.proposal(undo['id'])['phase'], 'applied')
        self.assertEqual(self.app.tasks.get(uid)['depends'], [prerequisite])

    def test_scheduler_switches_both_directions_and_preserves_unrelated_jobs(self):
        self.scheduler_scenario()

    def test_scheduler_failed_replacement_keeps_uninstalled_receipt(self):
        self.scheduler_scenario(fail=True)

    def scheduler_scenario(self, fail=False):
        home = Path(self.output.name)
        caps = {'user_systemd': False, 'crontab': 'review-crontab'}
        original = 'MAILTO=""\n17 3 * * * /usr/bin/true\n'
        table = [original]; active = set()
        def run(args, input=None, allow_missing=False):
            if args[0] == 'review-crontab':
                if args[1] == '-': table[0] = input
                return subprocess.CompletedProcess(args, 0, table[0] if args[1] == '-l' else '', '')
            if 'enable' in args:
                self.assertEqual(table[0], original)
                if fail: raise AssistantError('scheduler_failed', 'Controlled replacement failure')
                active.add(args[-1])
            if 'disable' in args: active.discard(args[-1])
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(lifecycle, 'capabilities', return_value=caps), patch.object(lifecycle, 'state_home', return_value=home), \
             patch.object(lifecycle.Path, 'home', return_value=home), patch.object(lifecycle, 'run_checked', side_effect=run), \
             patch.object(lifecycle.sys, 'platform', 'linux'):
            lifecycle.scheduler(self.app, 'install')
            self.assertNotEqual(table[0], original)
            caps['user_systemd'] = True
            if fail:
                with self.assertRaises(AssistantError): lifecycle.scheduler(self.app, 'install')
                self.assertFalse(lifecycle.scheduler(self.app, 'status')['installed'])
                self.assertEqual(table[0], original)
                self.assertEqual(active, set())
                return
            lifecycle.scheduler(self.app, 'install')
            self.assertEqual(len(active), 1)
            self.assertEqual(table[0], original)
            caps['user_systemd'] = False
            lifecycle.scheduler(self.app, 'install')
            self.assertEqual(active, set())
            self.assertNotEqual(table[0], original)
            lifecycle.scheduler(self.app, 'install')
            lifecycle.scheduler(self.app, 'uninstall'); lifecycle.scheduler(self.app, 'uninstall')
            self.assertEqual(table[0], original)
            self.assertFalse(self.app.doctor()['scheduler_installed_by_this_app'])

    def test_packaged_module_launches_service_and_cron_from_arbitrary_directory(self):
        runtime = ROOT / '.runtime'
        runtime.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime, prefix='review-package-') as stage:
            site = Path(stage) / 'installed'
            shutil.copytree(ROOT / 'day_attendant', site / 'day_attendant', ignore=shutil.ignore_patterns('__pycache__'))
            configure(self.app, {'notification_policy': {'enabled': False}, 'backup_directory': self.output.name}, 'packaged-policy')
            driver = '''import json,sys
from day_attendant.service import Assistant
from day_attendant import lifecycle
app=Assistant(sys.argv[1],sys.argv[2])
try:
 print(json.dumps(lifecycle.start(app)))
 print(json.dumps(lifecycle.status(app)))
finally:lifecycle.stop(app)
print(lifecycle.cron_block(app,{'PATH':''}).splitlines()[1].split(' ',5)[5])
'''
            env = dict(os.environ, PYTHONPATH=str(site), PATH='')
            result = subprocess.run([sys.executable, '-B', '-c', driver, self.temp.name, self.app.tasks.binary],
                                    cwd=self.output.name, env=env, capture_output=True, text=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            lines = result.stdout.splitlines()
            self.assertTrue(json.loads(lines[0])['running'])
            self.assertTrue(json.loads(lines[1])['running'])
            cron = subprocess.run(['/bin/sh', '-c', lines[2]], cwd=self.output.name, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(cron.returncode, 0, cron.stderr)
            self.assertEqual(self.app.store.state_value('last_tick')['status'], 'completed')
            self.assertIsNotNone(self.app.store.state_value('last_backup'))
            self.assertFalse(lifecycle.status(self.app)['running'])

    def test_daily_ui_executable_regressions(self):
        node = shutil.which('node')
        chrome = shutil.which('google-chrome') or shutil.which('chromium')
        if not node or not chrome:
            self.skipTest('Node and Chrome on PATH are required for the browser regressions')
        result = subprocess.run([node, str(ROOT / 'scripts/test_daily_regressions.mjs'), chrome], cwd=ROOT,
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    import unittest
    unittest.main()
