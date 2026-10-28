"""Real SQLite task-store oracles; controlled reviews test transport/state, not LLM quality."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from day_attendant.connected import Connected
from day_attendant.errors import AssistantError
from day_attendant.service import Assistant
from day_attendant.timeutil import stamp

ROOT=Path(__file__).resolve().parent.parent
NOW=datetime(2026,10,28,12,tzinfo=timezone.utc)


class ReviewFixture:
    model='controlled-fixture'
    available=True

    def thread(self,previous=None):return 'controlled-thread'

    def turn(self,thread,prompt,schema,**kwargs):
        supplied=json.loads(prompt[prompt.index('{'):])
        concerns=[{k:c[k] for k in ('code','message','refs')} for c in supplied['known_concerns']]
        return {'output':{'verdict':'review' if concerns else 'allow','message':'Controlled review response.',
                         'concerns':concerns,'uncertainties':[],'alternatives':[]},
                'model':self.model,'thread_id':thread,'turn_id':'controlled-turn','seconds':0,'status':'completed'}


class ConnectedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='day-connected-test-')
        self.app=Assistant(self.temp.name,clock=lambda:NOW)
        self.fixture=ReviewFixture()
        self.board=Connected(self.app,self.fixture)
        self.board.initialize()
        self.counter=0
        with self.app.store.connect() as con:
            self.ids=[json.loads(con.execute('SELECT result FROM operations WHERE key=?',(f'bootstrap-task-{i}',)).fetchone()[0])['uuid'] for i in range(4)]

    def tearDown(self):self.temp.cleanup()

    def request(self,action,payload):
        self.counter+=1
        return self.board.request(action,payload,f'input-{self.counter}')

    def apply(self,action,payload):
        proposal=self.request(action,payload)
        if proposal['phase']=='evaluating':self.board.evaluate(proposal['id'])
        final=self.board.proposal(proposal['id'])
        self.assertIn(final['phase'],('applied','unchanged'))
        return final

    def capacity(self):
        end=stamp(NOW+timedelta(minutes=45))
        for uid in self.ids[:2]:self.apply('task_update',{'uuid':uid,'due':end})
        self.apply('context_update',{'availableMinutes':45,'availableUntil':end,'rest':False})
        proposal=self.request('front_activate',{'uuid':self.ids[1]})
        self.board.evaluate(proposal['id'])
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'review')
        return self.board.proposal(proposal['id'])

    def test_multiple_fronts_use_native_start_and_persist(self):
        for uid in self.ids[1:]:self.apply('front_activate',{'uuid':uid})
        native=self.app.tasks.rows()
        self.assertEqual(sum(bool(r.get('start')) for r in native),4)
        restarted=Connected(Assistant(self.temp.name,clock=lambda:NOW),self.fixture)
        restarted.startup()
        self.assertEqual(sum(t['state']=='active' for t in restarted.snapshot()['tasks']),4)
        with self.app.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM frames').fetchone()[0],0)

    def test_pending_is_not_recoverable_and_restart_is_incomplete(self):
        proposal=self.request('front_activate',{'uuid':self.ids[1]})
        self.app.recover()
        self.assertFalse(self.app.tasks.get(self.ids[1]).get('start'))
        with self.app.store.connect() as con:self.assertEqual(con.execute("SELECT count(*) FROM operations WHERE key LIKE 'proposal:%'").fetchone()[0],0)
        self.board.startup()
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'unavailable')
        with self.assertRaises(AssistantError):self.board.confirm(proposal['id'],0,without_review=True)

    def test_new_valid_noop_supersedes_incompatible_pending(self):
        old=self.request('front_activate',{'uuid':self.ids[1]})
        new=self.request('front_todo',{'uuid':self.ids[1]})
        self.assertEqual(new['phase'],'unchanged')
        self.assertIn(old['id'],new['result']['superseded'])
        self.board.evaluate(old['id'])
        self.assertFalse(self.app.tasks.get(self.ids[1]).get('start'))

    def test_invalid_request_does_not_cancel_valid_pending(self):
        old=self.request('front_activate',{'uuid':self.ids[1]})
        with self.assertRaises(AssistantError):self.request('task_snooze',{'uuid':self.ids[1],'until':stamp(NOW-timedelta(minutes=1))})
        self.assertEqual(self.board.proposal(old['id'])['phase'],'evaluating')

    def test_cancel_ignores_late_success(self):
        started=threading.Event();release=threading.Event();original=self.fixture.turn
        def delayed(*args,**kwargs):started.set();release.wait(10);return original(*args,**kwargs)
        self.fixture.turn=delayed
        proposal=self.request('front_activate',{'uuid':self.ids[1]})
        worker=threading.Thread(target=self.board.evaluate,args=(proposal['id'],));worker.start()
        self.assertTrue(started.wait(5));self.board.cancel(proposal['id']);release.set();worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'canceled')
        self.assertFalse(self.app.tasks.get(self.ids[1]).get('start'))

    def test_old_failed_attempt_does_not_poison_new_generation(self):
        entered=[threading.Event(),threading.Event()];released=[threading.Event(),threading.Event()]
        original=self.fixture.turn;counter=[0];counter_lock=threading.Lock()
        def controlled(*args,**kwargs):
            with counter_lock:index=counter[0];counter[0]+=1
            entered[index].set();released[index].wait(10)
            if index==0:raise AssistantError('controlled_failure','Old attempt failed.')
            return original(*args,**kwargs)
        self.fixture.turn=controlled
        proposal=self.request('front_activate',{'uuid':self.ids[1]})
        first=threading.Thread(target=self.board.evaluate,args=(proposal['id'],));first.start();self.assertTrue(entered[0].wait(5))
        self.board.startup();self.board.retry(proposal['id'])
        second=threading.Thread(target=self.board.evaluate,args=(proposal['id'],));second.start();self.assertTrue(entered[1].wait(5))
        released[0].set();first.join(10)
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'evaluating')
        released[1].set();second.join(10)
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'applied')

    def test_acknowledged_capacity_waiting_and_new_work(self):
        proposal=self.capacity();self.board.confirm(proposal['id'],proposal['generation'])
        self.apply('front_activate',{'uuid':self.ids[3]})
        self.apply('front_todo',{'uuid':self.ids[1]})
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':50})
        newer=self.request('front_activate',{'uuid':self.ids[1]});self.board.evaluate(newer['id'])
        final=self.board.proposal(newer['id']);self.assertEqual(final['phase'],'review')
        self.assertTrue(any(c['code']=='capacity' and '90' in c['message'] for c in final['facts']))
        self.assertFalse(self.app.tasks.get(self.ids[1]).get('start'))

    def test_unrelated_fact_does_not_invalidate_pending_capacity(self):
        proposal=self.capacity()
        self.apply('task_done',{'uuid':self.ids[2]})
        final=self.board.proposal(proposal['id'])
        self.assertEqual(final['phase'],'review');self.assertEqual(final['generation'],proposal['generation'])

    def test_unrelated_waiting_activation_does_not_invalidate_pending_capacity(self):
        proposal=self.capacity()
        self.apply('front_activate',{'uuid':self.ids[3]})
        final=self.board.proposal(proposal['id'])
        self.assertEqual(final['phase'],'review');self.assertEqual(final['generation'],proposal['generation'])

    def test_context_change_invalidates_review_and_old_confirmation(self):
        proposal=self.capacity()
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':5})
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'stale')
        with self.assertRaises(AssistantError):self.board.confirm(proposal['id'],proposal['generation'])
        self.board.retry(proposal['id']);self.board.evaluate(proposal['id'])
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'applied')

    def test_unknown_model_fields_are_incomplete_not_authorized(self):
        original=self.fixture.turn
        def invalid(*args,**kwargs):
            result=original(*args,**kwargs);result['output']['execute']='delete everything';return result
        self.fixture.turn=invalid
        proposal=self.request('front_activate',{'uuid':self.ids[1]});self.board.evaluate(proposal['id'])
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'unavailable')
        self.assertFalse(self.app.tasks.get(self.ids[1]).get('start'))

    def test_permissive_model_cannot_erase_known_conflict(self):
        end=stamp(NOW+timedelta(minutes=45))
        for uid in self.ids[:2]:self.apply('task_update',{'uuid':uid,'due':end})
        self.apply('context_update',{'availableMinutes':45,'availableUntil':end,'rest':False})
        original=self.fixture.turn
        def permissive(*args,**kwargs):
            result=original(*args,**kwargs);result['output'].update(verdict='allow',concerns=[]);return result
        self.fixture.turn=permissive
        proposal=self.request('front_activate',{'uuid':self.ids[1]});self.board.evaluate(proposal['id'])
        final=self.board.proposal(proposal['id'])
        self.assertEqual(final['phase'],'unavailable');self.assertTrue(final['facts'])
        self.assertFalse(self.app.tasks.get(self.ids[1]).get('start'))

    def test_without_model_requires_explicit_versioned_continuation(self):
        self.board.bridge=None
        proposal=self.request('front_activate',{'uuid':self.ids[1]});self.board.evaluate(proposal['id'])
        with self.assertRaises(AssistantError):self.board.confirm(proposal['id'],0)
        self.assertFalse(self.app.tasks.get(self.ids[1]).get('start'))
        self.board.confirm(proposal['id'],0,without_review=True)
        self.assertTrue(self.app.tasks.get(self.ids[1]).get('start'))
        with self.app.store.connect() as con:self.assertEqual(con.execute('SELECT review_complete FROM decisions').fetchone()[0],0)

    def test_retry_key_is_idempotent_and_legacy_write_is_blocked(self):
        proposal=self.apply('front_activate',{'uuid':self.ids[1]})
        replay=self.board.request('front_activate',{'uuid':self.ids[1]},proposal['key'])
        self.assertEqual(replay['result'],proposal['result'])
        with self.app.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM facts').fetchone()[0],1)
        with self.assertRaises(AssistantError) as error:self.app.mutate('task_done',{'uuid':self.ids[0]},'legacy-bypass')
        self.assertEqual(error.exception.code,'proposal_required')

    def test_undo_is_limited_and_does_not_overwrite_new_changes(self):
        self.apply('front_activate',{'uuid':self.ids[1]})
        fact=self.board.snapshot()['events'][-1]['id']
        self.apply('task_update',{'uuid':self.ids[1],'title':'Changed by a later explicit request'})
        with self.assertRaises(AssistantError) as error:self.board.undo(fact,'undo-old')
        self.assertEqual(error.exception.code,'undo_conflict')
        self.assertEqual(self.app.tasks.get(self.ids[1])['description'],'Changed by a later explicit request')

    def test_retire_is_not_completion_and_can_be_undone(self):
        self.apply('front_retire',{'uuid':self.ids[1]})
        self.assertEqual(self.app.tasks.get(self.ids[1])['status'],'deleted')
        fact=self.board.snapshot()['events'][-1]['id'];proposal=self.board.undo(fact,'undo-retire');self.board.evaluate(proposal['id'])
        self.assertEqual(self.app.tasks.get(self.ids[1])['status'],'pending')
        self.assertEqual(json.loads(self.board.snapshot()['events'][-1]['plan'])['undo_of'],fact)

    def test_undo_cannot_activate_a_now_blocked_dependency(self):
        self.app.tasks.patch(self.ids[1],{'status':'completed'})
        self.app.tasks.patch(self.ids[0],{'depends':self.ids[1]})
        self.apply('task_done',{'uuid':self.ids[0]})
        fact=self.board.snapshot()['events'][-1]['id']
        self.app.tasks.patch(self.ids[1],{'status':'pending','end':None})
        with self.assertRaises(AssistantError) as error:self.board.undo(fact,'undo-with-new-dependency')
        self.assertEqual(error.exception.code,'not_actionable')
        self.assertEqual(self.app.tasks.get(self.ids[0])['status'],'completed')

    def test_process_death_after_authorized_admission_recovers_once(self):
        self.board.bridge=None
        proposal=self.request('front_activate',{'uuid':self.ids[1]});self.board.evaluate(proposal['id'])
        script='''
from datetime import datetime,timezone
import os,sys
from day_attendant.service import Assistant
from day_attendant.connected import Connected
def fault(point):
    if point==sys.argv[3]:os._exit(75)
app=Assistant(sys.argv[1],clock=lambda:datetime(2026,10,28,12,tzinfo=timezone.utc),fault=fault)
Connected(app).confirm(sys.argv[2],0,without_review=True)
'''
        result=subprocess.run([sys.executable,'-B','-c',script,self.temp.name,proposal['id'],'after_admission'],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,75,result.stderr)
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'admitted')
        self.assertFalse(self.app.tasks.get(self.ids[1]).get('start'))
        restarted=Connected(Assistant(self.temp.name,clock=lambda:NOW));restarted.startup();restarted.startup()
        self.assertTrue(restarted.app.tasks.get(self.ids[1]).get('start'))
        with self.app.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM facts').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT count(*) FROM decisions').fetchone()[0],1)

    def test_process_death_after_native_effect_recovers_once(self):
        self.board.bridge=None
        proposal=self.request('front_activate',{'uuid':self.ids[1]});self.board.evaluate(proposal['id'])
        script='''
from datetime import datetime,timezone
import os,sys
from day_attendant.service import Assistant
from day_attendant.connected import Connected
def fault(point):
    if point==sys.argv[3]:os._exit(75)
app=Assistant(sys.argv[1],clock=lambda:datetime(2026,10,28,12,tzinfo=timezone.utc),fault=fault)
Connected(app).confirm(sys.argv[2],0,without_review=True)
'''
        result=subprocess.run([sys.executable,'-B','-c',script,self.temp.name,proposal['id'],'after_task_0'],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,75,result.stderr)
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'admitted')
        self.assertTrue(self.app.tasks.get(self.ids[1]).get('start'))
        restarted=Connected(Assistant(self.temp.name,clock=lambda:NOW));restarted.startup();restarted.startup()
        self.assertTrue(restarted.app.tasks.get(self.ids[1]).get('start'))
        with self.app.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM facts').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT count(*) FROM decisions').fetchone()[0],1)

    def test_new_capacity_cause_is_not_silenced_by_acknowledged_category(self):
        accepted=self.capacity();self.board.confirm(accepted['id'],accepted['generation'])
        self.apply('task_update',{'uuid':self.ids[2],'due':stamp(NOW+timedelta(hours=2))})
        self.apply('continuity_update',{'uuid':self.ids[2],'remainingMinutes':40})
        original=self.fixture.turn
        def another_cause(*args,**kwargs):
            result=original(*args,**kwargs)
            result['output'].update(verdict='review',concerns=[{'code':'capacity','message':'A different completion window.',
                'refs':['proposal.until','tasks.'+self.ids[2]+'.due','tasks.'+self.ids[2]+'.remainingMinutes']}])
            return result
        self.fixture.turn=another_cause
        p=self.request('task_snooze',{'uuid':self.ids[2],'until':stamp(NOW+timedelta(minutes=90))})
        self.board.evaluate(p['id']);p=self.board.proposal(p['id'])
        self.assertEqual(p['phase'],'review');self.assertTrue(p['facts'])
        self.assertFalse(self.app.tasks.get(self.ids[2]).get('wait'))

    def test_identical_contextual_consequence_is_not_repeated(self):
        original=self.fixture.turn
        def same_consequence(*args,**kwargs):
            result=original(*args,**kwargs)
            result['output'].update(verdict='review',concerns=[{'code':'contextual','message':'Controlled accepted consequence.','refs':['context.rest']}])
            return result
        self.fixture.turn=same_consequence
        p=self.request('front_activate',{'uuid':self.ids[3]});self.board.evaluate(p['id'])
        p=self.board.proposal(p['id']);self.board.confirm(p['id'],p['generation'])
        self.apply('front_todo',{'uuid':self.ids[3]})

    def test_delayed_model_result_cannot_apply_after_temporal_change(self):
        end=stamp(NOW+timedelta(minutes=45))
        self.apply('task_update',{'uuid':self.ids[1],'due':end})
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':45})
        self.apply('front_todo',{'uuid':self.ids[0]})
        self.apply('context_update',{'availableMinutes':45,'availableUntil':end})
        original=self.fixture.turn
        def delayed(*args,**kwargs):
            result=original(*args,**kwargs);self.app.clock=lambda:NOW+timedelta(minutes=35);return result
        self.fixture.turn=delayed
        p=self.request('front_activate',{'uuid':self.ids[1]});self.board.evaluate(p['id'])
        self.assertEqual(self.board.proposal(p['id'])['phase'],'stale')
        self.assertFalse(self.app.tasks.get(self.ids[1]).get('start'))

    def test_late_confirmation_expires_without_mutation(self):
        p=self.capacity();self.app.clock=lambda:NOW+timedelta(minutes=3)
        with self.assertRaises(AssistantError):self.board.confirm(p['id'],p['generation'])
        self.assertEqual(self.board.proposal(p['id'])['phase'],'stale')
        self.assertFalse(self.app.tasks.get(self.ids[1]).get('start'))

    def test_rest_can_be_corrected_after_availability_expires(self):
        self.apply('context_update',{'availableMinutes':45,'availableUntil':stamp(NOW+timedelta(hours=1))})
        self.app.clock=lambda:NOW+timedelta(days=1)
        self.apply('context_update',{'rest':True})
        self.assertTrue(self.board.snapshot()['context']['rest'])

    def test_review_freshness_follows_the_selected_level(self):
        clock=[NOW];self.app.clock=lambda:clock[0]
        fixture=self.fixture;review=fixture.turn
        def slow(thread,prompt,schema,**kwargs):
            clock[0]+=timedelta(seconds=fixture.seconds);return review(thread,prompt,schema,**kwargs)
        fixture.turn=slow
        for effort,limit in (('high',300),('xhigh',600),('max',900)):
            fixture.effort=effort
            for seconds,phase in ((limit-30,'applied'),(limit+30,'stale')):
                fixture.seconds=seconds
                proposal=self.request('task_update',{'uuid':self.ids[1],'title':'Reviewed at '+effort+' after '+str(seconds)+' s'})
                self.assertEqual(proposal['phase'],'evaluating')
                self.board.evaluate(proposal['id'])
                self.assertEqual(self.board.proposal(proposal['id'])['phase'],phase,(effort,seconds))

    def test_clear_waits_for_pending_contextual_guidance(self):
        from day_attendant.conversation import Conversation
        end=stamp(NOW+timedelta(hours=1))
        for uid in self.ids[:2]:self.apply('task_update',{'uuid':uid,'due':end})
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':5})
        self.apply('context_update',{'availableMinutes':45,'availableUntil':end})
        self.apply('front_activate',{'uuid':self.ids[1]})
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':40})
        chat=Conversation(self.board)
        with self.assertRaises(AssistantError) as error:chat.clear('during-guidance')
        self.assertEqual(error.exception.code,'chat_busy')
        self.board.evaluate_notices()
        self.assertEqual(chat.clear('after-guidance')['status'],'cleared')

    def test_corrected_effort_creates_deduplicated_informational_guidance(self):
        end=stamp(NOW+timedelta(hours=1))
        for uid in self.ids[:2]:self.apply('task_update',{'uuid':uid,'due':end})
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':5})
        self.apply('context_update',{'availableMinutes':45,'availableUntil':end})
        self.apply('front_activate',{'uuid':self.ids[1]})
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':40})
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':40})
        before=self.board.snapshot()
        self.assertEqual(sum(m['key'].startswith('context-notice:') for m in before['messages']),1)
        self.board.evaluate_notices();after=self.board.snapshot()
        self.assertEqual(after['messages'][-1]['status'],'completed')
        self.assertEqual([t['state'] for t in before['tasks']],[t['state'] for t in after['tasks']])
        with self.app.store.connect() as con:
            plan=json.loads(con.execute('SELECT plan FROM facts ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(plan['review_status'],'not_required');self.assertFalse(plan['review_complete'])


if __name__=='__main__':unittest.main()
