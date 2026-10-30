"""Daily human flows over real SQLite task and auxiliary stores; controlled reasoning."""
from datetime import timedelta
import json
import tempfile
import unittest

from day_attendant.connected import Connected
from day_attendant.conversation import Conversation,FIELDS
from day_attendant.errors import AssistantError
from day_attendant.profiles import configure,delivery_allowed,default_policy
from day_attendant.service import Assistant
from day_attendant.taskstore import canonical
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
        p=self.request(action,payload);self.assertIn(p['phase'],('applied','unchanged'),p)
        return p
    def add(self,title):return self.apply('task_add',{'title':title})['result']['uuid']
    def waiting_retire(self,uid):
        """Retiring from the conversation is the one change that waits for the person."""
        self.counter+=1;p=self.board.request('front_retire',{'uuid':uid},'chat-retire-'+str(self.counter),origin='chat')
        self.assertEqual(p['phase'],'review');return p
    def legacy_pending(self,action,payload,phase='unavailable'):
        """A proposal that a service from before instant actions left waiting for its review."""
        import uuid
        from day_attendant.service import encoded,fingerprint
        identity=str(uuid.uuid4());intent={'action':action,'payload':payload};self.counter+=1
        with self.app.store.connect() as con:
            con.execute("INSERT INTO proposals(id,key,digest,intent,entity,origin,request_text,phase,generation,fingerprint,facts,created_at) VALUES(?,?,?,?,?,'ui','',?,0,'legacy','[]',?)",
                (identity,'legacy-'+str(self.counter),fingerprint(intent),encoded(intent),'task:'+payload.get('uuid','new'),phase,self.app.now()))
        return self.board.proposal(identity)
    def current_suggestions(self):
        return [(s['taskId'],s['code'],s['action']) for s in self.board.snapshot()['suggestions'] if s['current']]

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
    def test_cycle_is_rejected_at_once_and_again_at_admission(self):
        a,b=self.add('A'),self.add('B')
        legacy=self.legacy_pending('dependency_update',{'uuid':b,'dependsOn':[a]})
        self.apply('dependency_update',{'uuid':a,'dependsOn':[b]})
        with self.assertRaises(AssistantError) as error:self.request('dependency_update',{'uuid':b,'dependsOn':[a]})
        self.assertEqual(error.exception.code,'dependency_cycle')
        with self.app.store.connect() as con:self.assertEqual(con.execute("SELECT count(*) FROM operations WHERE key LIKE 'proposal:%'").fetchone()[0],3)
        with self.assertRaises(AssistantError) as error:self.board.retry(legacy['id'])
        self.assertEqual(error.exception.code,'dependency_cycle');self.assertNotIn('depends',self.app.tasks.get(b))
        shown=next(p for p in self.board.snapshot()['proposals'] if p['id']==legacy['id']);self.assertEqual((shown['phase'],shown['error_code']),('invalid','dependency_cycle'))
    def test_related_is_symmetric_idempotent_and_has_no_native_effect(self):
        a,b=self.add('A'),self.add('B');before=[canonical(t) for t in self.app.tasks.rows()]
        self.apply('related_update',{'uuid':a,'relatedTo':[b]});self.apply('related_update',{'uuid':b,'relatedTo':[a]})
        rows={t['id']:t for t in self.board.snapshot()['tasks']}
        self.assertEqual(rows[a]['relatedTo'],[b]);self.assertEqual(rows[b]['relatedTo'],[a]);self.assertEqual(before,[canonical(t) for t in self.app.tasks.rows()])
        with self.app.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM related').fetchone()[0],1)
    def test_retiring_prerequisite_applies_does_not_fulfill_it_and_is_noted(self):
        a,b=self.add('A'),self.add('B');self.apply('dependency_update',{'uuid':b,'dependsOn':[a]})
        self.apply('front_retire',{'uuid':a})
        target=next(t for t in self.board.snapshot()['tasks'] if t['id']==b)
        self.assertTrue(target['blocked']);self.assertEqual(target['blockedBy'],[a]);self.assertEqual(target['state'],'todo')
        self.board.review_changes()
        self.assertEqual(self.current_suggestions(),[(b,'retired_prerequisite',{'action':'dependency_update','payload':{'uuid':b,'dependsOn':[]}})])
    def test_reopening_prerequisite_preserves_active_but_blocked_front(self):
        a,b=self.add('A'),self.add('B');self.apply('dependency_update',{'uuid':b,'dependsOn':[a]})
        self.apply('task_done',{'uuid':a});fact=self.board.snapshot()['events'][-1]['id']
        self.apply('front_activate',{'uuid':b})
        p=self.board.undo(fact,'undo-prerequisite');self.assertEqual(p['phase'],'applied')
        target=next(t for t in self.board.snapshot()['tasks'] if t['id']==b)
        self.assertEqual(target['state'],'active');self.assertTrue(target['blocked']);self.assertTrue(self.app.tasks.get(b).get('start'))
        self.board.review_changes()
        self.assertEqual(self.current_suggestions(),[(b,'active_blocked',{'action':'front_activate','payload':{'uuid':a}})])
    def test_snooze_into_a_commitment_applies_and_suggests_its_end(self):
        uid=self.add('Draft')
        event=self.apply('event_add',{'title':'Meeting','start':stamp(NOW+timedelta(hours=1)),'end':stamp(NOW+timedelta(hours=2))})['result']['uuid']
        self.apply('task_snooze',{'uuid':uid,'until':stamp(NOW+timedelta(minutes=90))})
        self.assertTrue(self.app.tasks.get(uid).get('wait'))
        self.board.review_changes()
        self.assertEqual(self.current_suggestions(),[(uid,'calendar',{'action':'task_snooze','payload':{'uuid':uid,'until':stamp(NOW+timedelta(hours=2))}})])
        suggestion=next(s for s in self.board.snapshot()['suggestions'] if s['current'])
        self.assertEqual(suggestion['messageParams'],{'event':'Meeting'})
        self.board.accept_suggestion(suggestion['id'])
        self.assertEqual(next(t for t in self.board.snapshot()['tasks'] if t['id']==uid)['until'],stamp(NOW+timedelta(hours=2)))
        self.assertEqual(self.current_suggestions(),[])
        self.apply('event_cancel',{'uuid':event});self.assertEqual(self.board.snapshot()['calendar_events'],[])
    def test_capture_keeps_observed_and_captured_times_separate(self):
        observed=stamp(NOW-timedelta(days=1))
        uid=self.apply('task_add',{'title':'Reply to Ana','source':'chat','person':'Ana','reference':'Human supplied reference','observed_at':observed})['result']['uuid']
        item=next(t for t in self.board.snapshot()['tasks'] if t['id']==uid)
        self.assertEqual(item['observedAt'],observed);self.assertEqual(item['capturedAt'],stamp(NOW));self.assertEqual(item['person'],'Ana')
        self.assertIn('chat_waiting',{n['kind'] for n in self.app.pulse()['notices']})
    def test_locale_changes_do_not_invalidate_pending_reasoning(self):
        a=self.add('Responsabilidad en español');p=self.waiting_retire(a)
        revision=self.app.store.revision();epoch=self.app.store.epoch()
        configure(self.app,{'locale':'es'},'locale-test',revision)
        self.assertEqual(self.app.store.revision(),revision);self.assertEqual(self.app.store.epoch(),epoch)
        current=self.board.proposal(p['id']);self.assertEqual((current['phase'],current['generation']),('review',p['generation']))
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
        # One interpretation turn; the interpreted changes apply without a second review.
        self.assertEqual(fixture.calls,1)
        with self.app.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM proposals').fetchone()[0],3)
            self.assertEqual(con.execute("SELECT count(*) FROM proposals WHERE phase='applied'").fetchone()[0],3)
            self.assertEqual(con.execute('SELECT count(*) FROM evaluations').fetchone()[0],0)
    def test_invalid_second_item_leaves_prior_intent_and_tasks_untouched(self):
        uid=self.add('Existing');old=self.waiting_retire(uid);text='Capture these items.'
        fixture=ChatFixture({'message':'Will check.','uncertainties':[],'intent':self.intent(text,'task_add',title='Must not appear'),
            'additionalIntents':[self.intent(text,'dependency_update',taskId=uid,dependsOn=['not-a-uuid'])],'unprocessed':[]})
        self.board.bridge=fixture;chat=Conversation(self.board);chat.submit(text,'bad-batch');chat.run('bad-batch')
        self.assertEqual(len(self.app.tasks.rows()),1);self.assertEqual(self.board.proposal(old['id'])['phase'],'review')
        with self.app.store.connect() as con:self.assertEqual(con.execute("SELECT count(*) FROM proposals WHERE message_key='bad-batch'").fetchone()[0],0)

if __name__=='__main__':unittest.main()
