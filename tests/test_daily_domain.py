"""Daily human flows over real native Taskwarrior and SQLite; controlled reasoning."""
from datetime import timedelta
import json
import tempfile
import unittest

from day_attendant.connected import Connected
from day_attendant.conversation import Conversation,FIELDS
from day_attendant.errors import AssistantError
from day_attendant.profiles import configure,delivery_allowed,default_policy
from day_attendant.service import Assistant
from day_attendant.taskwarrior import canonical
from day_attendant.timeutil import stamp
from test_connected import NOW,ReviewFixture
from test_conversation import ChatFixture

class DailyBase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='day-personal-test-')
        self.app=Assistant(self.temp.name,clock=lambda:NOW);self.board=Connected(self.app,ReviewFixture())
        self.board.initialize(kind='personal',zone='America/Edmonton');self.counter=0
    def tearDown(self):self.temp.cleanup()
    def request(self,action,payload):
        self.counter+=1;return self.board.request(action,payload,'daily-'+str(self.counter))
    def apply(self,action,payload):
        p=self.request(action,payload)
        if p['phase']=='evaluating':self.board.evaluate(p['id'])
        p=self.board.proposal(p['id']);self.assertIn(p['phase'],('applied','unchanged'),p)
        return p
    def add(self,title):return self.apply('task_add',{'title':title})['result']['uuid']

class DailyDomainTests(DailyBase):
    def test_personal_profile_is_empty_stable_and_not_reseeded(self):
        profile=self.board.snapshot()['profile'];self.assertEqual(self.board.snapshot()['tasks'],[])
        self.assertEqual(self.board.snapshot()['messages'],[])
        self.add('Mi responsabilidad')
        restarted=Connected(Assistant(self.temp.name,clock=lambda:NOW),ReviewFixture());restarted.initialize();restarted.startup()
        self.assertEqual(restarted.snapshot()['profile'],self.board.snapshot()['profile'])
        self.assertEqual(profile['id'],restarted.snapshot()['profile']['id'])
        self.assertFalse(restarted.snapshot()['synthetic']);self.assertEqual(len(restarted.snapshot()['tasks']),1)
        with self.assertRaises(AssistantError):restarted.initialize(kind='synthetic')
    def test_native_dependency_blocks_then_completion_only_unlocks(self):
        a,b=self.add('A'),self.add('B')
        self.apply('dependency_update',{'uuid':b,'dependsOn':[a]})
        self.assertEqual(self.app.tasks.get(b)['depends'],[a])
        with self.assertRaises(AssistantError):self.request('front_activate',{'uuid':b})
        self.apply('task_done',{'uuid':a})
        target=next(t for t in self.board.snapshot()['tasks'] if t['id']==b)
        self.assertFalse(target['blocked']);self.assertEqual(target['state'],'todo');self.assertFalse(self.app.tasks.get(b).get('start'))
        self.apply('dependency_update',{'uuid':b,'dependsOn':[]})
        self.assertNotIn('depends',self.app.tasks.get(b))
    def test_concurrent_cycle_is_rejected_again_at_admission(self):
        a,b=self.add('A'),self.add('B')
        first=self.request('dependency_update',{'uuid':a,'dependsOn':[b]})
        second=self.request('dependency_update',{'uuid':b,'dependsOn':[a]})
        self.board.evaluate(first['id']);self.assertEqual(self.board.proposal(first['id'])['phase'],'applied')
        self.assertEqual(self.board.proposal(second['id'])['phase'],'stale')
        with self.assertRaises(AssistantError) as error:self.board.retry(second['id'])
        self.assertEqual(error.exception.code,'dependency_cycle');self.assertNotIn('depends',self.app.tasks.get(b))
    def test_related_is_symmetric_idempotent_and_has_no_native_effect(self):
        a,b=self.add('A'),self.add('B');before=[canonical(t) for t in self.app.tasks.rows()]
        self.apply('related_update',{'uuid':a,'relatedTo':[b]});self.apply('related_update',{'uuid':b,'relatedTo':[a]})
        rows={t['id']:t for t in self.board.snapshot()['tasks']}
        self.assertEqual(rows[a]['relatedTo'],[b]);self.assertEqual(rows[b]['relatedTo'],[a]);self.assertEqual(before,[canonical(t) for t in self.app.tasks.rows()])
        with self.app.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM related').fetchone()[0],1)
    def test_retiring_prerequisite_is_reviewed_and_does_not_fulfill_it(self):
        a,b=self.add('A'),self.add('B');self.apply('dependency_update',{'uuid':b,'dependsOn':[a]})
        p=self.request('front_retire',{'uuid':a});self.board.evaluate(p['id']);p=self.board.proposal(p['id'])
        self.assertEqual(p['phase'],'review');self.board.confirm(p['id'],p['generation'])
        target=next(t for t in self.board.snapshot()['tasks'] if t['id']==b)
        self.assertTrue(target['blocked']);self.assertEqual(target['blockedBy'],[a]);self.assertEqual(target['state'],'todo')
    def test_reopening_prerequisite_preserves_active_but_blocked_front(self):
        a,b=self.add('A'),self.add('B');self.apply('dependency_update',{'uuid':b,'dependsOn':[a]})
        self.apply('task_done',{'uuid':a});fact=self.board.snapshot()['events'][-1]['id']
        self.apply('front_activate',{'uuid':b})
        p=self.board.undo(fact,'undo-prerequisite');self.board.evaluate(p['id']);p=self.board.proposal(p['id'])
        self.assertEqual(p['phase'],'review');self.board.confirm(p['id'],p['generation'])
        target=next(t for t in self.board.snapshot()['tasks'] if t['id']==b)
        self.assertEqual(target['state'],'active');self.assertTrue(target['blocked']);self.assertTrue(self.app.tasks.get(b).get('start'))
    def test_calendar_edits_invalidate_a_conflicting_pending_snooze(self):
        uid=self.add('Draft')
        event=self.apply('event_add',{'title':'Meeting','start':stamp(NOW+timedelta(hours=1)),'end':stamp(NOW+timedelta(hours=2))})['result']['uuid']
        p=self.request('task_snooze',{'uuid':uid,'until':stamp(NOW+timedelta(minutes=90))});self.board.evaluate(p['id'])
        self.assertEqual(self.board.proposal(p['id'])['phase'],'review')
        self.apply('event_update',{'uuid':event,'start':stamp(NOW+timedelta(days=1)),'end':stamp(NOW+timedelta(days=1,hours=1))})
        self.assertEqual(self.board.proposal(p['id'])['phase'],'stale')
        self.board.retry(p['id']);self.board.evaluate(p['id']);self.assertEqual(self.board.proposal(p['id'])['phase'],'applied')
        self.apply('event_cancel',{'uuid':event});self.assertEqual(self.board.snapshot()['calendar_events'],[])
    def test_capture_keeps_observed_and_captured_times_separate(self):
        observed=stamp(NOW-timedelta(days=1))
        uid=self.apply('task_add',{'title':'Reply to Ana','source':'chat','person':'Ana','reference':'Human supplied reference','observed_at':observed})['result']['uuid']
        item=next(t for t in self.board.snapshot()['tasks'] if t['id']==uid)
        self.assertEqual(item['observedAt'],observed);self.assertEqual(item['capturedAt'],stamp(NOW));self.assertEqual(item['person'],'Ana')
        self.assertIn('chat_waiting',{n['kind'] for n in self.app.pulse()['notices']})
    def test_locale_changes_do_not_invalidate_pending_reasoning(self):
        a=self.add('Responsabilidad en español');p=self.request('front_activate',{'uuid':a})
        revision=self.app.store.revision();epoch=self.app.store.epoch()
        configure(self.app,{'locale':'es'},'locale-test',revision)
        self.assertEqual(self.app.store.revision(),revision);self.assertEqual(self.app.store.epoch(),epoch)
        self.assertEqual(self.board.proposal(p['id'])['phase'],'evaluating')
        self.assertEqual(self.app.tasks.get(a)['description'],'Responsabilidad en español')
    def test_work_and_quiet_windows_do_not_claim_human_availability(self):
        policy=default_policy();policy.update(weekdays=list(range(7)),start='22:00',end='02:00',quietStart='00:00',quietEnd='00:00')
        from datetime import datetime,timezone
        self.assertTrue(delivery_allowed(policy,datetime(2026,10,28,5,tzinfo=timezone.utc),'America/Edmonton')[0])
        self.assertFalse(delivery_allowed(policy,datetime(2026,10,28,18,tzinfo=timezone.utc),'America/Edmonton')[0])

class DailyBatchTests(DailyBase):
    def intent(self,text,action,**values):
        result={key:None for key in FIELDS};result.update(action=action,clearDue=False,userAuthorized=True,requestEvidence=text,**values);return result
    def test_batch_captures_two_tasks_and_an_event_once(self):
        text='Capture Alpha, Beta and the meeting.'
        output={'message':'The service will check all three items.','uncertainties':[],'intent':self.intent(text,'task_add',title='Alpha'),
            'additionalIntents':[self.intent(text,'task_add',title='Beta'),self.intent(text,'event_add',title='Meeting',start=stamp(NOW+timedelta(hours=1)),end=stamp(NOW+timedelta(hours=2)))],'unprocessed':[]}
        fixture=ChatFixture(output);self.board.bridge=fixture;chat=Conversation(self.board)
        chat.submit(text,'three-items');chat.run('three-items');chat.submit(text,'three-items');chat.run('three-items')
        self.assertEqual({t['title'] for t in self.board.snapshot()['tasks']},{'Alpha','Beta'});self.assertEqual(len(self.board.snapshot()['calendar_events']),1)
        self.assertEqual(fixture.calls,4)
        with self.app.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM proposals').fetchone()[0],3)
    def test_invalid_second_item_leaves_prior_intent_and_tasks_untouched(self):
        uid=self.add('Existing');old=self.request('front_activate',{'uuid':uid});text='Capture these items.'
        fixture=ChatFixture({'message':'Will check.','uncertainties':[],'intent':self.intent(text,'task_add',title='Must not appear'),
            'additionalIntents':[self.intent(text,'dependency_update',taskId=uid,dependsOn=['not-a-uuid'])],'unprocessed':[]})
        self.board.bridge=fixture;chat=Conversation(self.board);chat.submit(text,'bad-batch');chat.run('bad-batch')
        self.assertEqual(len(self.app.tasks.rows()),1);self.assertEqual(self.board.proposal(old['id'])['phase'],'evaluating')
        with self.app.store.connect() as con:self.assertEqual(con.execute("SELECT count(*) FROM proposals WHERE message_key='bad-batch'").fetchone()[0],0)

if __name__=='__main__':unittest.main()
