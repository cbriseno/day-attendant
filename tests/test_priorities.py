"""Four priorities and the verified task-store upgrade, with real SQLite stores."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from day_attendant import archives
from day_attendant.cli import main
from day_attendant.connected import Connected
from day_attendant.errors import AssistantError
from day_attendant.service import Assistant
from day_attendant.taskstore import PRIORITIES, SCHEMA, TaskStore
from test_connected import NOW, ReviewFixture
from test_daily_domain import DailyBase


def task_schema(app):
    with sqlite3.connect(app.tasks.database) as con:
        return con.execute('PRAGMA user_version').fetchone()[0]


def raw_rows(app):
    with sqlite3.connect(app.tasks.database) as con:
        return con.execute('SELECT uuid,data FROM tasks ORDER BY rowid').fetchall()


class FourPriorityTests(DailyBase):
    def priorities(self):
        return {t['id']: t['priority'] for t in self.board.snapshot()['tasks']}

    def test_each_level_is_created_changed_and_stored_without_a_model_turn(self):
        self.assertEqual(list(PRIORITIES), ['urgent', 'high', 'medium', 'low'])
        ids = {p: self.apply('task_add', {'title': 'Level ' + p, 'priority': p})['result']['uuid'] for p in PRIORITIES}
        self.assertEqual(self.priorities(), {ids[p]: p for p in PRIORITIES})
        self.assertEqual({self.app.tasks.get(ids[p])['priority'] for p in PRIORITIES}, {'U', 'H', 'M', 'L'})
        self.apply('task_update', {'uuid': ids['low'], 'priority': 'urgent'})
        self.apply('task_update', {'uuid': ids['urgent'], 'priority': 'low'})
        self.assertEqual(self.priorities()[ids['low']], 'urgent')
        self.assertEqual(self.priorities()[ids['urgent']], 'low')
        with self.app.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM evaluations').fetchone()[0], 0)
        with self.assertRaises(AssistantError) as error:
            self.request('task_update', {'uuid': ids['high'], 'priority': 'critical'})
        self.assertEqual(error.exception.code, 'invalid_request')

    def test_undo_restores_the_previous_level_and_urgent_itself(self):
        uid = self.apply('task_add', {'title': 'Undo me', 'priority': 'urgent'})['result']['uuid']
        self.apply('task_update', {'uuid': uid, 'priority': 'medium'})
        self.board.undo(self.board.snapshot()['lastFact']['id'], 'undo-priority')
        self.assertEqual(self.app.tasks.get(uid)['priority'], 'U')

    def test_urgent_orders_first_in_the_brief_and_the_review_vocabulary(self):
        low = self.apply('task_add', {'title': 'Low', 'priority': 'low'})['result']['uuid']
        high = self.apply('task_add', {'title': 'High', 'priority': 'high'})['result']['uuid']
        urgent = self.apply('task_add', {'title': 'Urgent', 'priority': 'urgent'})['result']['uuid']
        self.assertEqual(self.app.brief()['next']['uuid'], urgent)
        self.apply('task_done', {'uuid': urgent})
        self.assertEqual(self.app.brief()['next']['uuid'], high)
        self.apply('task_done', {'uuid': high})
        self.assertEqual(self.app.brief()['next']['uuid'], low)
        from day_attendant.connected import followup_schema
        from day_attendant.conversation import FIELDS
        enum = followup_schema([low])['properties']['suggestions']['items']['properties']['action']['anyOf'][1]['properties']['priority']['enum']
        self.assertEqual(enum, ['urgent', 'high', 'medium', 'low', None])
        self.assertEqual(FIELDS['priority']['enum'], ['urgent', 'high', 'medium', 'low', None])

    def test_applied_change_reports_public_names(self):
        uid = self.apply('task_add', {'title': 'Name', 'priority': 'high'})['result']['uuid']
        self.apply('task_update', {'uuid': uid, 'priority': 'urgent'})
        with self.app.store.connect() as con:
            row = con.execute("SELECT * FROM facts WHERE action='task_update' ORDER BY id DESC").fetchone()
        self.assertEqual(Connected._change(row)['fields']['priority'], ['high', 'urgent'])


class TaskStoreUpgradeTests(DailyBase):
    """A profile from before urgent: task store schema 1 with high, medium and low rows."""

    def setUp(self):
        super().setUp()
        self.output = tempfile.TemporaryDirectory(prefix='day-priority-backup-')
        self.folder = Path(self.output.name) / 'backups'
        with self.app.store.connect() as con:
            con.execute("INSERT OR REPLACE INTO state VALUES('backup_directory',?)", (json.dumps(str(self.folder)),))
        self.ids = {p: self.apply('task_add', {'title': 'Old ' + p, 'priority': p})['result']['uuid'] for p in ('high', 'medium', 'low')}
        with sqlite3.connect(self.app.tasks.database) as con:
            con.execute('PRAGMA user_version=1')

    def tearDown(self):
        self.output.cleanup()
        super().tearDown()

    def test_old_store_still_opens_and_keeps_working_but_refuses_urgent_until_upgraded(self):
        reopened = Assistant(self.temp.name, clock=lambda: NOW)
        self.assertEqual(reopened.tasks.schema(), 1)
        revision, before = self.app.store.revision(), raw_rows(self.app)
        for action, payload in (('task_update', {'uuid': self.ids['low'], 'priority': 'urgent'}),
                                ('task_add', {'title': 'New urgent', 'priority': 'urgent'})):
            with self.subTest(action=action), self.assertRaises(AssistantError) as error:
                self.request(action, payload)
            self.assertEqual(error.exception.code, 'task_upgrade_required')
        self.assertEqual((self.app.store.revision(), raw_rows(self.app)), (revision, before))
        with self.app.store.connect() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM operations WHERE status NOT IN ('confirmed','abandoned')").fetchone())
        self.apply('task_update', {'uuid': self.ids['low'], 'priority': 'high'})

    def test_upgrade_takes_a_verified_backup_and_changes_no_row(self):
        before, revision = raw_rows(self.app), self.app.store.revision()
        receipt = archives.upgrade_tasks(self.app)
        self.assertEqual(task_schema(self.app), SCHEMA)
        self.assertEqual(raw_rows(self.app), before)
        self.assertEqual(self.app.store.revision(), revision)
        self.assertEqual({p: self.app.tasks.get(u)['priority'] for p, u in self.ids.items()}, {'high': 'H', 'medium': 'M', 'low': 'L'})
        manifest = archives.verify(receipt['backup']['path'])
        self.assertEqual(Path(receipt['backup']['path']).parent, self.folder.resolve())
        self.assertEqual((manifest['category'], manifest['task_fingerprint']), ('migration', archives.task_digest(self.app.tasks.rows())))
        self.assertEqual(self.app.store.state_value('priority_migration'), receipt)
        self.assertIsNone(archives.upgrade_tasks(self.app))
        self.assertEqual(len(list(self.folder.glob('*.zip'))), 1)
        self.apply('task_update', {'uuid': self.ids['low'], 'priority': 'urgent'})
        self.assertEqual(self.app.tasks.get(self.ids['low'])['priority'], 'U')

    def test_backup_of_the_old_store_restores_with_the_old_levels(self):
        receipt = archives.upgrade_tasks(self.app)
        restored = archives.restore(receipt['backup']['path'], Path(self.output.name) / 'restored')
        old = Assistant(restored['state'], clock=lambda: NOW)
        self.assertEqual(old.tasks.schema(), 1)
        self.assertEqual(sorted(r['priority'] for r in old.tasks.rows()), ['H', 'L', 'M'])

    def test_stale_backup_upgrades_nothing(self):
        real = archives.snapshot

        def racing(app, **kwargs):
            result = real(app, **kwargs)
            self.apply('task_add', {'title': 'Arrived during the backup'})
            return result
        archives.snapshot = racing
        try:
            with self.assertRaises(AssistantError) as error:
                archives.upgrade_tasks(self.app)
        finally:
            archives.snapshot = real
        self.assertEqual(error.exception.code, 'stale_migration')
        self.assertEqual(task_schema(self.app), 1)
        self.assertIsNone(self.app.store.state_value('priority_migration'))

    def test_profile_upgrade_command_upgrades_the_task_store(self):
        for expected in (True, False):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(['--state', self.temp.name, '--json', 'profile', 'upgrade']), 0)
            self.assertEqual(json.loads(output.getvalue())['data']['upgraded'], expected)
            self.assertEqual(task_schema(self.app), SCHEMA)
        self.assertEqual(len(list(self.folder.glob('*.zip'))), 1)

    def test_a_newer_store_is_refused_by_this_release_and_older_ones_detect_it(self):
        archives.upgrade_tasks(self.app)
        with sqlite3.connect(self.app.tasks.database) as con:
            con.execute(f'PRAGMA user_version={SCHEMA + 1}')
        with self.assertRaises(AssistantError) as error:
            TaskStore(self.app.store)
        self.assertEqual(error.exception.code, 'unsupported_task_schema')


class ServiceStartUpgradeTests(unittest.TestCase):
    def test_service_start_upgrades_an_old_store_before_serving(self):
        import os
        import subprocess
        import sys
        from urllib.request import Request, urlopen
        with tempfile.TemporaryDirectory(prefix='day-priority-serve-') as root:
            state, folder = Path(root) / 'profile', Path(root) / 'backups'
            app = Assistant(state)
            Connected(app, ReviewFixture()).initialize(kind='synthetic')
            with app.store.connect() as con:
                con.execute("INSERT OR REPLACE INTO state VALUES('backup_directory',?)", (json.dumps(str(folder)),))
            before = raw_rows(app)
            with sqlite3.connect(app.tasks.database) as con:
                con.execute('PRAGMA user_version=1')
            script = ("import sys\nfrom unittest.mock import patch\nfrom day_attendant.errors import AssistantError\nfrom day_attendant.web import serve\n"
                      "with patch('day_attendant.web.create_harness',side_effect=AssistantError('agent_auth_required','Controlled unavailable account')):\n"
                      "    serve(sys.argv[1],kind='synthetic')\n")
            process = subprocess.Popen([sys.executable, '-B', '-c', script, str(state)], cwd=Path(__file__).resolve().parent.parent,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, env=dict(os.environ, XDG_STATE_HOME=root))
            try:
                receipt = state / 'ui-server.json'
                for _ in range(150):
                    if receipt.exists() and receipt.read_text().strip():
                        break
                    if process.poll() is not None:
                        self.fail(process.stderr.read())
                    time.sleep(.1)
                data = json.loads(receipt.read_text())
                with urlopen(Request(data['origin'] + '/api/state', headers={'Authorization': 'Bearer ' + data['token']}), timeout=5) as r:
                    board = json.load(r)['data']
                self.assertEqual(len(board['tasks']), 4)
                self.assertEqual(task_schema(app), SCHEMA)
                self.assertEqual(raw_rows(app), before)
                self.assertEqual(len(list(folder.glob('*-migration.zip'))), 1)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stderr.close()


if __name__ == '__main__':
    unittest.main()
