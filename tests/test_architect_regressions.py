"""Round-20 reproduced boundary failures, using real SQLite stores."""
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch
from day_attendant import archives,recovery
from day_attendant.connected import Connected
from day_attendant.conversation import Conversation,FIELDS
from day_attendant.errors import AssistantError
from day_attendant.profiles import configure
from day_attendant.service import Assistant
from day_attendant.timeutil import stamp
from test_daily_domain import DailyBase
from test_connected import NOW,ReviewFixture
from test_conversation import ChatFixture

class ArchitectRegressions(DailyBase):
    def setUp(self):
        super().setUp();import tempfile
        self.output=tempfile.TemporaryDirectory(prefix='day-architect-regression-')
    def tearDown(self):self.output.cleanup();super().tearDown()
    def interrupted(self,action,payload,point='after_admission'):
        p=self.request(action,payload)
        self.app.fault=lambda at:(_ for _ in ()).throw(RuntimeError('simulated process loss')) if at==point else None
        self.board.evaluate(p['id']);self.app.fault=lambda at:None
        self.assertTrue(self.app.store.unresolved());return p
    def test_second_preexisting_proposal_cannot_bypass_partial_operation(self):
        a,b=self.add('A'),self.add('B');second=self.request('front_activate',{'uuid':b})
        with self.app.store.connect() as con:con.execute("UPDATE proposals SET phase='ready' WHERE id=?",(second['id'],))
        first=self.interrupted('front_activate',{'uuid':a},'after_task_0');revision=self.app.store.revision()
        with self.assertRaises(AssistantError) as error:self.board.confirm(second['id'],second['generation'])
        self.assertEqual(error.exception.code,'recovery_required');self.assertEqual(self.app.store.revision(),revision);self.assertFalse(self.app.tasks.get(b).get('start'))
        self.assertEqual(len(self.app.store.unresolved()),1);self.app.recover();self.assertEqual(self.app.store.unresolved(),[])
    def test_recovery_rechecks_native_prerequisite_read_set(self):
        a,b=self.add('Prerequisite'),self.add('Dependent');self.apply('dependency_update',{'uuid':b,'dependsOn':[a]});self.apply('task_done',{'uuid':a})
        self.interrupted('front_activate',{'uuid':b})
        self.app.tasks.patch(a,{'status':'pending','end':None})
        result=self.app.recover();self.assertTrue(result['unresolved']);self.assertFalse(self.app.tasks.get(b).get('start'))
        detail=recovery.inspect(self.app,result['unresolved'][0]['key']);self.assertFalse(detail['can_complete']);self.assertFalse(detail['read_guards']['matching'])
    def test_recovery_rechecks_graph_before_creating_cycle(self):
        a,b=self.add('A'),self.add('B');self.interrupted('dependency_update',{'uuid':b,'dependsOn':[a]})
        self.app.tasks.patch(a,{'depends':[b]});self.assertTrue(self.app.recover()['unresolved']);self.assertNotIn('depends',self.app.tasks.get(b))
    def test_partial_capture_can_gain_honest_persistent_provenance(self):
        p=self.interrupted('task_add',{'title':'Captured message'},'after_task_0');uid=self.app.tasks.rows()[0]['uuid'];self.app.tasks.patch(uid,{'description':'External title'});self.app.recover()
        key=self.app.store.unresolved()[0]['key'];observed=recovery.inspect(self.app,key);recovery.resolve(self.app,key,'keep_observed',observed['precondition'],self.output.name)
        observed_at=stamp(NOW-timedelta(days=1));self.apply('provenance_update',{'uuid':uid,'source':'chat','person':'Ana','reference':'Human supplied','observed_at':observed_at})
        reopened=Connected(Assistant(self.temp.name,clock=lambda:NOW),ReviewFixture());reopened.initialize();reopened.startup();item=reopened.snapshot()['tasks'][0]
        self.assertEqual((item['source'],item['person'],item['reference'],item['observedAt']),('chat','Ana','Human supplied',observed_at));self.assertIsNone(item['progressRecordedAt'])
        self.assertIn('chat_waiting',{n['kind'] for n in reopened.app.pulse()['notices']})
        with reopened.store.connect() as con:self.assertIsNone(con.execute('SELECT human_progress_at FROM task_meta WHERE uuid=?',(uid,)).fetchone()[0])
    def test_real_process_death_does_not_leave_permanent_maintenance(self):
        code="from day_attendant.service import Assistant;from day_attendant.archives import maintenance;import os,sys;a=Assistant(sys.argv[1]);m=maintenance(a,'real-death');m.__enter__();os._exit(37)"
        run=subprocess.run([sys.executable,'-B','-c',code,self.temp.name],capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,37,run.stderr);self.assertTrue(self.app.store.state_value('maintenance'))
        self.board.initialize();self.assertIsNone(self.app.store.state_value('maintenance'));self.assertTrue(self.app.store.state_value('maintenance_recovery'));self.add('Recovered without manual SQL')
    def test_live_maintenance_owner_is_not_reclaimed(self):
        with archives.maintenance(self.app,'live'):
            self.assertTrue(archives.recover_maintenance(self.app)['active']);self.assertTrue(self.app.store.state_value('maintenance'))
            with self.assertRaises(AssistantError):self.board.retry(self.request_placeholder())
    def request_placeholder(self):
        # Invalid ID is irrelevant: maintenance must be checked before evaluation.
        return '00000000-0000-0000-0000-000000000000'
    def test_ready_without_admission_is_visible_and_retriable_after_restart(self):
        uid=self.add('Pending');p=self.request('front_activate',{'uuid':uid})
        with self.app.store.connect() as con:con.execute("UPDATE proposals SET phase='ready' WHERE id=?",(p['id'],))
        self.board.startup();current=self.board.proposal(p['id']);self.assertEqual(current['phase'],'unavailable');self.assertFalse(self.app.tasks.get(uid).get('start'))
        retried=self.board.retry(p['id']);self.board.evaluate(retried['id']);self.assertTrue(self.app.tasks.get(uid).get('start'))
    def test_reopen_blocks_dependents_without_removing_active_front(self):
        a,b=self.add('A'),self.add('B');self.apply('dependency_update',{'uuid':b,'dependsOn':[a]});self.apply('task_done',{'uuid':a});self.apply('front_activate',{'uuid':b})
        p=self.request('task_reopen',{'uuid':a});self.board.evaluate(p['id']);p=self.board.proposal(p['id']);self.assertEqual(p['phase'],'review');self.board.confirm(p['id'],p['generation'])
        dependent=next(t for t in self.board.snapshot()['tasks'] if t['id']==b);self.assertTrue(dependent['blocked']);self.assertEqual(dependent['state'],'active')

    def enable_delivery(self):
        policy=self.app.store.state_value('notification_policy');policy.update(weekdays=list(range(7)),start='00:00',end='23:59',quietStart='00:00',quietEnd='00:00')
        configure(self.app,{'notification_policy':policy},'test-delivery')
        self.apply('task_add',{'title':'Private reminder','due':stamp(NOW-timedelta(hours=1))})
    def test_backup_waits_for_claim_owner_to_defer_before_transport(self):
        self.enable_delivery();claimed=threading.Event();release=threading.Event();sent=[];errors=[];result=[]
        self.app.notifier=lambda *args:sent.append(args) or {'accepted':True}
        self.app.fault=lambda point:(claimed.set(),release.wait(5)) if point=='after_claim' else None
        def notify():
            try:result.append(self.app.notify())
            except BaseException as e:errors.append(e)
        def backup():
            try:result.append(archives.snapshot(self.app,self.output.name,timeout=4))
            except BaseException as e:errors.append(e)
        worker=threading.Thread(target=notify);worker.start();self.assertTrue(claimed.wait(3));snapshot=threading.Thread(target=backup);snapshot.start()
        deadline=time.monotonic()+3
        while not self.app.store.state_value('maintenance') and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(self.app.store.state_value('maintenance'));release.set();worker.join(5);snapshot.join(5)
        self.assertFalse(worker.is_alive());self.assertFalse(snapshot.is_alive());self.assertEqual(errors,[]);self.assertEqual(sent,[])
        with self.app.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
        self.assertEqual(len(list(Path(self.output.name).glob('*.zip'))),1)
    def test_backup_never_uses_lease_expiry_as_proof_live_transport_finished(self):
        self.enable_delivery();entered=threading.Event();release=threading.Event();errors=[]
        def notifier(*args):entered.set();release.wait(5);return {'accepted':True}
        self.app.notifier=notifier
        def send():
            try:self.app.notify()
            except BaseException as e:errors.append(e)
        worker=threading.Thread(target=send);worker.start();self.assertTrue(entered.wait(3))
        original=self.app.clock;self.app.clock=lambda:NOW+timedelta(hours=1)
        with self.assertRaises(AssistantError) as error:archives.snapshot(self.app,self.output.name,timeout=.1)
        self.assertEqual(error.exception.code,'maintenance_busy');self.assertEqual(list(Path(self.output.name).glob('*.zip')),[])
        self.app.clock=original;release.set();worker.join(5);self.assertEqual(errors,[])
        with self.app.store.connect() as con:self.assertEqual(con.execute('SELECT status FROM attempts').fetchone()[0],'accepted')
    def test_batch_does_not_restart_sibling_after_maintenance_invalidates_generation(self):
        text='Capture Alpha and Beta';intent={key:None for key in FIELDS};intent.update(action='task_add',title='Alpha',clearDue=False,userAuthorized=True,requestEvidence=text)
        other=dict(intent,title='Beta');fixture=ChatFixture({'message':'Will check both.','uncertainties':[],'intent':intent,'additionalIntents':[other],'unprocessed':[]})
        entered=threading.Event();release=threading.Event();native_turn=fixture.turn
        def blocked(*args,**kwargs):
            if 'intent' not in args[2]['properties']:entered.set();release.wait(5)
            return native_turn(*args,**kwargs)
        fixture.turn=blocked;self.board.bridge=fixture;chat=Conversation(self.board);chat.submit(text,'batch-maintenance')
        worker=threading.Thread(target=chat.run,args=('batch-maintenance',));worker.start();self.assertTrue(entered.wait(3))
        with archives.maintenance(self.app,'batch-test'):
            release.set();worker.join(5);self.assertFalse(worker.is_alive());self.assertEqual(fixture.calls,2)
            self.assertEqual(self.app.tasks.rows(),[]);self.assertTrue(all(p['phase']=='stale' for p in self.board.snapshot()['proposals']))
        self.assertEqual(fixture.calls,2)
    def test_informational_guidance_never_uses_previous_selected_provider(self):
        a,b=self.add('A'),self.add('B')
        for uid in (a,b):
            self.apply('task_update',{'uuid':uid,'due':stamp(NOW+timedelta(hours=2))});self.apply('continuity_update',{'uuid':uid,'remainingMinutes':40});self.apply('front_activate',{'uuid':uid})
        self.apply('context_update',{'availableMinutes':45,'availableUntil':stamp(NOW+timedelta(hours=2))})
        self.board.bridge.provider='codex';configure(self.app,{'harness':'claude'},'provider-change')
        self.apply('continuity_update',{'uuid':a,'remainingMinutes':50})
        with patch.object(self.board.bridge,'turn',side_effect=AssertionError('Wrong harness turn')) as turn:
            self.board.evaluate_notices();self.assertEqual(turn.call_count,0)
        self.assertFalse(any(m['status']=='completed' and m['key'].startswith('context-notice:') for m in self.board.snapshot()['messages']))

if __name__=='__main__':import unittest;unittest.main()
