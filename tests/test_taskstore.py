"""Portable SQLite operation, plus optional parity/import against the old engine."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from day_attendant import archives
from day_attendant.connected import Connected
from day_attendant.errors import AssistantError
from day_attendant.migration import migrate_profile
from day_attendant.platforms import exclusive
from day_attendant.profiles import configure
from day_attendant.service import Assistant
from day_attendant.store import Store
from day_attendant.taskstore import canonical
from test_connected import NOW, ReviewFixture

ROOT = Path(__file__).resolve().parents[1]
LEGACY = os.environ.get('DAY_ATTENDANT_LEGACY_TASK_BIN', '')


class SQLiteTasks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='day-sqlite-tasks-')
        self.app = Assistant(self.temp.name, clock=lambda: NOW)

    def tearDown(self):
        self.temp.cleanup()

    def row(self):
        return {'uuid': str(uuid.uuid4()), 'description': 'Responsabilidad humana',
                'status': 'pending', 'entry': '20261028T120000Z', 'priority': 'M'}

    def test_creates_are_idempotent_without_overwriting_conflicts(self):
        row = self.row()
        self.app.tasks.create(row)
        self.app.tasks.create(row)
        with self.assertRaises(AssistantError) as error:
            self.app.tasks.create(dict(row, description='Different task'))
        self.assertEqual(error.exception.code, 'external_conflict')
        self.assertEqual(self.app.tasks.get(row['uuid']), row)
        with sqlite3.connect(Path(self.temp.name) / 'tasks.sqlite3') as con:
            self.assertEqual(json.loads(con.execute('SELECT data FROM tasks').fetchone()[0]), row)

    def test_invalid_patch_rolls_back_and_concurrent_fields_are_not_lost(self):
        row = self.row()
        self.app.tasks.create(row)
        with self.assertRaises(AssistantError):
            self.app.tasks.patch(row['uuid'], {'description': 'Must roll back', 'status': 'invalid'})
        self.assertEqual(self.app.tasks.get(row['uuid']), row)
        def write(field):
            Assistant(self.temp.name).tasks.patch(row['uuid'], {field: field})
        fields = ['extra_' + str(n) for n in range(12)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, fields))
        saved = self.app.tasks.get(row['uuid'])
        self.assertTrue(all(saved[field] == field for field in fields))

    def test_actual_process_death_does_not_publish_half_a_row(self):
        row = self.row()
        self.app.tasks.create(row)
        code = '''import os,sqlite3,sys
con=sqlite3.connect(sys.argv[1]);con.execute('BEGIN IMMEDIATE')
con.execute("UPDATE tasks SET data='broken'");os._exit(75)
'''
        run = subprocess.run([sys.executable, '-B', '-c', code, str(self.app.tasks.database)])
        self.assertEqual(run.returncode, 75)
        self.assertEqual(Assistant(self.temp.name).tasks.get(row['uuid']), row)

    def test_no_taskwarrior_probe_even_with_a_poisoned_binary_setting(self):
        with patch.dict(os.environ, {'PATH': '', 'DAY_ATTENDANT_TASK_BIN': '/must-not-run/task'}), \
             patch('subprocess.run', side_effect=AssertionError('No native task command is allowed')):
            app = Assistant(self.temp.name, clock=lambda: NOW)
            result = app.mutate('task_add', {'title': 'Pure Python'}, 'no-native')
            app.mutate('task_done', {'uuid': result['uuid']}, 'done-native-free')
            self.assertEqual(app.tasks.get(result['uuid'])['status'], 'completed')

    def test_real_cli_initializes_and_reads_without_any_tools_on_path(self):
        with tempfile.TemporaryDirectory() as state:
            env = dict(os.environ, PATH='', DAY_ATTENDANT_TASK_BIN='/must-not-run/task')
            def run(*args):
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'day-attendant.py'),
                                         '--state', state, '--json', *args], env=env,
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return json.loads(result.stdout)['data']
            self.assertEqual(run('init', '--zone', 'America/Edmonton')['tasks'], 0)
            self.assertEqual(run('doctor')['backend'], 'python-sqlite')
            self.assertEqual(run('--task-bin', '/must-not-run/legacy-scheduler-hint', 'doctor')['backend'], 'python-sqlite')
            self.assertTrue((Path(state) / 'tasks.sqlite3').exists())
            self.assertFalse((Path(state) / 'taskwarrior').exists())

    def test_an_old_store_is_not_silently_replaced_with_an_empty_database(self):
        with tempfile.TemporaryDirectory() as state:
            legacy = Path(state) / 'taskwarrior'
            legacy.mkdir()
            (legacy / 'keep').write_text('old data')
            with self.assertRaises(AssistantError) as error:
                Assistant(state)
            self.assertEqual(error.exception.code, 'task_migration_required')
            self.assertFalse((Path(state) / 'tasks.sqlite3').exists())
            self.assertEqual((legacy / 'keep').read_text(), 'old data')

    def test_service_status_and_stop_do_not_need_the_old_task_engine(self):
        from day_attendant.cli import main
        Connected(self.app).initialize(kind='personal', zone='America/Edmonton')
        # Ownership inspection/shutdown must still work when the task constructor
        # refuses an old profile pending migration.
        with patch('day_attendant.cli.Assistant', side_effect=AssertionError('Task engine must not be constructed')):
            for command in ('status', 'stop'):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(['--state', self.temp.name, '--json', 'service', command]), 0)
                self.assertFalse(json.loads(output.getvalue())['data']['running'])

    def test_restore_rejects_a_valid_sqlite_store_with_a_broken_dependency_graph(self):
        Connected(self.app).initialize(kind='personal', zone='America/Edmonton')
        row = self.row()
        self.app.tasks.create(row)
        self.app.tasks.patch(row['uuid'], {'depends': [str(uuid.uuid4())]})
        with tempfile.TemporaryDirectory() as output:
            backup = archives.snapshot(self.app, Path(output) / 'backups')
            target = Path(output) / 'restored'
            with self.assertRaises(AssistantError):
                archives.restore(backup['path'], target)
            self.assertFalse(target.exists())


@unittest.skipUnless(LEGACY and Path(LEGACY).is_file(), 'Optional legacy import/parity check: set DAY_ATTENDANT_LEGACY_TASK_BIN')
class LegacyImport(unittest.TestCase):
    def setUp(self):
        from day_attendant.legacy_taskwarrior import Taskwarrior
        self.temp = tempfile.TemporaryDirectory(prefix='day-legacy-import-')
        self.output = tempfile.TemporaryDirectory(prefix='day-legacy-backups-')
        with patch('day_attendant.service.TaskStore', side_effect=lambda store: Taskwarrior(store, LEGACY)):
            self.app = Assistant(self.temp.name, clock=lambda: NOW)
        self.board = Connected(self.app, ReviewFixture())
        self.board.initialize(kind='personal', zone='America/Edmonton')
        configure(self.app, {'backup_directory': self.output.name}, 'migration-backups')
        self.counter = 0

    def tearDown(self):
        self.output.cleanup()
        self.temp.cleanup()

    def apply(self, action, payload):
        self.counter += 1
        p = self.board.request(action, payload, 'legacy-' + str(self.counter))
        if p['phase'] == 'evaluating':
            self.board.evaluate(p['id'])
        p = self.board.proposal(p['id'])
        self.assertIn(p['phase'], ('applied', 'unchanged'), p)
        return p

    def add(self, title):
        return self.apply('task_add', {'title': title})['result']['uuid']

    def test_native_fields_match_the_real_old_engine_after_each_operation(self):
        with tempfile.TemporaryDirectory() as state:
            native, portable = self.app.tasks, Assistant(state).tasks
            a, b = [str(uuid.uuid4()) for _ in range(2)]
            for backend in (native, portable):
                for uid, title in ((a, 'Prerrequisito'), (b, 'Dependent')):
                    backend.create({'uuid': uid, 'description': title, 'status': 'pending',
                                    'entry': '20261028T120000Z', 'priority': 'M'})
            for uid, changes in [(b, {'depends': [a]}),
                                 (b, {'description': 'Título literal', 'priority': 'H', 'due': '20271028T120000Z'}),
                                 (b, {'wait': '20271028T100000Z', 'start': None}),
                                 (b, {'wait': None, 'start': '20261028T120000Z'}),
                                 (a, {'status': 'completed', 'end': '20261028T120000Z', 'start': None, 'wait': None}),
                                 (b, {'depends': None, 'due': None, 'start': None}),
                                 (a, {'status': 'pending', 'end': None}),
                                 (b, {'status': 'deleted', 'end': '20261028T120000Z'})]:
                for backend in (native, portable):
                    backend.patch(uid, changes)
                self.assertEqual(sorted(map(canonical, native.rows()), key=lambda r: r['uuid']),
                                 sorted(map(canonical, portable.rows()), key=lambda r: r['uuid']), changes)

    def test_migration_preserves_native_rows_metadata_identity_and_backups(self):
        a, b = self.add('Responsabilidad'), self.add('Another responsibility')
        self.apply('dependency_update', {'uuid': b, 'dependsOn': [a]})
        self.apply('related_update', {'uuid': a, 'relatedTo': [b]})
        self.apply('continuity_update', {'uuid': a, 'context': 'Conservar contexto', 'next': 'Seguir aquí'})
        before = self.board.snapshot()
        rows = [canonical(r) for r in self.app.tasks.rows()]
        result = migrate_profile(self.temp.name, LEGACY)
        self.assertTrue(result['migrated'])
        self.assertEqual(archives.verify(result['backup']['path'])['format'], 1)
        self.assertTrue((Path(self.temp.name) / 'taskwarrior/data/taskchampion.sqlite3').exists())
        app = Assistant(self.temp.name, clock=lambda: NOW)
        board = Connected(app, ReviewFixture())
        board.initialize(); board.startup()
        self.assertEqual([canonical(r) for r in app.tasks.rows()], rows)
        for field in ('tasks', 'context', 'calendar_events', 'profile', 'notification_policy'):
            self.assertEqual(board.snapshot()[field], before[field], field)
        self.assertFalse(migrate_profile(self.temp.name, '/not-needed-on-retry')['migrated'])
        backup = archives.snapshot(app, self.output.name)
        self.assertEqual(archives.verify(backup['path'])['format'], 2)
        restored = archives.restore(backup['path'], Path(self.output.name) / 'restored')
        self.assertEqual([canonical(r) for r in Assistant(restored['state']).tasks.rows()], rows)

    def test_a_legacy_backup_converts_only_when_explicitly_requested(self):
        self.add('From the old backup')
        backup = archives.snapshot(self.app, self.output.name)
        target = Path(self.output.name) / 'converted'
        with self.assertRaises(AssistantError) as error:
            archives.restore(backup['path'], target)
        self.assertEqual(error.exception.code, 'legacy_backup')
        self.assertFalse(target.exists())
        result = archives.restore(backup['path'], target, LEGACY)
        self.assertEqual(Assistant(result['state']).tasks.rows()[0]['description'], 'From the old backup')

    def test_migration_is_offline_and_rejects_invalid_graphs_without_replacement(self):
        a = self.add('Preserve this')
        with exclusive(Path(self.temp.name) / 'server.lock', timeout=0):
            with self.assertRaises(AssistantError) as error:
                migrate_profile(self.temp.name, LEGACY)
        self.assertEqual(error.exception.code, 'busy')
        self.app.tasks.patch(a, {'depends': [str(uuid.uuid4())]})
        with self.assertRaises(AssistantError) as error:
            migrate_profile(self.temp.name, LEGACY)
        self.assertEqual(error.exception.code, 'invalid_dependencies')
        self.assertFalse((Path(self.temp.name) / 'tasks.sqlite3').exists())
        self.assertIsNone(self.app.store.state_value('maintenance'))

    def test_partial_operation_is_preserved_and_recovered_by_the_new_store(self):
        uid = self.add('Interrupted task')
        self.app.fault = lambda point: (_ for _ in ()).throw(RuntimeError('crash')) if point == 'after_task_0' else None
        p = self.board.request('front_activate', {'uuid': uid}, 'partial-before-migration')
        self.board.evaluate(p['id'])
        pending = self.app.store.unresolved()
        self.assertTrue(pending)
        migrate_profile(self.temp.name, LEGACY)
        app = Assistant(self.temp.name, clock=lambda: NOW)
        self.assertEqual(app.store.unresolved(), pending)
        self.assertEqual(app.recover()['unresolved'], [])
        self.assertTrue(app.tasks.get(uid).get('start'))

    def test_interruption_after_atomic_publication_leaves_a_complete_retryable_store(self):
        from day_attendant import migration
        uid = self.add('Survive migration interruption')
        publish = migration.publish_rows
        def interrupted(*args):
            publish(*args)
            raise OSError('Interrupted before migration receipt')
        with patch.object(migration, 'publish_rows', interrupted), self.assertRaises(AssistantError):
            migrate_profile(self.temp.name, LEGACY)
        app = Assistant(self.temp.name)
        self.assertEqual(app.tasks.get(uid)['description'], 'Survive migration interruption')
        self.assertIsNone(app.store.state_value('maintenance'))
        self.assertFalse(migrate_profile(self.temp.name, '/must-not-run/task')['migrated'])
        self.assertTrue(app.store.state_value('last_backup'))


if __name__ == '__main__':
    unittest.main()
