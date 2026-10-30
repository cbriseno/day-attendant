"""Closing dates are native persisted facts, not inferred from creation or display order."""
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from day_attendant.connected import Connected
from day_attendant.service import Assistant
from test_connected import ReviewFixture

class ClosedTests(unittest.TestCase):
    def test_close_retire_reopen_and_undo_dates_survive_reopening_profile(self):
        with tempfile.TemporaryDirectory(prefix='day-closed-') as state:
            now = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)
            app = Assistant(state, clock=lambda: now)
            board = Connected(app, ReviewFixture())
            board.initialize(kind='personal', zone='America/Edmonton')
            def apply(action, payload, key):
                return board.request(action, payload, key, expected_revision=board.snapshot()['revision'])
            uid = apply('task_add', {'title': 'Conservar historial'}, 'add')['result']['uuid']
            now += timedelta(days=3)
            apply('task_done', {'uuid': uid}, 'done')
            closed = board.snapshot()['tasks'][0]['closedAt']
            self.assertTrue(closed.startswith('2026-10-23'))
            self.assertNotEqual(closed, board.snapshot()['tasks'][0]['createdAt'])
            board = Connected(Assistant(state, clock=lambda: now), ReviewFixture())
            self.assertEqual(board.snapshot()['tasks'][0]['closedAt'], closed)
            apply('task_reopen', {'uuid': uid}, 'reopen')
            self.assertIsNone(board.snapshot()['tasks'][0]['closedAt'])
            board.undo(board.snapshot()['lastFact']['id'], 'undo-reopen')
            self.assertEqual(board.snapshot()['tasks'][0]['closedAt'], closed)
            apply('task_reopen', {'uuid': uid}, 'reopen-again')
            now += timedelta(days=2)
            apply('front_retire', {'uuid': uid}, 'retire')
            self.assertTrue(board.snapshot()['tasks'][0]['closedAt'].startswith('2026-10-25'))
            self.assertEqual(len(board.snapshot()['tasks']), 1)
