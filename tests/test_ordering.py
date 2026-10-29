"""Priority, groups, manual order and the on-request suggestion with real SQLite stores."""
from datetime import timedelta
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen

from day_attendant.archives import task_digest
from day_attendant.connected import Connected, SUGGESTION_SCHEMA
from day_attendant.conversation import FIELDS
from day_attendant.errors import AssistantError
from day_attendant.service import Assistant
from day_attendant.taskstore import TaskStore
from day_attendant.web import LocalServer
from test_connected import NOW, ReviewFixture

ROOT = Path(__file__).resolve().parent.parent


class OrderingFixture(ReviewFixture):
    """Controlled reviews plus a scripted suggestion; counts every model turn."""

    def __init__(self):
        self.calls = []
        self.suggestion = None

    def turn(self, thread, prompt, schema, **kwargs):
        self.calls.append(schema)
        if schema is SUGGESTION_SCHEMA:
            supplied = json.loads(prompt[prompt.index('{'):])
            output = self.suggestion(supplied) if self.suggestion else {
                'message': 'Start with the nearest deadline.',
                'order': [{'id': t['id'], 'reason': 'Reason for ' + t['title']} for t in reversed(supplied['tasks'])]}
            return {'output': output, 'model': self.model, 'thread_id': thread, 'turn_id': 'suggestion', 'seconds': 0, 'status': 'completed'}
        return super().turn(thread, prompt, schema, **kwargs)


class OrderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='day-ordering-test-')
        self.app = Assistant(self.temp.name, clock=lambda: NOW)
        self.fixture = OrderingFixture()
        self.board = Connected(self.app, self.fixture)
        self.board.initialize()
        self.counter = 0
        with self.app.store.connect() as con:
            self.ids = [json.loads(con.execute('SELECT result FROM operations WHERE key=?', (f'bootstrap-task-{i}',)).fetchone()[0])['uuid'] for i in range(4)]

    def tearDown(self):
        self.temp.cleanup()

    def request(self, action, payload, **kwargs):
        self.counter += 1
        return self.board.request(action, payload, f'ordering-{self.counter}', **kwargs)

    def apply(self, action, payload):
        proposal = self.request(action, payload)
        if proposal['phase'] == 'evaluating':
            self.board.evaluate(proposal['id'])
        final = self.board.proposal(proposal['id'])
        self.assertIn(final['phase'], ('applied', 'unchanged'))
        return final

    def task(self, uid):
        return next(t for t in self.board.snapshot()['tasks'] if t['id'] == uid)

    def test_group_is_a_native_label_set_on_capture_and_edit_and_undoable(self):
        uid = self.apply('task_add', {'title': 'Pack the kitchen', 'group': '  Move house  '})['result']['uuid']
        self.assertEqual(self.task(uid)['group'], 'Move house')
        self.assertEqual(self.app.tasks.get(uid)['project'], 'Move house')
        self.assertTrue(self.task(uid)['createdAt'])
        self.assertIsNone(self.task(self.ids[0])['group'])
        self.apply('task_update', {'uuid': uid, 'group': 'Home'})
        with self.app.store.connect() as con:
            fact = con.execute('SELECT id FROM facts ORDER BY id DESC LIMIT 1').fetchone()[0]
        undo = self.board.undo(fact, 'ordering-undo')
        self.board.evaluate(undo['id'])
        self.assertEqual(self.board.proposal(undo['id'])['phase'], 'applied')
        self.assertEqual(self.task(uid)['group'], 'Move house')
        self.apply('task_update', {'uuid': uid, 'group': ''})
        self.assertIsNone(self.task(uid)['group'])
        self.assertNotIn('project', self.app.tasks.get(uid))
        for value, code in (('x' * 121, 'invalid_group'), ('Two\nlines', 'invalid_group'), (7, 'invalid_request')):
            with self.assertRaises(AssistantError) as raised:
                self.request('task_update', {'uuid': uid, 'group': value})
            self.assertEqual(raised.exception.code, code)
        with self.assertRaises(AssistantError):
            TaskStore.validate({'uuid': uid, 'description': 'x', 'status': 'pending', 'project': 7})

    def test_manual_order_is_a_reviewless_declaration_that_changes_no_task(self):
        before = task_digest(self.app.tasks.rows())
        priorities = {t['id']: t['priority'] for t in self.board.snapshot()['tasks']}
        # A pending review on another task must survive rearranging.
        pending = self.request('front_activate', {'uuid': self.ids[1]})
        self.assertEqual(pending['phase'], 'evaluating')
        revision = self.app.store.revision()
        order = list(reversed(self.ids))
        proposal = self.board.request('order_update', {'order': order}, 'drag-1', expected_revision=revision)
        self.assertEqual(proposal['phase'], 'applied')
        self.assertEqual(self.fixture.calls, [])
        snapshot = self.board.snapshot()
        self.assertEqual(snapshot['taskOrder'], order)
        self.assertEqual(snapshot['revision'], revision + 1)
        self.assertEqual(task_digest(self.app.tasks.rows()), before)
        self.assertEqual({t['id']: t['priority'] for t in snapshot['tasks']}, priorities)
        self.assertEqual(self.board.proposal(pending['id'])['phase'], 'evaluating')
        with self.app.store.connect() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM messages WHERE key LIKE 'context-notice:%'").fetchone()[0], 0)
        # Same key is the same intent; the same order under a new key changes nothing.
        self.assertEqual(self.board.request('order_update', {'order': order}, 'drag-1')['id'], proposal['id'])
        self.assertEqual(self.board.request('order_update', {'order': order}, 'drag-2')['phase'], 'unchanged')
        self.assertEqual(self.app.store.revision(), revision + 1)
        for payload, code in (({'order': order + [order[0]]}, 'invalid_order'), ({'order': 'x'}, 'invalid_order'),
                              ({'order': ['not-a-uuid']}, 'invalid_uuid'), ({'order': ['00000000-0000-4000-8000-000000000000']}, 'task_not_found'),
                              ({'order': order, 'priority': 'high'}, 'unsupported_action')):
            with self.assertRaises(AssistantError) as raised:
                self.request('order_update', payload)
            self.assertEqual(raised.exception.code, code)
        with self.assertRaises(AssistantError) as raised:
            self.board.request('order_update', {'order': self.ids}, 'drag-stale', expected_revision=revision)
        self.assertEqual(raised.exception.code, 'stale_revision')
        # Closed tasks leave the visible order; the stored arrangement survives a restart.
        self.apply('task_done', {'uuid': self.ids[3]})
        restarted = Connected(Assistant(self.temp.name, clock=lambda: NOW), self.fixture)
        restarted.startup()
        self.assertEqual(restarted.snapshot()['taskOrder'], order)
        self.assertEqual(self.app.store.state_value('task_order'), order)

    def test_rearranging_never_queues_capacity_guidance(self):
        clock = [NOW]
        self.app.clock = lambda: clock[0]
        def notices():
            with self.app.store.connect() as con:
                return con.execute("SELECT count(*) FROM messages WHERE key LIKE 'context-notice:%'").fetchone()[0]
        self.apply('task_update', {'uuid': self.ids[0], 'due': (NOW + timedelta(minutes=45)).isoformat()})
        self.apply('context_update', {'availableMinutes': 30, 'availableUntil': (NOW + timedelta(minutes=45)).isoformat(), 'rest': False})
        self.assertEqual(notices(), 1)
        # A shorter remaining window gives the capacity concern a new signature.
        clock[0] = NOW + timedelta(minutes=20)
        self.board.request('order_update', {'order': list(reversed(self.ids))}, 'drag-under-pressure')
        self.assertEqual(notices(), 1)
        self.apply('provenance_update', {'uuid': self.ids[0], 'person': 'Ana'})
        self.assertEqual(notices(), 2)

    def test_conversation_cannot_propose_a_manual_order(self):
        self.assertNotIn('order_update', FIELDS['action']['enum'])
        self.assertIn('group', FIELDS)

    def test_suggestion_runs_once_per_request_and_is_reused(self):
        for _ in range(3):
            self.assertIsNone(self.board.snapshot()['orderSuggestion'])
        self.assertEqual(self.fixture.calls, [])
        record = self.board.request_suggestion('suggest-1')
        self.assertEqual(record['request']['status'], 'pending')
        self.board.suggest_order('suggest-1')
        self.assertEqual(self.fixture.calls, [SUGGESTION_SCHEMA])
        suggestion = self.board.snapshot()['orderSuggestion']
        self.assertEqual(suggestion['request']['status'], 'completed')
        self.assertEqual([item['id'] for item in suggestion['result']['order']], list(reversed(self.ids)))
        self.assertEqual(suggestion['result']['model'], 'controlled-fixture')
        self.assertFalse(suggestion['stale'])
        # Repeating the request key or reading the state again spends nothing.
        self.assertEqual(self.board.request_suggestion('suggest-1')['request']['status'], 'completed')
        self.board.suggest_order('suggest-1')
        self.board.snapshot()
        self.assertEqual(len(self.fixture.calls), 1)
        # Rearranging by hand does not outdate it; a task change does, without a new call.
        self.board.request('order_update', {'order': self.ids}, 'drag-after-suggestion')
        self.assertFalse(self.board.snapshot()['orderSuggestion']['stale'])
        self.apply('task_update', {'uuid': self.ids[2], 'priority': 'high'})
        calls = len(self.fixture.calls)
        suggestion = self.board.snapshot()['orderSuggestion']
        self.assertTrue(suggestion['stale'])
        self.assertEqual(len(suggestion['result']['order']), 4)
        self.assertEqual(len(self.fixture.calls), calls)

    def test_suggestion_input_is_open_work_only_and_in_the_interface_language(self):
        self.apply('task_done', {'uuid': self.ids[3]})
        prompts = []
        self.fixture.suggestion = lambda supplied: prompts.append(supplied) or {'message': 'ok', 'order': []}
        self.board.request_suggestion('suggest-open')
        self.board.suggest_order('suggest-open')
        self.assertEqual({t['id'] for t in prompts[0]['tasks']}, set(self.ids[:3]))
        self.assertNotIn('fingerprint', prompts[0])
        self.assertEqual(self.board.snapshot()['orderSuggestion']['result']['order'], [])

    def test_invalid_suggestion_is_incomplete_and_keeps_the_previous_one(self):
        self.board.request_suggestion('good')
        self.board.suggest_order('good')
        previous = self.board.snapshot()['orderSuggestion']['result']
        for index, output in enumerate(({'message': 'x', 'order': [{'id': '00000000-0000-4000-8000-000000000000', 'reason': 'x'}]},
                                        {'message': 'x', 'order': [{'id': self.ids[0], 'reason': 'x'}, {'id': self.ids[0], 'reason': 'y'}]},
                                        {'message': 'x'})):
            self.fixture.suggestion = lambda supplied, output=output: output
            self.board.request_suggestion(f'bad-{index}')
            self.board.suggest_order(f'bad-{index}')
            suggestion = self.board.snapshot()['orderSuggestion']
            self.assertEqual(suggestion['request']['status'], 'incomplete')
            self.assertEqual(suggestion['request']['code'], 'agent_invalid_output')
            self.assertEqual(suggestion['result'], previous)

    def test_busy_restart_and_missing_assistant_are_explicit(self):
        self.board.request_suggestion('first')
        with self.assertRaises(AssistantError) as raised:
            self.board.request_suggestion('second')
        self.assertEqual(raised.exception.code, 'suggestion_busy')
        restarted = Connected(Assistant(self.temp.name, clock=lambda: NOW), self.fixture)
        restarted.startup()
        request = restarted.snapshot()['orderSuggestion']['request']
        self.assertEqual((request['status'], request['code']), ('incomplete', 'restart_interrupted'))
        self.board.suggest_order('first')
        self.assertEqual(self.fixture.calls, [])
        offline = Connected(Assistant(self.temp.name, clock=lambda: NOW), None)
        with self.assertRaises(AssistantError) as raised:
            offline.request_suggestion('offline')
        self.assertEqual(raised.exception.code, 'agent_unavailable')
        self.assertEqual(offline.snapshot()['orderSuggestion']['request']['key'], 'first')

    def test_ordering_rules_in_node(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the pure ordering rules')
        result = subprocess.run([node, str(ROOT / 'scripts/test_ordering.mjs')], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class OrderingHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='day-ordering-http-')
        self.app = Assistant(self.temp.name)
        Connected(self.app).initialize()
        self.fixture = OrderingFixture()
        self.server = LocalServer(self.app, self.fixture)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.pool.shutdown(wait=True)
        self.server.server_close()
        self.temp.cleanup()

    def call(self, path, body=None):
        request = Request(self.server.origin + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={'Content-Type': 'application/json', 'Origin': self.server.origin, 'Authorization': 'Bearer ' + self.server.token})
        with urlopen(request, timeout=10) as response:
            return response.read() if path.endswith('.js') else json.load(response)['data']

    def test_suggestion_endpoint_and_static_ordering_script(self):
        self.assertIn(b'dayOrdering', self.call('/ordering.js'))
        self.assertEqual(self.call('/api/suggestion', {'key': 'http-suggest'})['request']['status'], 'pending')
        deadline = time.time() + 10
        while self.call('/api/state')['orderSuggestion']['request']['status'] == 'pending' and time.time() < deadline:
            time.sleep(0.05)
        state = self.call('/api/state')
        self.assertEqual(state['orderSuggestion']['request']['status'], 'completed')
        self.assertEqual(len(state['orderSuggestion']['result']['order']), 4)
        self.call('/api/suggestion', {'key': 'http-suggest'})
        self.call('/api/state')
        self.assertEqual(self.fixture.calls, [SUGGESTION_SCHEMA])


if __name__ == '__main__':
    unittest.main()
