"""Direct actions apply in their request with Undo; the assistant only looks afterwards and suggests."""
from contextlib import contextmanager
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from day_attendant import web
from day_attendant.connected import Connected
from day_attendant.errors import AssistantError
from day_attendant.service import Assistant
from day_attendant.taskstore import canonical
from day_attendant.timeutil import stamp
from test_connected import NOW, ReviewFixture
from test_daily_domain import DailyBase


class RefusingBridge(ReviewFixture):
    """Any model turn in the click path is a failure."""
    def turn(self, *args, **kwargs):
        raise AssertionError('A direct action waited for the model.')


class InstantActionTests(DailyBase):
    def setUp(self):
        super().setUp()
        self.board.bridge = RefusingBridge()

    def state(self):
        snapshot = self.board.snapshot()
        return {'tasks': {r['uuid']: canonical(r) for r in self.app.tasks.rows()}, 'context': snapshot['context'],
                'order': snapshot['taskOrder'], 'continuity': {t['id']: (t['remainingMinutes'], t['awaiting'], t['context'], t['next']) for t in snapshot['tasks']},
                'related': {t['id']: t['relatedTo'] for t in snapshot['tasks']}, 'events': [(e['uuid'], e['title'], e['start_at'], e['end_at']) for e in snapshot['calendar_events']]}

    def test_every_direct_action_applies_at_once_and_can_be_undone(self):
        a, b, c = self.add('A'), self.add('B'), self.add('C')
        event = self.apply('event_add', {'title': 'Meeting', 'start': stamp(NOW + timedelta(hours=3)), 'end': stamp(NOW + timedelta(hours=4))})['result']['uuid']
        actions = [
            ('task_done', {'uuid': a}), ('front_activate', {'uuid': b}), ('task_snooze', {'uuid': c, 'until': stamp(NOW + timedelta(days=2))}),
            ('task_update', {'uuid': b, 'priority': 'high'}), ('task_add', {'title': 'Pay the light bill', 'group': 'Home'}),
            ('front_todo', {'uuid': b}), ('task_update', {'uuid': b, 'group': 'Home'}), ('task_update', {'uuid': b, 'title': 'B renamed', 'next': 'Call'}),
            ('order_update', {'order': [c, b, a]}), ('context_update', {'availableMinutes': 30, 'availableUntil': stamp(NOW + timedelta(hours=2)), 'rest': False}),
            ('continuity_update', {'uuid': b, 'remainingMinutes': 25}), ('dependency_update', {'uuid': b, 'dependsOn': [c]}),
            ('related_update', {'uuid': a, 'relatedTo': [b]}), ('task_reopen', {'uuid': a}), ('front_retire', {'uuid': a}),
            ('event_update', {'uuid': event, 'title': 'Moved meeting', 'start': stamp(NOW + timedelta(hours=5)), 'end': stamp(NOW + timedelta(hours=6))}),
            ('event_add', {'title': 'Dentist', 'start': stamp(NOW + timedelta(days=1)), 'end': stamp(NOW + timedelta(days=1, hours=1))}),
            ('event_cancel', {'uuid': event}),
        ]
        for action, payload in actions:
            with self.subTest(action=action, payload=payload):
                before = self.state()
                started = time.perf_counter()
                proposal = self.request(action, payload)
                elapsed = time.perf_counter() - started
                self.assertEqual(proposal['phase'], 'applied')
                self.assertLess(elapsed, 3.0)  # A model review takes 7–14 s; a direct action never waits for one.
                last = self.board.snapshot()['lastFact']
                self.assertTrue(last['undoable'], last)
                self.assertNotEqual(self.state(), before)
                undo = self.board.undo(last['id'], 'undo-' + str(last['id']))
                self.assertEqual(undo['phase'], 'applied')
                after = self.state()
                if action == 'task_add':
                    # A capture is retired, never erased.
                    created = proposal['result']['uuid']
                    self.assertEqual(after['tasks'][created]['status'], 'deleted')
                    del after['tasks'][created]
                    after['continuity'].pop(created); after['related'].pop(created)
                self.assertEqual(after, before)
                self.assertTrue(self.board.snapshot()['lastFact']['undoOf'])
                self.request(action, payload)  # Keep the change for the next action.
        with self.app.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM evaluations').fetchone()[0], 0)

    def test_undo_never_overwrites_a_later_change_to_the_same_data(self):
        a, b = self.add('A'), self.add('B')
        cases = [
            (('order_update', {'order': [a, b]}), ('order_update', {'order': [b, a]})),
            (('context_update', {'availableMinutes': 30, 'availableUntil': stamp(NOW + timedelta(hours=2))}), ('context_update', {'rest': True})),
            (('continuity_update', {'uuid': a, 'remainingMinutes': 20}), ('continuity_update', {'uuid': a, 'remainingMinutes': 5})),
            (('related_update', {'uuid': a, 'relatedTo': [b]}), ('related_update', {'uuid': b, 'relatedTo': []})),
            (('task_add', {'title': 'Captured'}), None),
        ]
        for first, later in cases:
            with self.subTest(first=first):
                self.apply(*first)
                fact = self.board.snapshot()['lastFact']['id']
                if later: self.apply(*later)
                else: self.apply('task_update', {'uuid': self.board.snapshot()['lastFact']['taskId'], 'title': 'Renamed after capture'})
                before = self.state()
                with self.assertRaises(AssistantError) as error: self.board.undo(fact, 'late-undo-' + str(fact))
                self.assertEqual(error.exception.code, 'undo_conflict')
                self.assertEqual(self.state(), before)

    def event(self, title='Dentist'):
        return self.apply('event_add', {'title': title, 'start': stamp(NOW + timedelta(days=1)), 'end': stamp(NOW + timedelta(days=1, hours=1)),
                                        'source': 'call', 'person': 'Clinic'})['result']['uuid']

    def event_row(self, uid):
        with self.app.store.connect() as con: return dict(con.execute('SELECT * FROM events WHERE uuid=?', (uid,)).fetchone())

    def test_undoing_a_cancellation_brings_back_the_same_commitment(self):
        uid = self.event()
        before = self.event_row(uid)
        self.apply('event_cancel', {'uuid': uid})
        last = self.board.snapshot()['lastFact']
        self.assertEqual((last['action'], last['taskId'], last['undoable']), ('event_cancel', uid, True))
        undo = self.board.undo(last['id'], 'undo-cancel')
        self.assertEqual((undo['phase'], undo['intent']['action']), ('applied', 'event_restore'))
        # Same permanent UUID, title, times and provenance; only the cancellation is reverted.
        self.assertEqual(self.event_row(uid), before)
        self.assertEqual([e['uuid'] for e in self.board.snapshot()['calendar_events']], [uid])
        # A retry with the same key replays the receipt instead of restoring twice.
        self.assertEqual(self.board.undo(last['id'], 'undo-cancel')['id'], undo['id'])
        with self.app.store.connect() as con:
            history = [r[0] for r in con.execute('SELECT action FROM facts ORDER BY id')]
        self.assertEqual(history, ['event_add', 'event_cancel', 'event_restore'])
        restored = self.board.snapshot()['lastFact']
        self.assertEqual((restored['undoOf'], restored['taskId']), (last['id'], uid))
        # The restore is itself a fact: undoing it cancels the commitment again, unless it was edited since.
        self.apply('event_update', {'uuid': uid, 'title': 'Dentist, confirmed'})
        with self.assertRaises(AssistantError) as error: self.board.undo(restored['id'], 'undo-restore-late')
        self.assertEqual(error.exception.code, 'undo_conflict')
        self.apply('event_update', {'uuid': uid, 'title': 'Dentist'})
        self.board.undo(restored['id'], 'undo-restore')
        self.assertEqual(self.board.snapshot()['calendar_events'], [])

    def test_cancellation_undo_never_overwrites_a_later_change(self):
        uid = self.event()
        self.apply('event_cancel', {'uuid': uid})
        fact = self.board.snapshot()['lastFact']['id']
        self.board.undo(fact, 'first-undo')
        self.apply('event_update', {'uuid': uid, 'title': 'Dentist, moved'})
        row = self.event_row(uid)
        # The commitment is active again and was edited since; a second Undo with a new key is refused.
        with self.assertRaises(AssistantError) as error: self.board.undo(fact, 'second-undo')
        self.assertEqual(error.exception.code, 'undo_conflict')
        self.assertEqual(self.event_row(uid), row)
        # A cancellation is undone only through its fact, never as a request of its own.
        self.apply('event_cancel', {'uuid': uid})
        revision = self.app.store.revision()
        with self.assertRaises(AssistantError) as error: self.board.request('event_restore', {'uuid': uid}, 'direct-restore')
        self.assertEqual(error.exception.code, 'invalid_restore')
        self.assertEqual(self.app.store.revision(), revision)
        self.assertEqual(self.board.snapshot()['calendar_events'], [])

    def test_cancellation_undo_recovers_once_after_process_death(self):
        root = Path(__file__).resolve().parent.parent
        script = '''
from datetime import datetime,timezone
import os,sys
from day_attendant.service import Assistant
from day_attendant.connected import Connected
def fault(point):
    if point==sys.argv[3]:os._exit(75)
app=Assistant(sys.argv[1],clock=lambda:datetime(2026,10,28,12,tzinfo=timezone.utc),fault=fault)
Connected(app).undo(int(sys.argv[2]),'death-undo-'+sys.argv[3])
'''
        for point in ('after_admission', 'inside_commit'):
            with self.subTest(point=point):
                uid = self.event('Dentist ' + point)
                before = self.event_row(uid)
                self.apply('event_cancel', {'uuid': uid})
                fact = self.board.snapshot()['lastFact']['id']
                result = subprocess.run([sys.executable, '-B', '-c', script, self.temp.name, str(fact), point], cwd=root, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 75, result.stderr)
                self.assertEqual(self.event_row(uid)['canceled'], 1)
                restarted = Connected(Assistant(self.temp.name, clock=lambda: NOW)); restarted.startup(); restarted.startup()
                self.assertEqual(self.event_row(uid), before)
                self.assertEqual(restarted.undo(fact, 'death-undo-' + point)['phase'], 'applied')
                with self.app.store.connect() as con:
                    self.assertEqual(con.execute("SELECT count(*) FROM facts WHERE action='event_restore' AND json_extract(plan,'$.target_uuid')=?", (uid,)).fetchone()[0], 1)
                self.assertEqual(self.app.store.unresolved(), [])

    def test_a_direct_change_writes_each_store_as_few_times_as_recovery_allows(self):
        uid = self.add('A')
        def writes(action, payload, key):
            counted = {'assistant': 0, 'tasks': 0}
            def count(owner, name):
                original = owner.connect
                @contextmanager
                def connect(*args, **kwargs):
                    with original(*args, **kwargs) as con:
                        before = con.total_changes
                        yield con
                        counted[name] += con.total_changes > before
                return patch.object(owner, 'connect', connect)
            with count(self.app.store, 'assistant'), count(self.app.tasks, 'tasks'):
                self.assertEqual(self.board.request(action, payload, key)['phase'], 'applied')
            return counted
        # Admission before any effect and the commit after it; the task store changes only in between.
        self.assertEqual(writes('task_update', {'uuid': uid, 'priority': 'high'}, 'one-task-write'), {'assistant': 2, 'tasks': 1})
        self.assertEqual(writes('order_update', {'order': [uid]}, 'one-order-write'), {'assistant': 2, 'tasks': 0})

    def test_a_drop_saves_its_change_and_new_place_as_one_fact_with_one_undo(self):
        a, b, c = self.add('A'), self.add('B'), self.add('C')
        self.apply('order_update', {'order': [a, b, c]})
        facts = self.board.snapshot()['lastFact']['id']
        for action, payload in (('front_activate', {'uuid': c}), ('task_update', {'uuid': c, 'priority': 'high'}), ('task_snooze', {'uuid': c, 'until': stamp(NOW + timedelta(days=1))})):
            with self.subTest(action=action):
                before, order = self.state(), [c, a, b]
                self.apply(action, dict(payload, order=order))
                last = self.board.snapshot()['lastFact']
                self.assertEqual((last['id'], last['action'], last['undoable']), (facts + 1, action, True))
                self.assertEqual(self.board.snapshot()['taskOrder'], order)
                self.board.undo(last['id'], 'undo-' + action)
                self.assertEqual(self.state(), before)
                facts = self.board.snapshot()['lastFact']['id']
        # A later reorder is not overwritten, and neither half of the drop is undone alone.
        self.apply('front_activate', {'uuid': c, 'order': [c, a, b]})
        fact = self.board.snapshot()['lastFact']['id']
        self.apply('order_update', {'order': [b, c, a]})
        with self.assertRaises(AssistantError) as error: self.board.undo(fact, 'undo-after-reorder')
        self.assertEqual(error.exception.code, 'undo_conflict')
        self.assertEqual((self.board.snapshot()['taskOrder'], self.app.tasks.get(c).get('start') is not None), ([b, c, a], True))
        with self.assertRaises(AssistantError) as error: self.board.request('front_todo', {'uuid': c, 'order': [c, c]}, 'bad-order')
        self.assertEqual(error.exception.code, 'invalid_order')

    def test_moves_made_together_undo_together(self):
        a, b, c = self.add('A'), self.add('B'), self.add('C')
        self.apply('order_update', {'order': [a, b, c]})
        first = self.board.request('order_update', {'order': [b, a, c]}, 'burst-first')
        second = self.board.request('order_update', {'order': [b, c, a]}, 'burst-second')
        self.board.undo_proposals([first['id'], second['id']], 'undo-burst')
        self.assertEqual(self.board.snapshot()['taskOrder'], [a, b, c])

    def test_a_retry_after_a_failed_apply_clears_its_error_and_finishes(self):
        uid = self.add('A')
        self.app.fault = lambda point: (_ for _ in ()).throw(AssistantError('interrupted', 'Interrupted.')) if point == 'before_commit' else None
        with self.assertRaises(AssistantError):
            self.board.request('task_update', {'uuid': uid, 'priority': 'high'}, 'failed-apply')
        self.assertEqual([(o['status'], o['error']) for o in self.app.store.unresolved()], [('applying', 'Interrupted.')])
        self.app.fault = lambda point: None
        self.assertEqual(self.board.startup() or self.app.store.unresolved(), [])
        self.assertEqual(self.app.tasks.get(uid)['priority'], 'H')

    def test_rejections_are_immediate_with_a_stable_code_and_save_nothing(self):
        a, b = self.add('A'), self.add('B')
        self.apply('dependency_update', {'uuid': a, 'dependsOn': [b]})
        revision = self.app.store.revision()
        for action, payload, code in (('dependency_update', {'uuid': b, 'dependsOn': [a]}, 'dependency_cycle'),
                                      ('dependency_update', {'uuid': a, 'dependsOn': [a]}, 'dependency_cycle'),
                                      ('task_done', {'uuid': 'not-a-uuid'}, 'invalid_uuid'),
                                      ('front_activate', {'uuid': '00000000-0000-4000-8000-000000000000'}, 'task_not_found'),
                                      ('related_update', {'uuid': a, 'relatedTo': [a]}, 'invalid_relations')):
            with self.subTest(code=code, payload=payload):
                started = time.perf_counter()
                with self.assertRaises(AssistantError) as error: self.request(action, payload)
                self.assertEqual(error.exception.code, code)
                self.assertLess(time.perf_counter() - started, 3.0)
        self.assertEqual(self.app.store.revision(), revision)
        self.assertEqual(self.app.store.unresolved(), [])

    def test_row_edits_save_one_field_at_a_time_and_keep_the_rest(self):
        """The row saves each field by itself; the others, and unrequested links, stay as they were."""
        a, b, c = self.add('A'), self.add('B'), self.add('C')
        self.apply('continuity_update', {'uuid': a, 'context': 'Page 3', 'next': 'Call', 'remainingMinutes': 40, 'awaiting': False})
        self.apply('task_update', {'uuid': a, 'group': 'Home', 'due': stamp(NOW + timedelta(days=1))})
        self.apply('dependency_update', {'uuid': a, 'dependsOn': [b]})
        task = lambda: next(t for t in self.board.snapshot()['tasks'] if t['id'] == a)
        for action, payload, field, value in (
                ('continuity_update', {'uuid': a, 'next': 'Email instead'}, 'next', 'Email instead'),
                ('continuity_update', {'uuid': a, 'awaiting': True}, 'awaiting', True),
                ('continuity_update', {'uuid': a, 'remainingMinutes': None}, 'remainingMinutes', None),
                ('task_update', {'uuid': a, 'title': 'A renamed'}, 'title', 'A renamed'),
                ('task_update', {'uuid': a, 'group': ''}, 'group', None),
                ('task_update', {'uuid': a, 'due': None}, 'due', None),
                ('dependency_update', {'uuid': a, 'dependsOn': [b, c]}, 'dependsOn', [b, c])):
            with self.subTest(payload=payload):
                before = task()
                self.assertEqual(self.request(action, payload)['phase'], 'applied')
                after = task()
                self.assertEqual(sorted(after[field]) if isinstance(value, list) else after[field], sorted(value) if isinstance(value, list) else value)
                self.assertEqual({k: v for k, v in after.items() if k not in (field, 'blocked', 'blockedBy')}, {k: v for k, v in before.items() if k not in (field, 'blocked', 'blockedBy')})
                # Undo puts back only that field.
                self.board.undo(self.board.snapshot()['lastFact']['id'], 'undo-row-' + field)
                self.assertEqual(task()[field], before[field]); self.request(action, payload)
        with self.app.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM evaluations').fetchone()[0], 0)
        # A field saved against an older revision is refused and changes nothing.
        revision = self.app.store.revision()
        self.apply('continuity_update', {'uuid': b, 'next': 'Elsewhere'})
        with self.assertRaises(AssistantError) as error:
            self.board.request('continuity_update', {'uuid': a, 'context': 'Stale draft'}, 'stale-row-edit', expected_revision=revision)
        self.assertEqual(error.exception.code, 'stale_revision'); self.assertEqual(task()['context'], 'Page 3')

    def test_review_spends_no_turn_without_new_changes(self):
        fixture = ReviewFixture(); self.board.bridge = fixture
        self.assertIsNone(self.board.review_changes())
        self.add('A'); self.add('B'); self.add('C')
        self.board.review_changes()
        self.assertEqual(fixture.turns, 1)
        for _ in range(3): self.assertIsNone(self.board.review_changes())
        self.assertEqual(fixture.turns, 1)
        restarted = Connected(Assistant(self.temp.name, clock=lambda: NOW), fixture); restarted.startup()
        self.assertIsNone(restarted.review_changes()); self.assertEqual(fixture.turns, 1)

    def test_finished_prerequisite_suggests_starting_the_unblocked_task(self):
        self.board.bridge = ReviewFixture()
        a, b = self.add('Buy paint'), self.add('Paint the room')
        self.apply('dependency_update', {'uuid': b, 'dependsOn': [a]})
        self.apply('task_done', {'uuid': a})
        self.board.review_changes()
        found = [s for s in self.board.snapshot()['suggestions'] if s['current']]
        self.assertEqual([(s['taskId'], s['code'], s['messageKey'], s['messageParams']) for s in found], [(b, 'unblocked', 'suggest_unblocked', {'title': 'Buy paint'})])
        self.board.accept_suggestion(found[0]['id'])
        self.assertEqual(next(t for t in self.board.snapshot()['tasks'] if t['id'] == b)['state'], 'active')
        current = next(s for s in self.board.snapshot()['suggestions'] if s['id'] == found[0]['id'])
        self.assertEqual((current['status'], current['current']), ('applied', False))
        self.assertEqual(self.board.accept_suggestion(found[0]['id'])['phase'], 'applied')
        last = self.board.snapshot()['lastFact']
        self.assertTrue(last['undoable']); self.board.undo(last['id'], 'undo-suggestion')
        self.assertEqual(next(t for t in self.board.snapshot()['tasks'] if t['id'] == b)['state'], 'todo')

    def test_snoozing_past_the_deadline_is_noted_with_an_earlier_time(self):
        self.board.bridge = ReviewFixture()
        uid = self.apply('task_add', {'title': 'File taxes', 'due': stamp(NOW + timedelta(days=3))})['result']['uuid']
        self.apply('task_snooze', {'uuid': uid, 'until': stamp(NOW + timedelta(days=4))})
        self.board.review_changes()
        found = [s for s in self.board.snapshot()['suggestions'] if s['current']]
        self.assertEqual([(s['code'], s['action']) for s in found], [('after_due', {'action': 'task_snooze', 'payload': {'uuid': uid, 'until': stamp(NOW + timedelta(days=2))}})])
        self.board.dismiss_suggestion(found[0]['id'])
        self.apply('task_update', {'uuid': uid, 'title': 'File the taxes'}); self.board.review_changes()
        self.assertEqual([s['status'] for s in self.board.snapshot()['suggestions']], ['dismissed'])

    def test_assistant_never_repeats_a_note_already_on_the_card(self):
        fixture = ReviewFixture(); self.board.bridge = fixture
        uid = self.apply('task_add', {'title': 'File taxes', 'due': stamp(NOW + timedelta(days=3))})['result']['uuid']
        other = self.add('Call the accountant')
        fixture.suggestions = [{'taskId': uid, 'message': 'It now comes back after its deadline.', 'action': None},
                               {'taskId': other, 'message': 'You could call before filing.', 'action': None}]
        self.apply('task_snooze', {'uuid': uid, 'until': stamp(NOW + timedelta(days=4))})
        self.apply('task_update', {'uuid': other, 'priority': 'high'})
        self.board.review_changes()
        self.assertEqual(sorted((s['taskId'] == uid, s['origin']) for s in self.board.snapshot()['suggestions']), [(False, 'assistant'), (True, 'rules')])
        self.apply('task_update', {'uuid': other, 'title': 'Call the accountant today'}); self.board.review_changes()
        self.assertEqual(len([s for s in self.board.snapshot()['suggestions'] if s['taskId'] == other]), 1)

    def test_suggestion_for_a_changed_task_cannot_be_accepted(self):
        self.board.bridge = ReviewFixture()
        a, b = self.add('A'), self.add('B')
        self.apply('dependency_update', {'uuid': b, 'dependsOn': [a]}); self.apply('task_done', {'uuid': a})
        self.board.review_changes()
        found = next(s for s in self.board.snapshot()['suggestions'] if s['current'])
        self.apply('task_snooze', {'uuid': b, 'until': stamp(NOW + timedelta(hours=2))})
        with self.assertRaises(AssistantError) as error: self.board.accept_suggestion(found['id'])
        self.assertEqual(error.exception.code, 'suggestion_stale')
        self.assertEqual(next(t for t in self.board.snapshot()['tasks'] if t['id'] == b)['state'], 'snoozed')

    def test_conversation_changes_undo_together_newest_first(self):
        a, b = self.add('A'), self.add('B')
        with self.app.store.connect() as con:
            con.execute("INSERT INTO messages(key,role,text,status,at) VALUES('several','user','Start A and B','pending',?)", (self.app.now(),))
        revision = self.app.store.revision()
        applied = self.board.request_many([('front_activate', {'uuid': a}), ('front_activate', {'uuid': b}), ('front_retire', {'uuid': a})], 'several', 'Start A and B', revision, 0)
        self.assertEqual([p['phase'] for p in applied], ['applied', 'applied', 'review'])
        self.assertTrue(self.app.tasks.get(a).get('start') and self.app.tasks.get(b).get('start'))
        self.assertEqual(self.app.tasks.get(a)['status'], 'pending')
        undone = self.board.undo_message('several', 'undo-several')
        self.assertEqual(len(undone), 2)
        self.assertFalse(self.app.tasks.get(a).get('start') or self.app.tasks.get(b).get('start'))
        with self.assertRaises(AssistantError) as error: self.board.undo_message('nothing', 'undo-nothing')
        self.assertEqual(error.exception.code, 'fact_not_found')

    def test_a_drop_undoes_its_move_priority_and_order_together(self):
        a, b, c = self.add('A'), self.add('B'), self.add('C')
        self.apply('order_update', {'order': [a, b, c]})
        before = self.state()
        # One drop: C goes to Active, takes High and lands first; each change applies on its own.
        drop = [self.request(action, payload) for action, payload in (
            ('front_activate', {'uuid': c}), ('task_update', {'uuid': c, 'priority': 'high'}), ('order_update', {'order': [c, a, b]}))]
        self.assertEqual([p['phase'] for p in drop], ['applied'] * 3)
        later = self.request('task_update', {'uuid': a, 'title': 'A renamed'})
        undone = self.board.undo_proposals([p['id'] for p in drop], 'undo-drop', self.app.store.revision())
        self.assertEqual([p['intent']['action'] for p in undone], ['order_update', 'task_restore', 'task_restore'])
        after = self.state()
        self.assertEqual(after['tasks'][a]['description'], 'A renamed')  # A later change to another task stays.
        after['tasks'][a] = before['tasks'][a]
        self.assertEqual(after, before)
        # A retry with the same key replays; a stale view, an empty or unknown list is refused.
        self.assertEqual(self.board.undo_proposals([p['id'] for p in drop], 'undo-drop', 0), undone)
        self.assertEqual(later['phase'], 'applied')
        for ids, code in (([], 'invalid_request'), (['x'] * 11, 'invalid_request'), (['00000000-0000-4000-8000-000000000000'], 'fact_not_found')):
            with self.subTest(ids=ids), self.assertRaises(AssistantError) as error: self.board.undo_proposals(ids, 'undo-' + str(len(ids)))
            self.assertEqual(error.exception.code, code)
        again = [self.request('front_activate', {'uuid': b})]
        with self.assertRaises(AssistantError) as error: self.board.undo_proposals([again[0]['id']], 'undo-stale', self.app.store.revision() - 1)
        self.assertEqual(error.exception.code, 'stale_revision')


class InstantHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='day-instant-http-')
        self.app = Assistant(self.temp.name); Connected(self.app).initialize()
        self.fixture = ReviewFixture()
        self.delay = patch.object(web, 'FOLLOWUP_DELAY', 0.4); self.delay.start()
        self.server = web.LocalServer(self.app, self.fixture)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        with self.app.store.connect() as con:
            self.ids = [json.loads(con.execute('SELECT result FROM operations WHERE key=?', (f'bootstrap-task-{i}',)).fetchone()[0])['uuid'] for i in range(4)]

    def tearDown(self):
        self.server.shutdown(); self.thread.join(5); self.server.server_close(); self.server.pool.shutdown(wait=True)
        self.delay.stop(); self.temp.cleanup()

    def post(self, path, body):
        req = Request(self.server.origin + path, data=json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json', 'Origin': self.server.origin, 'Authorization': 'Bearer ' + self.server.token})
        try:
            with urlopen(req, timeout=10) as response: return response.status, json.load(response)
        except HTTPError as error: return error.code, json.load(error)

    def state(self):
        req = Request(self.server.origin + '/api/state', headers={'Authorization': 'Bearer ' + self.server.token})
        with urlopen(req, timeout=10) as response: return json.load(response)['data']

    def wait(self, predicate):
        for _ in range(100):
            data = self.state()
            if predicate(data): return data
            time.sleep(.05)
        self.fail('Timed out waiting for the later review')

    def test_quick_changes_apply_at_once_and_share_one_later_review(self):
        for index, action in enumerate(('front_activate', 'front_todo', 'front_activate')):
            started = time.perf_counter()
            status, body = self.post('/api/proposals', {'action': action, 'payload': {'uuid': self.ids[2]}, 'key': f'quick-{index}'})
            self.assertEqual((status, body['data']['phase']), (200, 'applied'))
            self.assertLess(time.perf_counter() - started, 3.0)
        self.assertEqual(self.fixture.turns, 0)
        data = self.wait(lambda d: d['review']['pending'] == 0 and d['review']['status'] == 'completed')
        self.assertEqual(self.fixture.turns, 1)
        self.assertEqual(data['lastFact']['action'], 'front_activate')
        time.sleep(.6); self.assertEqual(self.fixture.turns, 1)

    def test_a_saved_change_reports_its_fact_in_the_reply(self):
        def latest(): return self.state()['lastFact']
        task = self.ids[2]
        status, body = self.post('/api/proposals', {'action': 'front_activate', 'payload': {'uuid': task}, 'key': 'reply-fact'})
        fact = body['data']['fact']
        self.assertEqual((status, fact), (200, latest()))
        self.assertEqual((fact['proposalId'], fact['taskId'], fact['undoable'], fact['undoOf']), (body['data']['id'], task, True, None))
        self.assertNotIn('plan', fact)
        replay = self.post('/api/proposals', {'action': 'front_activate', 'payload': {'uuid': task}, 'key': 'reply-fact'})[1]['data']
        self.assertEqual(replay['fact'], fact)
        undone = self.post('/api/undo', {'fact_id': fact['id'], 'key': 'reply-undo'})[1]['data']['fact']
        self.assertEqual((undone, undone['undoOf'], undone['undoable']), (latest(), fact['id'], True))
        unchanged = self.post('/api/proposals', {'action': 'front_todo', 'payload': {'uuid': task}, 'key': 'reply-same'})[1]['data']
        self.assertEqual(unchanged['phase'], 'unchanged')
        self.assertNotIn('fact', unchanged)
        # Changes undone together each report their fact, and a refused undo reports none.
        drop = [self.post('/api/proposals', {'action': action, 'payload': payload, 'key': 'reply-drop-' + str(i)})[1]['data']['id']
                for i, (action, payload) in enumerate((('front_activate', {'uuid': task}), ('task_update', {'uuid': task, 'priority': 'high'})))]
        together = self.post('/api/undo', {'proposal_ids': drop, 'key': 'reply-together'})[1]['data']
        self.assertEqual([p['fact']['id'] for p in together], sorted(p['fact']['id'] for p in together))
        self.assertEqual(together[-1]['fact'], latest())
        status, body = self.post('/api/undo', {'fact_id': fact['id'], 'key': 'reply-conflict'})
        self.assertEqual((status, body['error']['code']), (409, 'undo_conflict'))
        self.assertNotIn('data', body)

    def test_rejection_is_a_409_with_its_code(self):
        status, body = self.post('/api/proposals', {'action': 'dependency_update', 'payload': {'uuid': self.ids[1], 'dependsOn': [self.ids[1]]}, 'key': 'self-cycle'})
        self.assertEqual((status, body['error']['code']), (409, 'dependency_cycle'))

    def test_suggestion_and_message_undo_routes(self):
        self.post('/api/proposals', {'action': 'dependency_update', 'payload': {'uuid': self.ids[2], 'dependsOn': [self.ids[1]]}, 'key': 'link'})
        self.post('/api/proposals', {'action': 'task_done', 'payload': {'uuid': self.ids[1]}, 'key': 'finish'})
        data = self.wait(lambda d: any(s['current'] for s in d['suggestions']))
        suggestion = next(s for s in data['suggestions'] if s['current'])
        self.assertEqual((suggestion['taskId'], suggestion['code']), (self.ids[2], 'unblocked'))
        status, body = self.post('/api/suggestions/' + suggestion['id'] + '/accept', {})
        self.assertEqual((status, body['data']['phase']), (200, 'applied'))
        self.assertEqual(body['data']['fact'], self.state()['lastFact'])
        status, body = self.post('/api/suggestions/' + suggestion['id'] + '/dismiss', {})
        self.assertEqual((status, body['data']['status']), (200, 'applied'))
        self.assertEqual(self.post('/api/suggestions/' + suggestion['id'] + '/accept', {'extra': 1})[0], 400)
        status, body = self.post('/api/undo', {'message_key': 'unknown', 'key': 'undo-unknown'})
        self.assertEqual((status, body['error']['code']), (409, 'fact_not_found'))

    def test_drop_undo_route_reverts_every_change_of_the_gesture(self):
        drop = [self.post('/api/proposals', {'action': action, 'payload': payload, 'key': 'drop-' + str(i)})[1]['data']['id']
                for i, (action, payload) in enumerate((('front_activate', {'uuid': self.ids[3]}), ('task_update', {'uuid': self.ids[3], 'priority': 'high'})))]
        status, body = self.post('/api/undo', {'proposal_ids': drop, 'key': 'undo-drop', 'expected_revision': self.state()['revision']})
        self.assertEqual((status, [p['phase'] for p in body['data']]), (200, ['applied', 'applied']))
        task = next(t for t in self.state()['tasks'] if t['id'] == self.ids[3])
        self.assertEqual((task['state'], task['priority']), ('todo', 'low'))
        self.assertEqual(self.post('/api/undo', {'proposal_ids': drop, 'key': 'undo-drop-2', 'fact_id': 1})[0], 400)


if __name__ == '__main__':
    unittest.main()
