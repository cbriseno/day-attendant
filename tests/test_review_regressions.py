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
from day_attendant.service import Assistant, encoded
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

    def test_proposed_prerequisite_reopening_cancels_late_allow_and_requires_new_review(self):
        prerequisite, uid = self.add('Prerequisite'), self.add('Active front')
        self.apply('task_done', {'uuid': prerequisite})
        self.apply('front_activate', {'uuid': uid})
        p = self.request('dependency_update', {'uuid': uid, 'dependsOn': [prerequisite]})
        entered, release = threading.Event(), threading.Event()
        inputs, cancellations, errors = [], [], []
        original = self.board.bridge.turn
        def blocked(thread, prompt, schema, **kwargs):
            supplied = json.loads(prompt[prompt.index('{'):])
            if supplied['proposal_id'] == p['id']:
                inputs.append(supplied); cancellations.append(kwargs['canceled']); entered.set()
                if not release.wait(10): raise RuntimeError('Review fixture was not released')
            return original(thread, prompt, schema, **kwargs)
        def evaluate():
            try: self.board.evaluate(p['id'])
            except BaseException as error: errors.append(error)
        self.board.bridge.turn = blocked
        worker = threading.Thread(target=evaluate); worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual(inputs[0]['evidence']['relationships.prerequisites'],
                             [{'id': prerequisite, 'title': 'Prerequisite', 'state': 'done'}])
            self.assertIn(prerequisite, {t['id'] for t in inputs[0]['tasks']})
            self.apply('task_reopen', {'uuid': prerequisite})
            self.assertEqual(self.board.proposal(p['id'])['phase'], 'stale')
            self.assertTrue(cancellations[0].is_set())
        finally:
            release.set(); worker.join(10); self.board.bridge.turn = original
        self.assertFalse(worker.is_alive()); self.assertEqual(errors, [])
        self.assertNotIn('depends', self.app.tasks.get(uid))
        retried = self.board.retry(p['id']); self.board.evaluate(retried['id'])
        current = self.board.proposal(p['id'])
        self.assertEqual(current['phase'], 'review')
        self.assertIn('risk_active_blocked', {c.get('message_key') for c in current['facts']})
        self.assertNotIn('depends', self.app.tasks.get(uid))
        self.board.confirm(current['id'], current['generation'])
        task = next(t for t in self.board.snapshot()['tasks'] if t['id'] == uid)
        self.assertEqual(task['state'], 'active'); self.assertEqual(task['blockedBy'], [prerequisite])

    def test_replacement_review_tracks_current_and_proposed_prerequisites_only(self):
        old, new, uid, unrelated = (self.add(title) for title in ('Old prerequisite', 'New prerequisite', 'Front', 'Unrelated'))
        for prerequisite in (old, new): self.apply('task_done', {'uuid': prerequisite})
        self.apply('dependency_update', {'uuid': uid, 'dependsOn': [old]})
        self.apply('front_activate', {'uuid': uid})
        p = self.request('dependency_update', {'uuid': uid, 'dependsOn': [new]})
        supplied = []
        original = self.board.bridge.turn
        def insufficient(thread, prompt, schema, **kwargs):
            supplied.append(json.loads(prompt[prompt.index('{'):]))
            result = original(thread, prompt, schema, **kwargs)
            result['output']['verdict'] = 'insufficient'
            return result
        with patch.object(self.board.bridge, 'turn', side_effect=insufficient): self.board.evaluate(p['id'])
        evidence = supplied[0]['evidence']
        self.assertEqual({t['id'] for t in evidence['relationships.prerequisites']}, {old, new})
        self.assertTrue({old, new} <= {t['id'] for t in supplied[0]['tasks']})
        self.assertEqual(supplied[0]['proposed_task']['dependsOn'], [new])
        self.assertEqual(self.board.proposal(p['id'])['fingerprint'], supplied[0]['fingerprint'])
        self.apply('task_update', {'uuid': unrelated, 'title': 'Unrelated correction'})
        self.assertEqual(self.board.proposal(p['id'])['phase'], 'unavailable')
        for prerequisite in (new, old):
            self.apply('task_update', {'uuid': prerequisite, 'title': 'Corrected ' + prerequisite})
            self.assertEqual(self.board.proposal(p['id'])['phase'], 'stale')
            self.board.retry(p['id'])
        self.assertEqual(self.app.tasks.get(uid)['depends'], [old])

    def test_admission_rechecks_new_prerequisite_after_external_native_change(self):
        prerequisite, uid = self.add('Prerequisite'), self.add('Front')
        self.apply('task_done', {'uuid': prerequisite}); self.apply('front_activate', {'uuid': uid})
        p = self.request('dependency_update', {'uuid': uid, 'dependsOn': [prerequisite]})
        original = self.board.bridge.turn
        def insufficient(*args, **kwargs):
            result = original(*args, **kwargs); result['output']['verdict'] = 'insufficient'; return result
        with patch.object(self.board.bridge, 'turn', side_effect=insufficient): self.board.evaluate(p['id'])
        self.app.tasks.patch(prerequisite, {'status': 'pending', 'end': None})
        with self.assertRaises(AssistantError) as error:
            self.board.confirm(p['id'], p['generation'], without_review=True)
        self.assertEqual(error.exception.code, 'stale_proposal')
        self.assertEqual(self.board.proposal(p['id'])['phase'], 'stale')
        self.assertNotIn('depends', self.app.tasks.get(uid))

    def test_explicit_folds_reach_snooze_event_bounds_observations_and_horizon(self):
        uid = self.add('Snoozable front')
        clash = self.apply('event_add', {'title': 'Later occurrence', 'start': '2026-11-01T08:00:00Z',
                                       'end': '2026-11-01T09:00:00Z'})['result']['uuid']
        for fold, hour in ((0, '07'), (1, '08')):
            with self.subTest(fold=fold):
                p = self.request('task_snooze', {'uuid': uid, 'until': '2026-11-01T01:30',
                                               'zone': 'America/Edmonton', 'fold': fold})
                self.board.evaluate(p['id']); p = self.board.proposal(p['id'])
                with self.app.store.connect() as con:
                    inputs = json.loads(con.execute('SELECT input FROM evaluations WHERE proposal_id=? ORDER BY id DESC', (p['id'],)).fetchone()[0])
                self.assertEqual(clash in {e['uuid'] for e in inputs['calendar_events']}, fold == 1)
                if fold == 1:
                    self.assertEqual(p['phase'], 'review'); self.board.confirm(p['id'], p['generation'])
                else: self.assertEqual(p['phase'], 'applied')
                task = next(t for t in self.board.snapshot()['tasks'] if t['id'] == uid)
                self.assertEqual(task['until'], '2026-11-01T' + hour + ':30:00Z')
                event = self.apply('event_add', {'title': 'Repeated event', 'start': '2026-11-01T01:10',
                    'end': '2026-11-01T01:50', 'zone': 'America/Edmonton', 'fold': fold,
                    'observed_at': '2025-11-02T01:30'})['result']['uuid']
                current = next(e for e in self.board.snapshot()['calendar_events'] if e['uuid'] == event)
                self.assertEqual(current['start_at'], '2026-11-01T' + hour + ':10:00Z')
                self.assertEqual(current['end_at'], '2026-11-01T' + hour + ':50:00Z')
                self.assertEqual(current['observed_at'], '2025-11-02T' + hour + ':30:00Z')
                for field, minute in (('start', '15'), ('end', '45')):
                    self.apply('event_update', {'uuid': event, field: '2026-11-01T01:' + minute,
                                               'zone': 'America/Edmonton', 'fold': fold})
                    current = next(e for e in self.board.snapshot()['calendar_events'] if e['uuid'] == event)
                    self.assertEqual(current[field + '_at'], '2026-11-01T' + hour + ':' + minute + ':00Z')
                event = self.apply('event_add', {'title': 'Profile-zone observation',
                    'start': '2026-11-02T12:00:00Z', 'end': '2026-11-02T13:00:00Z',
                    'observed_at': '2025-11-02T01:30', 'fold': fold})['result']['uuid']
                current = next(e for e in self.board.snapshot()['calendar_events'] if e['uuid'] == event)
                self.assertEqual(current['observed_at'], '2025-11-02T' + hour + ':30:00Z')

    def test_unresolved_repeated_times_never_create_or_change_a_proposal(self):
        uid = self.add('Front')
        event = self.apply('event_add', {'title': 'Original event', 'start': '2026-11-01T06:00:00Z',
                                       'end': '2026-11-01T10:00:00Z'})['result']['uuid']
        cases = [('task_snooze', {'uuid': uid, 'until': '2026-11-01T01:30'}),
                 ('event_add', {'title': 'Unclear start', 'start': '2026-11-01T01:10', 'end': '2026-11-01T10:00:00Z'}),
                 ('event_add', {'title': 'Unclear end', 'start': '2026-11-01T06:00:00Z', 'end': '2026-11-01T01:50'}),
                 ('event_add', {'title': 'Unclear observation', 'start': '2026-11-01T06:00:00Z', 'end': '2026-11-01T10:00:00Z', 'observed_at': '2025-11-02T01:30'}),
                 ('event_update', {'uuid': event, 'start': '2026-11-01T01:10'}),
                 ('event_update', {'uuid': event, 'end': '2026-11-01T01:50'}),
                 ('task_add', {'title': 'Unclear deadline', 'due': '2026-11-01T01:30'}),
                 ('task_update', {'uuid': uid, 'due': '2026-11-01T01:30'})]
        before = self.board.snapshot()
        for action, payload in cases:
            with self.subTest(action=action, payload=payload), self.assertRaises(AssistantError) as error:
                self.request(action, dict(payload, zone='America/Edmonton'))
            self.assertEqual(error.exception.code, 'ambiguous_time')
        after = self.board.snapshot()
        for field in ('revision', 'tasks', 'calendar_events', 'proposals'): self.assertEqual(before[field], after[field])

    def test_legacy_due_and_observation_consumers_preserve_explicit_fold(self):
        with tempfile.TemporaryDirectory(prefix='day-review-fold-') as state:
            app = Assistant(state, clock=lambda: NOW)
            for fold, hour in ((0, '07'), (1, '08')):
                result = app.mutate('task_add', {'title': 'Resolved deadline', 'due': '2026-11-01T01:10',
                    'observed_at': '2025-11-02T01:30', 'zone': 'America/Edmonton', 'fold': fold}, 'fold-add-' + str(fold))
                uid = result['uuid']
                self.assertEqual(app.tasks.get(uid)['due'], '20261101T' + hour + '1000Z')
                self.assertEqual(app.store.metadata()[uid]['observed_at'], '2025-11-02T' + hour + ':30:00Z')
                app.mutate('task_update', {'uuid': uid, 'due': '2026-11-01T01:50', 'zone': 'America/Edmonton', 'fold': fold}, 'fold-edit-' + str(fold))
                self.assertEqual(app.tasks.get(uid)['due'], '20261101T' + hour + '5000Z')

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
