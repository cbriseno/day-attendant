"""Real SQLite task-store oracles; controlled reasoning tests transport/state, not LLM quality."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from day_attendant.connected import Connected
from day_attendant.errors import AssistantError
from day_attendant.service import Assistant
from day_attendant.timeutil import stamp

ROOT=Path(__file__).resolve().parent.parent
NOW=datetime(2026,10,28,12,tzinfo=timezone.utc)


class ReviewFixture:
    """Answers the later review with no suggestions unless a test supplies some."""
    model='controlled-fixture'
    available=True

    def __init__(self):self.turns=0;self.suggestions=[]

    def thread(self,previous=None):return 'controlled-thread'

    def turn(self,thread,prompt,schema,**kwargs):
        self.turns=getattr(self,'turns',0)+1
        if 'suggestions' not in schema['properties']:raise AssertionError('Only the later review reaches this fixture.')
        return {'output':{'suggestions':list(getattr(self,'suggestions',[]))},'model':self.model,'thread_id':thread,
                'turn_id':'controlled-turn','seconds':0,'status':'completed'}


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
        final=self.request(action,payload)
        self.assertIn(final['phase'],('applied','unchanged'))
        return final

    def waiting_retire(self,uid,key='chat-retire'):
        """A retire interpreted from the conversation waits for the person; nothing else does."""
        proposal=self.board.request('front_retire',{'uuid':uid},key,origin='chat')
        self.assertEqual(proposal['phase'],'review')
        return proposal

    def capacity(self):
        end=stamp(NOW+timedelta(minutes=45))
        for uid in self.ids[:2]:self.apply('task_update',{'uuid':uid,'due':end})
        self.apply('context_update',{'availableMinutes':45,'availableUntil':end,'rest':False})
        self.apply('front_activate',{'uuid':self.ids[1]})
        self.board.review_changes()
        return [s for s in self.board.snapshot()['suggestions'] if s['code']=='capacity']

    def test_multiple_fronts_use_native_start_and_persist(self):
        for uid in self.ids[1:]:self.apply('front_activate',{'uuid':uid})
        native=self.app.tasks.rows()
        self.assertEqual(sum(bool(r.get('start')) for r in native),4)
        restarted=Connected(Assistant(self.temp.name,clock=lambda:NOW),self.fixture)
        restarted.startup()
        self.assertEqual(sum(t['state']=='active' for t in restarted.snapshot()['tasks']),4)
        with self.app.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM frames').fetchone()[0],0)

    def test_typed_change_applies_in_its_request_without_a_model_turn(self):
        proposal=self.request('front_activate',{'uuid':self.ids[1]})
        self.assertEqual(proposal['phase'],'applied')
        self.assertTrue(self.app.tasks.get(self.ids[1]).get('start'))
        self.assertEqual(self.fixture.turns,0)
        with self.app.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM evaluations').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT review_complete FROM decisions').fetchone()[0],0)
        last=self.board.snapshot()['lastFact']
        self.assertEqual((last['action'],last['taskId'],last['reviewStatus'],last['undoable']),('front_activate',self.ids[1],'deferred',True))

    def test_waiting_change_is_not_recoverable_and_survives_restart(self):
        proposal=self.waiting_retire(self.ids[1])
        self.app.recover()
        self.assertEqual(self.app.tasks.get(self.ids[1])['status'],'pending')
        with self.app.store.connect() as con:self.assertEqual(con.execute("SELECT count(*) FROM operations WHERE key LIKE 'proposal:%'").fetchone()[0],0)
        self.board.startup()
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'review')
        with self.assertRaises(AssistantError):self.board.confirm(proposal['id'],proposal['generation']+1)
        self.board.confirm(proposal['id'],proposal['generation'])
        self.assertEqual(self.app.tasks.get(self.ids[1])['status'],'deleted')

    def test_new_valid_noop_supersedes_incompatible_pending(self):
        old=self.waiting_retire(self.ids[1])
        new=self.request('front_todo',{'uuid':self.ids[1]})
        self.assertEqual(new['phase'],'unchanged')
        self.assertIn(old['id'],new['result']['superseded'])
        with self.assertRaises(AssistantError):self.board.confirm(old['id'],old['generation'])
        self.assertEqual(self.app.tasks.get(self.ids[1])['status'],'pending')

    def test_invalid_request_does_not_cancel_valid_pending(self):
        old=self.waiting_retire(self.ids[1])
        with self.assertRaises(AssistantError):self.request('task_snooze',{'uuid':self.ids[1],'until':stamp(NOW-timedelta(minutes=1))})
        self.assertEqual(self.board.proposal(old['id'])['phase'],'review')

    def test_cancel_discards_a_waiting_change(self):
        proposal=self.waiting_retire(self.ids[1])
        self.board.cancel(proposal['id'])
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'canceled')
        with self.assertRaises(AssistantError):self.board.confirm(proposal['id'],proposal['generation'])
        self.assertEqual(self.app.tasks.get(self.ids[1])['status'],'pending')

    def test_waiting_change_goes_stale_when_a_linked_task_changes(self):
        self.apply('dependency_update',{'uuid':self.ids[2],'dependsOn':[self.ids[1]]})
        proposal=self.waiting_retire(self.ids[1])
        self.apply('task_done',{'uuid':self.ids[2]})
        current=self.board.proposal(proposal['id'])
        self.assertEqual(current['phase'],'stale')
        with self.assertRaises(AssistantError):self.board.confirm(proposal['id'],current['generation'])
        asked=self.board.retry(proposal['id'])
        self.assertEqual(asked['phase'],'review')
        self.board.confirm(proposal['id'],asked['generation'])
        self.assertEqual(self.app.tasks.get(self.ids[1])['status'],'deleted')

    def test_a_new_change_to_the_same_task_supersedes_a_waiting_one(self):
        proposal=self.waiting_retire(self.ids[1])
        self.apply('task_update',{'uuid':self.ids[1],'title':'Renamed while the retire waited'})
        self.assertEqual(self.board.proposal(proposal['id'])['phase'],'superseded')
        self.assertEqual(self.app.tasks.get(self.ids[1])['status'],'pending')

    def test_unrelated_change_keeps_a_waiting_change_current(self):
        proposal=self.waiting_retire(self.ids[1])
        self.apply('task_update',{'uuid':self.ids[2],'title':'Unrelated title'})
        current=self.board.proposal(proposal['id'])
        self.assertEqual((current['phase'],current['generation']),('review',proposal['generation']))

    def test_capacity_overflow_becomes_one_suggestion_on_the_activated_task(self):
        found=self.capacity()
        self.assertEqual([(s['taskId'],s['current'],s['action']) for s in found],[(self.ids[1],True,None)])
        self.assertEqual(found[0]['messageParams'],{'required':80,'possible':45})
        self.assertTrue(self.app.tasks.get(self.ids[1]).get('start'))
        self.apply('task_done',{'uuid':self.ids[2]});self.board.review_changes()
        self.assertEqual(len([s for s in self.board.snapshot()['suggestions'] if s['code']=='capacity']),1)

    def test_dismissed_capacity_stays_dismissed_until_a_new_cause(self):
        found=self.capacity()
        self.board.dismiss_suggestion(found[0]['id'])
        self.apply('front_todo',{'uuid':self.ids[3]});self.board.review_changes()
        current=[s for s in self.board.snapshot()['suggestions'] if s['code']=='capacity']
        self.assertEqual([s['status'] for s in current],['dismissed'])
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':50});self.board.review_changes()
        current=[s for s in self.board.snapshot()['suggestions'] if s['code']=='capacity']
        self.assertEqual([s['status'] for s in current],['dismissed','open'])
        self.assertEqual(current[1]['messageParams']['required'],90)

    def test_correcting_the_estimate_hides_a_capacity_suggestion(self):
        found=self.capacity()
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':5})
        self.assertFalse(next(s for s in self.board.snapshot()['suggestions'] if s['id']==found[0]['id'])['current'])

    def test_unknown_model_fields_leave_only_service_findings(self):
        self.fixture.suggestions=[{'taskId':self.ids[1],'message':'Start it.','action':None,'execute':'delete everything'}]
        self.capacity()
        suggestions=self.board.snapshot()['suggestions']
        self.assertEqual([s['origin'] for s in suggestions],['rules'])
        self.assertEqual(self.board.snapshot()['review']['code'],'agent_invalid_output')
        self.assertEqual(len(self.app.tasks.rows()),4)

    def test_model_cannot_erase_or_replace_service_findings(self):
        self.fixture.suggestions=[]
        found=self.capacity()
        self.assertEqual(len(found),1)
        self.assertEqual(self.board.snapshot()['review']['status'],'completed')

    def test_model_suggestion_names_a_changed_task_and_an_applicable_change(self):
        self.apply('front_activate',{'uuid':self.ids[2]});self.apply('task_update',{'uuid':self.ids[0],'title':'Changed too'})
        self.fixture.suggestions=[{'taskId':self.ids[2],'message':'You could resume this tomorrow.','action':{'action':'task_snooze','until':stamp(NOW+timedelta(days=1)),'due':None,'priority':None}},
                                  {'taskId':self.ids[0],'message':'This one cannot be snoozed into the past.','action':{'action':'task_snooze','until':stamp(NOW-timedelta(days=1)),'due':None,'priority':None}}]
        self.board.review_changes()
        stored={s['taskId']:s for s in self.board.snapshot()['suggestions']}
        self.assertEqual(stored[self.ids[2]]['action'],{'action':'task_snooze','payload':{'uuid':self.ids[2],'until':stamp(NOW+timedelta(days=1)),'zone':'America/Edmonton'}})
        self.assertIsNone(stored[self.ids[0]]['action'])
        # Active work that the changes did not touch is context, never a suggestion target.
        for target,action in (('00000000-0000-4000-8000-000000000000','front_todo'),(self.ids[0],'front_activate')):
            self.fixture.suggestions=[{'taskId':target,'message':'Unknown.','action':None}]
            self.apply(action,{'uuid':self.ids[2]});self.board.review_changes()
            self.assertEqual(self.board.snapshot()['review']['code'],'agent_invalid_output')

    def test_without_model_changes_apply_and_only_service_findings_are_kept(self):
        self.board.bridge=None
        found=self.capacity()
        self.assertTrue(self.app.tasks.get(self.ids[1]).get('start'))
        self.assertEqual(len(found),1)
        review=self.board.snapshot()['review']
        self.assertEqual((review['status'],review['code'],review['pending']),('incomplete','agent_unavailable',0))

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
        fact=self.board.snapshot()['events'][-1]['id'];proposal=self.board.undo(fact,'undo-retire')
        self.assertEqual(proposal['phase'],'applied')
        self.assertEqual(self.app.tasks.get(self.ids[1])['status'],'pending')
        self.assertEqual(json.loads(self.board.snapshot()['events'][-1]['plan'])['undo_of'],fact)
        self.assertEqual(self.board.snapshot()['lastFact']['undoOf'],fact)

    def test_undo_cannot_activate_a_now_blocked_dependency(self):
        self.app.tasks.patch(self.ids[1],{'status':'completed'})
        self.app.tasks.patch(self.ids[0],{'depends':self.ids[1]})
        self.apply('task_done',{'uuid':self.ids[0]})
        fact=self.board.snapshot()['events'][-1]['id']
        self.app.tasks.patch(self.ids[1],{'status':'pending','end':None})
        with self.assertRaises(AssistantError) as error:self.board.undo(fact,'undo-with-new-dependency')
        self.assertEqual(error.exception.code,'not_actionable')
        self.assertEqual(self.app.tasks.get(self.ids[0])['status'],'completed')

    def death(self,point):
        script='''
from datetime import datetime,timezone
import os,sys
from day_attendant.service import Assistant
from day_attendant.connected import Connected
def fault(point):
    if point==sys.argv[3]:os._exit(75)
app=Assistant(sys.argv[1],clock=lambda:datetime(2026,10,28,12,tzinfo=timezone.utc),fault=fault)
Connected(app).request('front_activate',{'uuid':sys.argv[2]},'death-key')
'''
        result=subprocess.run([sys.executable,'-B','-c',script,self.temp.name,self.ids[1],point],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,75,result.stderr)
        with self.app.store.connect() as con:identity=con.execute("SELECT id FROM proposals WHERE key='death-key'").fetchone()[0]
        self.assertEqual(self.board.proposal(identity)['phase'],'admitted')

    def test_process_death_after_authorized_admission_recovers_once(self):
        self.death('after_admission')
        self.assertFalse(self.app.tasks.get(self.ids[1]).get('start'))
        restarted=Connected(Assistant(self.temp.name,clock=lambda:NOW));restarted.startup();restarted.startup()
        self.assertTrue(restarted.app.tasks.get(self.ids[1]).get('start'))
        with self.app.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM facts').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT count(*) FROM decisions').fetchone()[0],1)

    def test_process_death_after_native_effect_recovers_once(self):
        self.death('after_task_0')
        self.assertTrue(self.app.tasks.get(self.ids[1]).get('start'))
        restarted=Connected(Assistant(self.temp.name,clock=lambda:NOW));restarted.startup();restarted.startup()
        self.assertTrue(restarted.app.tasks.get(self.ids[1]).get('start'))
        with self.app.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM facts').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT count(*) FROM decisions').fetchone()[0],1)

    def test_restart_replays_the_key_without_a_second_effect(self):
        self.death('after_admission')
        restarted=Connected(Assistant(self.temp.name,clock=lambda:NOW));restarted.startup()
        replay=restarted.request('front_activate',{'uuid':self.ids[1]},'death-key')
        self.assertEqual(replay['phase'],'applied')
        with self.app.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM facts').fetchone()[0],1)

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
            for seconds,status in ((limit-30,'completed'),(limit+30,'incomplete')):
                fixture.seconds=seconds
                uid=self.ids[2]
                fixture.suggestions=[{'taskId':uid,'message':'Reviewed at '+effort+' after '+str(seconds)+' s','action':None}]
                for note in self.board.snapshot()['suggestions']:self.board.dismiss_suggestion(note['id'])
                self.apply('task_update',{'uuid':uid,'title':'Reviewed at '+effort+' after '+str(seconds)+' s'})
                self.board.review_changes()
                outcome=self.board.snapshot()['review']
                self.assertEqual(outcome['status'],status,(effort,seconds,outcome))
                shown=any(s['message']==fixture.suggestions[0]['message'] for s in self.board.snapshot()['suggestions'])
                self.assertEqual(shown,status=='completed',(effort,seconds))

    def test_clear_never_waits_for_the_later_review(self):
        from day_attendant.conversation import Conversation
        self.capacity()
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':60})
        self.assertEqual(Conversation(self.board).clear('while-review-pending')['status'],'cleared')

    def test_corrected_effort_saves_at_once_and_is_noted_once(self):
        end=stamp(NOW+timedelta(hours=1))
        for uid in self.ids[:2]:self.apply('task_update',{'uuid':uid,'due':end})
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':5})
        self.apply('context_update',{'availableMinutes':45,'availableUntil':end})
        self.apply('front_activate',{'uuid':self.ids[1]})
        before=self.board.snapshot()
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':40})
        self.apply('continuity_update',{'uuid':self.ids[1],'remainingMinutes':40})
        self.assertFalse(any(m['key'].startswith('context-notice:') for m in self.board.snapshot()['messages']))
        self.board.review_changes();self.board.review_changes();after=self.board.snapshot()
        self.assertEqual(len([s for s in after['suggestions'] if s['code']=='capacity']),1)
        self.assertEqual([t['state'] for t in before['tasks']],[t['state'] for t in after['tasks']])
        with self.app.store.connect() as con:
            plan=json.loads(con.execute('SELECT plan FROM facts ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(plan['review_status'],'deferred');self.assertFalse(plan['review_complete'])

    def test_legacy_context_notice_closes_at_startup(self):
        with self.app.store.connect() as con:
            con.execute("INSERT INTO messages(key,role,text,status,data,at) VALUES('context-notice:legacy','assistant','Guidance pending.','pending','{}',?)",(self.app.now(),))
        self.board.startup()
        notice=next(m for m in self.board.snapshot()['messages'] if m['key']=='context-notice:legacy')
        self.assertEqual(notice['status'],'stale')


if __name__=='__main__':unittest.main()
