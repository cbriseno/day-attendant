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
        with self.assertRaises(RuntimeError): self.request('task_add', {'title': 'Admitted responsibility'})
        self.app.fault = lambda point: None
        with self.app.store.connect() as con:
            key = 'proposal:' + con.execute('SELECT id FROM proposals ORDER BY rowid DESC LIMIT 1').fetchone()[0]
        self.assertTrue(self.app.store.unresolved())
        return key, recovery.inspect(self.app, key)

    def cli_recover(self):
        return subprocess.run([sys.executable, '-B', '-m', 'day_attendant', '--json', '--state', self.temp.name,
                               'recover'], cwd=ROOT, capture_output=True, text=True, timeout=10)

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
        self.apply('dependency_update', {'uuid': uid, 'dependsOn': [prerequisite]})
        fact = self.board.snapshot()['events'][-1]['id']
        undo = self.board.undo(fact, 'remove-blocker-undo')
        self.assertEqual(undo['phase'], 'applied')
        row = self.app.tasks.get(uid)
        self.assertTrue(row['start']); self.assertNotIn('depends', row)
        fact = self.board.snapshot()['events'][-1]['id']
        with self.assertRaises(AssistantError) as error:
            self.board.undo(fact, 'restore-pending-blocker')
        self.assertEqual(error.exception.code, 'not_actionable')
        self.apply('task_done', {'uuid': prerequisite})
        undo = self.board.undo(fact, 'restore-completed-blocker')
        self.assertEqual(undo['phase'], 'applied')
        self.assertEqual(self.app.tasks.get(uid)['depends'], [prerequisite])

    def test_review_in_progress_never_blocks_and_drops_outdated_suggestions(self):
        prerequisite, uid = self.add('Prerequisite'), self.add('Active front')
        self.apply('task_done', {'uuid': prerequisite})
        self.apply('front_activate', {'uuid': uid})
        self.apply('dependency_update', {'uuid': uid, 'dependsOn': [prerequisite]})
        entered, release = threading.Event(), threading.Event()
        inputs, errors = [], []
        original = self.board.bridge.turn
        def blocked(thread, prompt, schema, **kwargs):
            inputs.append(json.loads(prompt[prompt.index('{'):])); entered.set()
            if not release.wait(10): raise RuntimeError('Review fixture was not released')
            result = original(thread, prompt, schema, **kwargs)
            result['output'] = {'suggestions': [{'taskId': uid, 'message': 'Written before the prerequisite reopened.', 'action': None}]}
            return result
        def review():
            try: self.board.review_changes()
            except BaseException as error: errors.append(error)
        self.board.bridge.turn = blocked
        worker = threading.Thread(target=review); worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual({t['id'] for t in inputs[0]['closed']}, {prerequisite})
            self.assertIn(uid, {t['id'] for t in inputs[0]['tasks']})
            started = time.monotonic()
            self.apply('task_reopen', {'uuid': prerequisite})
            self.assertLess(time.monotonic() - started, 2)
        finally:
            release.set(); worker.join(10); self.board.bridge.turn = original
        self.assertFalse(worker.is_alive()); self.assertEqual(errors, [])
        # The front changed during the turn, so the note written for its old state is not stored.
        self.assertNotIn('Written before the prerequisite reopened.', [s['message'] for s in self.board.snapshot()['suggestions']])
        task = next(t for t in self.board.snapshot()['tasks'] if t['id'] == uid)
        self.assertEqual(task['state'], 'active'); self.assertEqual(task['blockedBy'], [prerequisite])
        self.board.review_changes()
        self.assertIn((uid, 'active_blocked'), {(s['taskId'], s['code']) for s in self.board.snapshot()['suggestions'] if s['current']})

    def test_later_review_sees_the_changed_task_and_both_prerequisites_only(self):
        old, new, uid, unrelated = (self.add(title) for title in ('Old prerequisite', 'New prerequisite', 'Front', 'Unrelated'))
        self.apply('task_done', {'uuid': old})
        self.apply('dependency_update', {'uuid': uid, 'dependsOn': [old]})
        self.apply('front_activate', {'uuid': uid})
        self.board.review_changes()
        supplied = []
        original = self.board.bridge.turn
        def record(thread, prompt, schema, **kwargs):
            supplied.append(json.loads(prompt[prompt.index('{'):]))
            return original(thread, prompt, schema, **kwargs)
        self.apply('dependency_update', {'uuid': uid, 'dependsOn': [new]})
        with patch.object(self.board.bridge, 'turn', side_effect=record): self.board.review_changes()
        self.assertEqual(supplied[0]['changes'][-1]['fields']['dependsOn'], [[old], [new]])
        self.assertEqual({t['id'] for t in supplied[0]['tasks']}, {uid, new})
        self.assertEqual({t['id'] for t in supplied[0]['closed']}, {old})
        self.assertEqual(supplied[0]['noted'], [{'taskId': uid, 'code': 'active_blocked'}])
        self.assertNotIn(unrelated, json.dumps(supplied[0]))
        self.assertEqual(self.app.tasks.get(uid)['depends'], [new])

    def test_admission_rechecks_native_state_after_external_change(self):
        prerequisite, uid = self.add('Prerequisite'), self.add('Front')
        self.apply('task_done', {'uuid': prerequisite}); self.apply('dependency_update', {'uuid': uid, 'dependsOn': [prerequisite]})
        p = self.legacy_pending('front_activate', {'uuid': uid})
        self.app.tasks.patch(prerequisite, {'status': 'pending', 'end': None})
        with self.assertRaises(AssistantError) as error: self.board.retry(p['id'])
        self.assertEqual(error.exception.code, 'not_actionable')
        self.assertEqual(self.board.proposal(p['id'])['phase'], 'invalid')
        self.assertFalse(self.app.tasks.get(uid).get('start'))

    def test_explicit_folds_reach_snooze_event_bounds_observations_and_horizon(self):
        # Keep snoozes future to the test clock at a historical DST transition.
        self.app.clock = lambda: NOW.replace(year=2025)
        uid = self.add('Snoozable front')
        clash = self.apply('event_add', {'title': 'Later occurrence', 'start': '2025-11-02T08:00:00Z',
                                       'end': '2025-11-02T09:00:00Z'})['result']['uuid']
        for fold, hour in ((0, '07'), (1, '08')):
            with self.subTest(fold=fold):
                self.apply('task_snooze', {'uuid': uid, 'until': '2025-11-02T01:30', 'zone': 'America/Edmonton', 'fold': fold})
                self.board.review_changes()
                noted = [s for s in self.board.snapshot()['suggestions'] if s['current'] and s['code'] == 'calendar']
                self.assertEqual([s['action']['payload']['until'] for s in noted], ['2025-11-02T09:00:00Z'] if fold == 1 else [])
                task = next(t for t in self.board.snapshot()['tasks'] if t['id'] == uid)
                self.assertEqual(task['until'], '2025-11-02T' + hour + ':30:00Z')
                event = self.apply('event_add', {'title': 'Repeated event', 'start': '2025-11-02T01:10',
                    'end': '2025-11-02T01:50', 'zone': 'America/Edmonton', 'fold': fold,
                    'observed_at': '2024-11-03T01:30'})['result']['uuid']
                current = next(e for e in self.board.snapshot()['calendar_events'] if e['uuid'] == event)
                self.assertEqual(current['start_at'], '2025-11-02T' + hour + ':10:00Z')
                self.assertEqual(current['end_at'], '2025-11-02T' + hour + ':50:00Z')
                self.assertEqual(current['observed_at'], '2024-11-03T' + hour + ':30:00Z')
                for field, minute in (('start', '15'), ('end', '45')):
                    self.apply('event_update', {'uuid': event, field: '2025-11-02T01:' + minute,
                                               'zone': 'America/Edmonton', 'fold': fold})
                    current = next(e for e in self.board.snapshot()['calendar_events'] if e['uuid'] == event)
                    self.assertEqual(current[field + '_at'], '2025-11-02T' + hour + ':' + minute + ':00Z')
                event = self.apply('event_add', {'title': 'Profile-zone observation',
                    'start': '2026-11-02T12:00:00Z', 'end': '2026-11-02T13:00:00Z',
                    'observed_at': '2024-11-03T01:30', 'fold': fold})['result']['uuid']
                current = next(e for e in self.board.snapshot()['calendar_events'] if e['uuid'] == event)
                self.assertEqual(current['observed_at'], '2024-11-03T' + hour + ':30:00Z')

    def test_unresolved_repeated_times_never_create_or_change_a_proposal(self):
        self.app.clock = lambda: NOW.replace(year=2025)
        uid = self.add('Front')
        event = self.apply('event_add', {'title': 'Original event', 'start': '2025-11-02T06:00:00Z',
                                       'end': '2025-11-02T10:00:00Z'})['result']['uuid']
        cases = [('task_snooze', {'uuid': uid, 'until': '2025-11-02T01:30'}),
                 ('event_add', {'title': 'Unclear start', 'start': '2025-11-02T01:10', 'end': '2025-11-02T10:00:00Z'}),
                 ('event_add', {'title': 'Unclear end', 'start': '2025-11-02T06:00:00Z', 'end': '2025-11-02T01:50'}),
                 ('event_add', {'title': 'Unclear observation', 'start': '2025-11-02T06:00:00Z', 'end': '2025-11-02T10:00:00Z', 'observed_at': '2024-11-03T01:30'}),
                 ('event_update', {'uuid': event, 'start': '2025-11-02T01:10'}),
                 ('event_update', {'uuid': event, 'end': '2025-11-02T01:50'}),
                 ('task_add', {'title': 'Unclear deadline', 'due': '2025-11-02T01:30'}),
                 ('task_update', {'uuid': uid, 'due': '2025-11-02T01:30'})]
        before = self.board.snapshot()
        for action, payload in cases:
            with self.subTest(action=action, payload=payload), self.assertRaises(AssistantError) as error:
                self.request(action, dict(payload, zone='America/Edmonton'))
            self.assertEqual(error.exception.code, 'ambiguous_time')
        after = self.board.snapshot()
        for field in ('revision', 'tasks', 'calendar_events', 'proposals'): self.assertEqual(before[field], after[field])

    def test_legacy_due_and_observation_consumers_preserve_explicit_fold(self):
        with tempfile.TemporaryDirectory(prefix='day-review-fold-') as state:
            app = Assistant(state, clock=lambda: NOW.replace(year=2025))
            for fold, hour in ((0, '07'), (1, '08')):
                result = app.mutate('task_add', {'title': 'Resolved deadline', 'due': '2025-11-02T01:10',
                    'observed_at': '2024-11-03T01:30', 'zone': 'America/Edmonton', 'fold': fold}, 'fold-add-' + str(fold))
                uid = result['uuid']
                self.assertEqual(app.tasks.get(uid)['due'], '20251102T' + hour + '1000Z')
                self.assertEqual(app.store.metadata()[uid]['observed_at'], '2024-11-03T' + hour + ':30:00Z')
                app.mutate('task_update', {'uuid': uid, 'due': '2025-11-02T01:50', 'zone': 'America/Edmonton', 'fold': fold}, 'fold-edit-' + str(fold))
                self.assertEqual(app.tasks.get(uid)['due'], '20251102T' + hour + '5000Z')

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
app=Assistant(sys.argv[1])
try:
 print(json.dumps(lifecycle.start(app)))
 print(json.dumps(lifecycle.status(app)))
finally:lifecycle.stop(app)
print(lifecycle.cron_block(app,{'PATH':''}).splitlines()[1].split(' ',5)[5])
'''
            env = dict(os.environ, PYTHONPATH=str(site), PATH='')
            result = subprocess.run([sys.executable, '-B', '-c', driver, self.temp.name],
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

    def test_details_actions_reject_stale_opening_revision_without_native_effects(self):
        for index, (status, action) in enumerate((('todo', 'front_activate'), ('snoozed', 'front_activate'),
                ('done', 'task_reopen'), ('retired', 'task_reopen'), ('active', 'task_done'),
                ('todo', 'front_retire'), ('active', 'front_todo'))):
            with self.subTest(status=status, action=action):
                uid = self.add('Snapshot action ' + str(index))
                if status != 'todo':
                    setup = {'snoozed': 'task_snooze', 'done': 'task_done',
                             'retired': 'front_retire', 'active': 'front_activate'}[status]
                    self.apply(setup, {'uuid': uid, **({'until': '2026-11-01T08:30:21Z'} if status == 'snoozed' else {})})
                opening = self.board.snapshot()['revision']
                self.apply('task_reopen' if status in ('done', 'retired') else 'task_done', {'uuid': uid})
                before = self.board.snapshot()
                with self.assertRaises(AssistantError) as error:
                    self.board.request(action, {'uuid': uid}, 'stale-details-' + str(index), expected_revision=opening)
                self.assertEqual(error.exception.code, 'stale_revision')
                after = self.board.snapshot()
                for field in ('revision', 'tasks', 'proposals'):
                    self.assertEqual(before[field], after[field])

    def test_unrelated_edits_preserve_resolved_instants_in_native_stores(self):
        self.app.clock = lambda: NOW.replace(year=2025)
        for hour in ('07', '08', '10'):
            with self.subTest(utc_hour=hour):
                instant = '2025-11-02T' + hour + ':30:21Z'
                uid = self.apply('task_add', {'title': 'Original', 'due': instant})['result']['uuid']
                self.apply('task_update', {'uuid': uid, 'title': 'Title only'})
                task = next(t for t in self.board.snapshot()['tasks'] if t['id'] == uid)
                self.assertEqual(task['title'], 'Title only'); self.assertEqual(task['due'], instant)
                start, end = instant.replace(':21Z', ':45Z'), instant.replace(':30:21Z', ':50:59Z')
                event = self.apply('event_add', {'title': 'Original event', 'start': start, 'end': end})['result']['uuid']
                self.apply('event_update', {'uuid': event, 'title': 'Event title only', 'start': start, 'end': end})
                saved = next(e for e in self.board.snapshot()['calendar_events'] if e['uuid'] == event)
                self.assertEqual((saved['title'], saved['start_at'], saved['end_at']), ('Event title only', start, end))
                policy = dict(self.board.snapshot()['notification_policy'], pausedUntil=instant)
                configure(self.app, {'notification_policy': policy}, 'pause-' + hour)
                configure(self.app, {'name': 'Name only ' + hour, 'notification_policy': policy}, 'name-' + hour)
                self.assertEqual(self.board.snapshot()['notification_policy']['pausedUntil'], instant)
                self.apply('context_update', {'availableMinutes': 20, 'availableUntil': instant, 'rest': False})
                self.apply('context_update', {'availableMinutes': 20, 'availableUntil': instant, 'rest': True})
                self.assertEqual(self.board.snapshot()['context']['availableUntil'], instant)
                self.assertTrue(self.board.snapshot()['context']['rest'])

    def test_daily_ui_executable_regressions(self):
        node = shutil.which('node')
        chrome = shutil.which('google-chrome') or shutil.which('chromium')
        if not node or not chrome:
            self.skipTest('Node and Chrome on PATH are required for the browser regressions')
        result = subprocess.run([node, str(ROOT / 'scripts/test_daily_regressions.mjs'), chrome], cwd=ROOT,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    import unittest
    unittest.main()
