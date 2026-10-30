"""Original creation stays native and stable across the connected action lifecycle."""
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from day_attendant.connected import Connected
from day_attendant.service import Assistant
from test_connected import ReviewFixture


class CreationTests(unittest.TestCase):
    def test_original_entry_survives_changes_undo_and_restart(self):
        with tempfile.TemporaryDirectory(prefix='day-created-') as state:
            now = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)
            board = Connected(Assistant(state, clock=lambda: now), ReviewFixture())
            board.initialize(kind='personal', zone='America/Edmonton')
            def apply(action, payload, key):
                return board.request(action, payload, key, expected_revision=board.snapshot()['revision'])
            uid = apply('task_add', {'title': 'Original task'}, 'create')['result']['uuid']
            original = board.snapshot()['tasks'][0]['createdAt']
            self.assertEqual(original, '2026-10-20T18:00:00Z')
            for n, (action, payload) in enumerate([
                ('task_update', {'title': 'Edited title', 'priority': 'high', 'group': 'Project'}),
                ('front_activate', {}), ('front_todo', {}),
                ('task_snooze', {'until': '2026-11-20T18:00:00Z'}),
                ('task_done', {}), ('task_reopen', {}), ('front_retire', {}),
            ]):
                now += timedelta(hours=1)
                apply(action, {'uuid': uid, **payload}, f'change-{n}')
                self.assertEqual(board.snapshot()['tasks'][0]['createdAt'], original)
                fact = board.snapshot()['lastFact']['id']
                board.undo(fact, f'undo-{n}')
                self.assertEqual(board.snapshot()['tasks'][0]['createdAt'], original)
                apply(action, {'uuid': uid, **payload}, f'reapply-{n}')
            board = Connected(Assistant(state, clock=lambda: now), ReviewFixture())
            self.assertEqual(board.snapshot()['tasks'][0]['createdAt'], original)

    def test_historical_native_rows_and_missing_entry_are_not_backfilled(self):
        with tempfile.TemporaryDirectory(prefix='day-created-history-') as state:
            app = Assistant(state)
            board = Connected(app, ReviewFixture())
            board.initialize(kind='personal', zone='UTC')
            from uuid import uuid4
            old, missing = str(uuid4()), str(uuid4())
            app.tasks.create({'uuid': old, 'description': 'Old imported task', 'entry': '20200101T120000Z'})
            # Native historical/import shape: an explicit missing entry, no SQL mutation.
            app.tasks.create({'uuid': missing, 'description': 'Unknown creation', 'entry': None})
            tasks = {t['id']: t for t in board.snapshot()['tasks']}
            self.assertEqual(tasks[old]['createdAt'], '2020-01-01T12:00:00Z')
            self.assertIsNone(tasks[missing]['createdAt'])
            self.assertIsNone(app.tasks.get(missing).get('entry'))
