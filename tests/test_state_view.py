"""The browser's state view stays small as history grows; the CLI keeps the full snapshot."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

from day_attendant.connected import Connected, PENDING, RECENT_PROPOSALS
from day_attendant.service import Assistant, encoded
from day_attendant.timeutil import parse
from day_attendant.web import LocalServer
from test_connected import NOW, ReviewFixture

ROOT = Path(__file__).resolve().parent.parent


class StateViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='day-state-view-test-')
        self.app = Assistant(self.temp.name, clock=lambda: NOW)
        self.board = Connected(self.app, ReviewFixture())
        self.board.initialize()
        self.counter = 0
        with self.app.store.connect() as con:
            self.ids = [json.loads(con.execute('SELECT result FROM operations WHERE key=?', (f'bootstrap-task-{i}',)).fetchone()[0])['uuid'] for i in range(4)]

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, action, payload):
        self.counter += 1
        proposal = self.board.request(action, payload, f'view-{self.counter}')
        if proposal['phase'] == 'evaluating':
            self.board.evaluate(proposal['id'])
        return self.board.proposal(proposal['id'])

    def toggle(self, times):
        for i in range(times):
            self.apply('front_activate' if i % 2 == 0 else 'front_todo', {'uuid': self.ids[0]})

    def test_view_does_not_grow_with_history(self):
        self.toggle(RECENT_PROPOSALS + 5)
        small, full = len(encoded(self.board.view())), len(encoded(self.board.snapshot()))
        self.toggle(40)
        view = self.board.view()
        self.assertLess(len(encoded(view)) - small, 512)
        self.assertGreater(len(encoded(self.board.snapshot())) - full, 20 * 1024)
        self.assertEqual(len(view['events']), 1)
        self.assertNotIn('plan', view['events'][0])
        self.assertLessEqual(len(view['proposals']), RECENT_PROPOSALS)
        self.assertTrue(all(set(p) == {'id', 'key', 'phase', 'origin'} for p in view['proposals']))

    def test_last_fact_summary_matches_its_plan(self):
        self.apply('task_done', {'uuid': self.ids[1]})
        fact = self.board.snapshot()['events'][-1]
        plan = json.loads(fact['plan'])
        summary = self.board.view()['events'][0]
        self.assertEqual(summary['id'], fact['id'])
        self.assertEqual(summary['taskId'], self.ids[1])
        self.assertEqual(summary['undoable'], bool(plan['steps']) and all(k == 'attachments_link' for k in plan['effect']))
        self.assertTrue(summary['undoable'])
        self.assertTrue(summary['reviewComplete'])
        undo = self.board.undo(fact['id'], 'view-undo')
        self.board.evaluate(undo['id'])
        self.assertEqual(self.board.view()['events'][0]['undoOf'], fact['id'])

    def test_pending_and_cited_proposals_stay_visible(self):
        pending = self.board.request('task_update', {'uuid': self.ids[2], 'title': 'Pending title'}, 'view-pending')
        cited = self.apply('task_update', {'uuid': self.ids[3], 'title': 'Cited title'})
        with self.app.store.connect() as con:
            con.execute("INSERT INTO messages(key,role,text,status,data,at) VALUES('chat-1:assistant','assistant','Saved.','completed',?,?)",
                        (encoded({'proposal_ids': [cited['id']]}), self.app.now()))
        self.toggle(RECENT_PROPOSALS + 5)
        ids = {p['id']: p for p in self.board.view()['proposals']}
        self.assertIn(ids[pending['id']]['phase'], PENDING)
        self.assertEqual(ids[pending['id']]['intent']['payload']['title'], 'Pending title')
        self.assertEqual(ids[cited['id']]['phase'], 'applied')

    def test_internal_history_matches_recorded_plans(self):
        self.toggle(5)
        self.apply('task_update', {'uuid': self.ids[1], 'priority': 'low'})
        for uid in self.ids[:2]:
            # A second read reuses the cached fact targets.
            for _ in range(2):
                proposal = {'id': 'probe', 'key': 'probe', 'intent': {'action': 'task_done', 'payload': {'uuid': uid}}, 'generation': 0, 'origin': 'ui', 'request_text': ''}
                history = self.board._input(proposal)['evidence']['history.confirmed']
                expected = [{'action': f['action'], 'at': f['at']} for f in self.board.snapshot()['events'] if json.loads(f['plan']).get('target_uuid') == uid][-8:]
                self.assertEqual(history, expected)
                self.assertTrue(history)

    def test_compact_dates_parse_like_strptime(self):
        def reference(value):
            try:return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
            except ValueError as error:return type(error)
        for value in ('20261029T120000Z', '20240229T235959Z', '20230229T000000Z', '20261329T000000Z', '20261029T240000Z',
                      '20261029T120060Z', '00001029T120000Z', '+0261029T120000Z', '2026102 T120000Z', '\uff120261029T120000Z'):
            with self.subTest(value=value):
                try:result = parse(value)
                except ValueError as error:result = type(error)
                self.assertEqual(result, reference(value))

    def test_tag_ignores_the_clock_and_follows_content(self):
        first = self.board.view()
        self.app.clock = lambda: NOW.replace(minute=5)
        second = self.board.view()
        self.assertNotEqual(first['now'], second['now'])
        self.assertEqual(first['tag'], second['tag'])
        self.apply('task_update', {'uuid': self.ids[0], 'priority': 'low'})
        self.assertNotEqual(self.board.view()['tag'], first['tag'])


class StateRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='day-state-route-test-')
        self.app = Assistant(self.temp.name)
        Connected(self.app).initialize()
        self.server = LocalServer(self.app, ReviewFixture())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        Path(self.temp.name, 'ui-server.json').write_text(json.dumps({'origin': self.server.origin, 'token': self.server.token, 'pid': 0}))
        with self.app.store.connect() as con:
            self.uid = json.loads(con.execute("SELECT result FROM operations WHERE key='bootstrap-task-1'").fetchone()[0])['uuid']
        board = self.server.board
        for i in range(6):
            proposal = board.request('front_activate' if i % 2 == 0 else 'front_todo', {'uuid': self.uid}, f'route-{i}')
            board.evaluate(proposal['id'])

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.pool.shutdown(wait=True)
        self.server.server_close()
        self.temp.cleanup()

    def get(self, path):
        request = Request(self.server.origin + path, headers={'Authorization': 'Bearer ' + self.server.token})
        with urlopen(request, timeout=10) as response:
            body = response.read()
        return len(body), json.loads(body)['data']

    def test_unchanged_poll_is_tiny_and_full_view_stays_for_the_cli(self):
        _, view = self.get('/api/state')
        self.assertNotIn('plan', view['events'][0])
        size, unchanged = self.get('/api/state?since=' + view['tag'])
        self.assertEqual(unchanged, {'unchanged': True, 'tag': view['tag'], 'now': unchanged['now']})
        self.assertLess(size, 2048)
        _, changed = self.get('/api/state?since=stale-tag')
        self.assertEqual(changed['tag'], view['tag'])
        self.assertIn('tasks', changed)
        _, full = self.get('/api/state?view=full')
        self.assertEqual(len(full['events']), 6)
        self.assertIn('plan', full['events'][-1])
        listed = subprocess.run([sys.executable, '-B', 'day-attendant.py', '--json', '--state', self.temp.name, 'board', 'list'],
                                cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual([e['id'] for e in json.loads(listed.stdout)['data']['events']], [e['id'] for e in full['events']])


if __name__ == '__main__':
    unittest.main()
