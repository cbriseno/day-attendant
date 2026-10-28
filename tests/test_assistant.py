"""Real Taskwarrior integration and independently observed failure boundaries."""
from datetime import datetime, timedelta, timezone
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from day_attendant.errors import AssistantError
from day_attendant.service import Assistant
from day_attendant.store import Store
from day_attendant.timeutil import instant, parse

ROOT = Path(__file__).resolve().parent.parent
TASK = os.environ.get("DAY_ATTENDANT_TASK_BIN", str(ROOT / ".runtime/taskwarrior/bin/task"))
NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class TimeTests(unittest.TestCase):
    def test_ambiguous_hour_requires_choice(self):
        with self.assertRaises(AssistantError) as caught:
            instant("2026-11-01T01:30:00", "America/Edmonton")
        self.assertEqual(caught.exception.code, "ambiguous_time")
        self.assertEqual(instant("2026-11-01T01:30:00", "America/Edmonton", 0), "2026-11-01T07:30:00Z")
        self.assertEqual(instant("2026-11-01T01:30:00", "America/Edmonton", 1), "2026-11-01T08:30:00Z")

    def test_nonexistent_hour_rejected(self):
        with self.assertRaises(AssistantError) as caught:
            instant("2026-03-08T02:30:00", "America/Edmonton")
        self.assertEqual(caught.exception.code, "nonexistent_time")

    def test_naive_time_requires_zone(self):
        with self.assertRaises(AssistantError):
            instant("2026-10-28T12:00")
        self.assertEqual(instant("2026-10-28T06:00-06:00"), "2026-10-28T12:00:00Z")

    def test_checkout_state_rejected(self):
        with self.assertRaises(AssistantError) as caught:
            Store(ROOT / "should-not-be-created")
        self.assertEqual(caught.exception.code, "unsafe_state_path")
        self.assertFalse((ROOT / "should-not-be-created").exists())


@unittest.skipUnless(Path(TASK).is_file(), "Set DAY_ATTENDANT_TASK_BIN to a Taskwarrior 3.5.0 binary")
class Integration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="asistente-product-test-")
        self.clock = Clock()
        self.deliveries = []
        self.app = Assistant(self.temp.name, TASK, self.clock, self.accept)
        self.counter = 0

    def tearDown(self):
        self.temp.cleanup()

    def accept(self, title, body):
        self.deliveries.append((title, body))
        return {"accepted": True}

    def key(self):
        self.counter += 1
        return f"test-{self.counter}"

    def add(self, title="[FICTICIO] task", **kwargs):
        return self.app.mutate("task_add", dict(title=title, priority="medium", **kwargs), self.key())["uuid"]

    def push(self, uid):
        return self.app.mutate("focus_push", {"uuid": uid}, self.key(), self.app.store.revision())

    def resume(self, skip=False):
        return self.app.mutate("focus_resume", {"skip_invalid": skip}, self.key(), self.app.store.revision())

    def worker(self, action, payload, key, point):
        code = '''
import json,os,sys
from datetime import datetime,timezone
from day_attendant.service import Assistant
def fail(point):
    if point==sys.argv[6]:os._exit(75)
app=Assistant(sys.argv[1],sys.argv[2],clock=lambda:datetime(2026,10,28,12,tzinfo=timezone.utc),fault=fail)
app.mutate(sys.argv[3],json.loads(sys.argv[4]),sys.argv[5],app.store.revision() if sys.argv[3].startswith('focus_') else None)
'''
        return subprocess.run([sys.executable, "-B", "-c", code, self.temp.name, TASK,
                               action, json.dumps(payload), key, point], cwd=ROOT,
                              capture_output=True, text=True, timeout=30)

    def test_creation_idempotent_and_key_conflict(self):
        payload = {"title": "[FICTICIO] capture", "priority": "high"}
        one = self.app.mutate("task_add", payload, "same")
        self.app.mutate("task_update", {"uuid": one["uuid"], "title": "[FICTICIO] later edit"}, self.key())
        two = self.app.mutate("task_add", payload, "same")
        self.assertEqual(one, two)
        self.assertEqual(len(self.app.tasks.rows()), 1)
        self.assertEqual(self.app.tasks.get(one["uuid"])["description"], "[FICTICIO] later edit")
        with self.assertRaises(AssistantError) as caught:
            self.app.mutate("task_add", dict(payload, title="different"), "same")
        self.assertEqual(caught.exception.code, "idempotency_conflict")

    def test_create_recovery_after_real_process_death(self):
        for point in ("after_intent", "after_task_0", "before_commit", "inside_commit", "after_commit"):
            with self.subTest(point=point):
                key = self.key()
                payload = {"title": "[FICTICIO] " + point, "priority": "medium"}
                run = self.worker("task_add", payload, key, point)
                self.assertEqual(run.returncode, 75, run.stderr)
                result = self.app.recover()
                self.assertEqual(result["unresolved"], [])
                repeated = self.app.mutate("task_add", payload, key)
                self.assertEqual(len([r for r in self.app.tasks.rows() if r["uuid"] == repeated["uuid"]]), 1)
                with self.app.store.connect() as con:
                    self.assertEqual(con.execute("SELECT count(*) FROM task_meta WHERE uuid=?", (repeated["uuid"],)).fetchone()[0], 1)

    def test_recovery_refuses_external_edit_after_timeout(self):
        run = self.worker("task_add", {"title": "[FICTICIO] original"}, "conflict", "after_task_0")
        self.assertEqual(run.returncode, 75)
        row = self.app.tasks.rows()[0]
        self.app.tasks.patch(row["uuid"], {"description": "[FICTICIO] external change"})
        result = self.app.recover()
        self.assertEqual(result["unresolved"][0]["status"], "conflict")
        self.assertEqual(self.app.tasks.get(row["uuid"])["description"], "[FICTICIO] external change")
        self.assertEqual(self.app.recover(), result)
        with self.assertRaises(AssistantError):
            self.add()

    def test_later_recovery_conflict_does_not_mutate_earlier_participant(self):
        a, b = self.add("[FICTICIO] A"), self.add("[FICTICIO] B")
        self.push(a)
        before = dict(self.app.tasks.get(a))
        run = self.worker("focus_push", {"uuid": b}, "later-conflict", "after_intent")
        self.assertEqual(run.returncode, 75, run.stderr)
        self.app.tasks.patch(b, {"description": "[FICTICIO] external B"})
        result = self.app.recover()
        self.assertEqual(result["unresolved"][0]["status"], "conflict")
        self.assertEqual(self.app.tasks.get(a), before)
        self.assertEqual(self.app.tasks.get(b)["description"], "[FICTICIO] external B")
        self.assertEqual(self.app.recover(), result)

    def test_nested_focus_revisions_and_stale_writer(self):
        a, b, c = self.add("[FICTICIO] A"), self.add("[FICTICIO] B"), self.add("[FICTICIO] C")
        self.push(a)
        old = self.app.store.revision()
        self.push(b)
        with self.assertRaises(AssistantError) as caught:
            self.app.mutate("focus_push", {"uuid": c}, self.key(), old)
        self.assertEqual(caught.exception.code, "stale_revision")
        self.push(c)
        self.assertEqual(self.resume()["focus"], b)
        self.assertEqual(self.resume()["focus"], a)
        self.assertTrue(self.app.tasks.get(a).get("start"))
        self.assertFalse(self.app.tasks.get(b).get("start"))
        self.assertFalse(self.app.tasks.get(c).get("start"))

    def test_focus_recovery_at_all_boundaries(self):
        a, b = self.add("[FICTICIO] A"), self.add("[FICTICIO] B")
        self.push(a)
        for point in ("after_intent", "after_task_0", "after_task_1", "before_commit", "inside_commit", "after_commit"):
            with self.subTest(point=point):
                run = self.worker("focus_push", {"uuid": b}, self.key(), point)
                self.assertEqual(run.returncode, 75, run.stderr)
                self.assertEqual(self.app.recover()["unresolved"], [])
                self.assertEqual(self.app.brief()["next"]["uuid"], b)
                self.assertEqual(len(self.app.store.frames()), 2)
                self.assertEqual(self.resume()["focus"], a)

    def test_completed_previous_frame_needs_explicit_skip(self):
        a, b = self.add("[FICTICIO] A"), self.add("[FICTICIO] B")
        self.push(a); self.push(b)
        self.app.mutate("task_done", {"uuid": a}, self.key())
        with self.assertRaises(AssistantError) as caught:
            self.resume()
        self.assertEqual(caught.exception.code, "invalid_frame")
        self.assertEqual(len(self.app.store.frames()), 2)
        self.assertIsNone(self.resume(True)["focus"])
        self.assertFalse(self.app.tasks.get(a).get("start"))

    def test_snoozed_previous_is_not_resumed(self):
        a, b = self.add("[FICTICIO] A"), self.add("[FICTICIO] B")
        self.push(a); self.push(b)
        self.app.mutate("task_snooze", {"uuid": a, "until": "2030-01-01T12:00Z"}, self.key())
        with self.assertRaises(AssistantError) as caught:
            self.resume()
        self.assertEqual(caught.exception.code, "invalid_frame")
        self.assertTrue(self.app.tasks.get(b).get("start"))
        self.assertEqual(len(self.app.store.frames()), 2)

    def test_resume_recovery_at_all_boundaries(self):
        a, b = self.add("[FICTICIO] A"), self.add("[FICTICIO] B")
        self.push(a)
        for point in ("after_intent", "after_task_0", "after_task_1", "before_commit", "inside_commit", "after_commit"):
            with self.subTest(point=point):
                self.push(b)
                run = self.worker("focus_resume", {"skip_invalid": False}, self.key(), point)
                self.assertEqual(run.returncode, 75, run.stderr)
                self.assertEqual(self.app.recover()["unresolved"], [])
                self.assertEqual(self.app.brief()["next"]["uuid"], a)
                self.assertEqual(len(self.app.store.frames()), 1)

    def test_snooze_expiry_is_not_human_progress(self):
        uid = self.add()
        before = self.app.store.metadata()[uid]["human_progress_at"]
        self.app.mutate("task_snooze", {"uuid": uid, "until": "2026-10-28T12:15Z"}, self.key())
        self.assertEqual(self.app.list()["tasks"][0]["status"], "waiting")
        self.assertIsNone(self.app.brief()["next"])
        self.clock.advance(minutes=16)
        notices = self.app.pulse()["notices"]
        self.assertEqual([n["kind"] for n in notices], ["snooze_ready"])
        self.assertEqual(self.app.store.metadata()[uid]["human_progress_at"], before)
        self.push(uid)
        self.assertNotIn("wait", self.app.tasks.get(uid))
        self.assertEqual(self.app.pulse()["notices"], [])

    def test_pulse_does_not_consume_notification_and_repeat_is_suppressed(self):
        self.add(due="2026-10-28T12:30Z")
        first = self.app.pulse()
        self.assertEqual(first, self.app.pulse())
        with self.app.store.connect() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM notices").fetchone()[0], 0)
        result = self.app.notify()
        self.assertEqual(result["attempts"][0]["status"], "accepted")
        self.assertEqual(result["attempts"][0]["read_by_user"], "unknown")
        self.assertEqual(self.app.notify()["attempts"], [])
        self.app.pulse()
        self.assertEqual(len(self.deliveries), 1)
        self.clock.advance(minutes=31)
        self.app.notify()
        self.assertEqual(len(self.deliveries), 2)

    def test_failed_notice_retries_without_claiming_acceptance(self):
        self.add(due="2026-10-28T12:30Z")
        self.app.notifier = lambda *args: {"accepted": False, "error": "notifier unavailable"}
        self.assertEqual(self.app.notify()["attempts"][0]["status"], "pending")
        self.assertEqual(self.app.notify()["attempts"], [])
        with self.app.store.connect() as con:
            self.assertIsNone(con.execute("SELECT accepted_at FROM notices").fetchone()[0])
        self.clock.advance(minutes=3)
        self.app.notifier = self.accept
        self.assertEqual(self.app.notify()["attempts"][0]["status"], "accepted")

    def test_notification_retry_bound(self):
        self.add(due="2026-10-28T12:30Z")
        self.app.notifier = lambda *args: {"accepted": False}
        for minutes in (0, 3, 5):
            self.clock.advance(minutes=minutes)
            self.app.notify()
        with self.app.store.connect() as con:
            row = con.execute("SELECT status,attempts FROM notices").fetchone()
            self.assertEqual(tuple(row), ("failed", 3))
        self.clock.advance(days=1)
        self.assertEqual(self.app.notify()["attempts"], [])

    def test_notifier_runs_without_writer_lock_or_write_transaction(self):
        self.add(due="2026-10-28T12:30Z")
        def callback(*args):
            # Separate process has to acquire the same coordinator lock. A
            # SQLite writer must also succeed during the external invocation.
            code = '''
import sqlite3,sys
from pathlib import Path
from day_attendant.platforms import exclusive
with exclusive(Path(sys.argv[1])/'writer.lock',timeout=.5):
 con=sqlite3.connect(Path(sys.argv[1])/'assistant.sqlite3',timeout=.5)
 con.execute('BEGIN IMMEDIATE');con.rollback();con.close()
'''
            run = subprocess.run([sys.executable, "-B", "-c", code, self.temp.name], cwd=ROOT, capture_output=True, text=True, timeout=3)
            self.assertEqual(run.returncode, 0, run.stderr)
            return {"accepted": True}
        self.app.notifier = callback
        self.app.notify()

    def test_interrupted_notifier_stays_uncertain_until_explicit_retry(self):
        self.add(due="2026-10-28T12:30Z")
        code = '''
from datetime import datetime,timezone
from day_attendant.service import Assistant
import os,sys
app=Assistant(sys.argv[1],sys.argv[2],clock=lambda:datetime(2026,10,28,12,tzinfo=timezone.utc),notifier=lambda *a:{'accepted':True},fault=lambda p:os._exit(75) if p==sys.argv[3] else None)
app.notify()
'''
        run = subprocess.run([sys.executable, "-B", "-c", code, self.temp.name, TASK, "after_notify"], cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 75, run.stderr)
        self.clock.advance(minutes=3)
        self.assertEqual(self.app.notify()["attempts"], [])
        with self.app.store.connect() as con:
            self.assertEqual(con.execute("SELECT status FROM notices").fetchone()[0], "uncertain")
        self.assertEqual(self.app.notify(retry_uncertain=True)["attempts"][0]["status"], "accepted")

    def test_obsolete_notice_not_delivered(self):
        uid = self.add(due="2026-10-28T12:30Z")
        self.app.notifier = lambda *args: {"accepted": False}
        self.app.notify()
        self.app.mutate("task_done", {"uuid": uid}, self.key())
        self.clock.advance(minutes=3)
        self.app.notifier = self.accept
        self.app.notify()
        self.assertEqual(self.deliveries, [])
        with self.app.store.connect() as con:
            self.assertEqual(con.execute("SELECT status FROM notices").fetchone()[0], "obsolete")

    def test_notice_invalidated_after_claim_is_not_sent(self):
        uid = self.add(due="2026-10-28T12:30Z")
        def interleave(point):
            if point == "after_claim":
                self.app.mutate("task_done", {"uuid": uid}, "complete-after-claim")
        self.app.fault = interleave
        result = self.app.notify()
        self.assertEqual(result["attempts"][0]["status"], "obsolete")
        self.assertEqual(self.deliveries, [])
        with self.app.store.connect() as con:
            self.assertEqual(con.execute("SELECT status FROM attempts").fetchone()[0], "obsolete")
            self.assertIsNone(con.execute("SELECT accepted_at FROM notices").fetchone()[0])

    def test_doctor_reports_expired_claim_without_mutating_it(self):
        self.add(due="2026-10-28T12:30Z")
        code = '''
from datetime import datetime,timezone
from day_attendant.service import Assistant
import os,sys
a=Assistant(sys.argv[1],sys.argv[2],clock=lambda:datetime(2026,10,28,12,tzinfo=timezone.utc),fault=lambda p:os._exit(75) if p=='before_notify' else None)
a.notify()
'''
        run = subprocess.run([sys.executable, "-B", "-c", code, self.temp.name, TASK], cwd=ROOT,
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 75, run.stderr)
        active = self.app.doctor()["notices_requiring_attention"][0]
        self.assertFalse(active["claim_expired"])
        self.clock.advance(minutes=3)
        expired = self.app.doctor()["notices_requiring_attention"][0]
        self.assertTrue(expired["claim_expired"])
        self.assertEqual(expired["delivery_state"], "expired_unknown")
        self.assertIn("notify", expired["recommended_action"])
        with self.app.store.connect() as con:
            self.assertEqual(con.execute("SELECT status FROM notices").fetchone()[0], "sending")
        self.app.notify()
        with self.app.store.connect() as con:
            self.assertEqual(con.execute("SELECT status FROM notices").fetchone()[0], "uncertain")
        self.assertEqual(self.deliveries, [])

    def test_administrative_changes_do_not_imply_human_progress(self):
        uid = self.add()
        original = self.app.store.metadata()[uid]["human_progress_at"]
        self.clock.advance(minutes=1)
        self.app.mutate("task_update", {"uuid": uid, "priority": "high"}, self.key())
        self.app.mutate("task_snooze", {"uuid": uid, "until": "2026-10-28T12:15Z"}, self.key())
        self.clock.advance(minutes=15)
        self.push(uid)
        self.resume()
        self.assertEqual(self.app.store.metadata()[uid]["human_progress_at"], original)
        self.clock.advance(minutes=1)
        self.app.mutate("task_update", {"uuid": uid, "progress": True}, self.key())
        self.assertGreater(parse(self.app.store.metadata()[uid]["human_progress_at"]), parse(original))

    def test_real_dependencies_pending_completed_and_deleted(self):
        a, b = self.add("[FICTICIO] prerequisite"), self.add("[FICTICIO] dependent")
        # Native edits establish independent backend fixtures, not a supported
        # ordinary write route for agents operating the product.
        self.app.tasks.command(b, "modify", f"depends:{a}")
        self.assertEqual(next(t for t in self.app.list()["tasks"] if t["uuid"] == b)["status"], "blocked")
        self.assertEqual(self.app.brief()["next"]["uuid"], a)
        self.app.mutate("task_done", {"uuid": a}, self.key())
        self.assertEqual(self.app.brief()["next"]["uuid"], b)
        c = self.add("[FICTICIO] removed prerequisite")
        self.app.tasks.command(b, "modify", f"depends:{c}")
        self.assertEqual(next(t for t in self.app.list()["tasks"] if t["uuid"] == b)["status"], "blocked")
        self.app.tasks.command(c, "delete")
        self.assertEqual(self.app.brief()["next"]["uuid"], b)

    def test_private_files_are_not_group_or_world_readable(self):
        self.add()
        for path in Path(self.temp.name).rglob("*"):
            if path.is_file() or path.is_dir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0, str(path))

    def test_concurrent_notifiers_claim_one_attempt(self):
        self.add(due="2026-10-28T12:30Z")
        marker = Path(self.temp.name) / "deliveries.txt"
        code = '''
from day_attendant.service import Assistant
from datetime import datetime,timezone
import os,sys,time
def notify(*args):
 fd=os.open(sys.argv[3],os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
 os.write(fd,b'delivered\\n');os.close(fd);time.sleep(.2)
 return {'accepted':True}
a=Assistant(sys.argv[1],sys.argv[2],clock=lambda:datetime(2026,10,28,12,tzinfo=timezone.utc),notifier=notify)
a.notify()
'''
        processes = [subprocess.Popen([sys.executable, "-B", "-c", code, self.temp.name, TASK, str(marker)],
                                      cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for process in processes:
            out, err = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, out + err)
        self.assertEqual(marker.read_text().splitlines(), ["delivered"])
        with self.app.store.connect() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)

    def test_events_conflicts_and_cancel(self):
        self.add(estimate_minutes=60)
        event = self.app.mutate("event_add", {"title": "[FICTICIO] junta", "start": "2026-10-28T12:10Z", "end": "2026-10-28T12:40Z"}, self.key())
        self.assertEqual(len(self.app.brief()["conflicts"]), 1)
        self.assertEqual(self.app.pulse()["notices"][0]["kind"], "event")
        self.app.mutate("event_cancel", {"uuid": event["uuid"]}, self.key())
        self.assertEqual(self.app.brief()["conflicts"], [])

    def test_priority_is_explicit_and_focus_overrides_it(self):
        low = self.app.mutate("task_add", {"title": "[FICTICIO] low", "priority": "low"}, self.key())["uuid"]
        high = self.app.mutate("task_add", {"title": "[FICTICIO] high", "priority": "high"}, self.key())["uuid"]
        self.assertEqual(self.app.brief()["next"]["uuid"], high)
        self.push(low)
        self.assertEqual(self.app.brief()["next"]["uuid"], low)

    def test_cli_json_any_level_and_schedule_no_install(self):
        def run(*args):
            call = subprocess.run([sys.executable, "-B", str(ROOT / "day-attendant.py"), "--state", self.temp.name,
                                   "--task-bin", TASK, *args], capture_output=True, text=True, timeout=20)
            self.assertEqual(call.returncode, 0, call.stdout + call.stderr)
            return json.loads(call.stdout)
        created = run("task", "add", "[FICTICIO] CLI", "--key", "cli", "--json")
        self.assertEqual(created["schema_version"], 1)
        data = run("--json", "task", "list")["data"]
        self.assertEqual(data["tasks"][0]["uuid"], created["data"]["uuid"])
        planned = run("schedule", "--json", "--dry-run")["data"]
        self.assertTrue(planned["dry_run"])
        self.assertFalse(planned["installed"])

    def test_concurrent_clients_and_read_only_pulse(self):
        code = '''
from day_attendant.service import Assistant
import sys
a=Assistant(sys.argv[1],sys.argv[2]);a.mutate('task_add',{'title':'[FICTICIO] concurrent'},sys.argv[3])
'''
        children = [subprocess.Popen([sys.executable, "-B", "-c", code, self.temp.name, TASK, f"parallel-{i}"],
                                     cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(6)]
        for _ in range(3):
            self.app.pulse()
        for child in children:
            out, err = child.communicate(timeout=30)
            self.assertEqual(child.returncode, 0, out + err)
        self.assertEqual(len(self.app.list()["tasks"]), 6)
        self.assertEqual(self.app.recover()["unresolved"], [])


if __name__ == "__main__":
    unittest.main()
