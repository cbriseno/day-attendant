"""Daily delivery policy, fair retries, portable memory and supervised recovery."""
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from day_attendant import lifecycle,recovery
from day_attendant.conversation import continuity_summary,portable_context
from day_attendant.errors import AssistantError
from day_attendant.profiles import configure,default_policy
from day_attendant.service import encoded
from test_daily_domain import DailyBase
from test_connected import NOW

class DailyOperations(DailyBase):
    def setUp(self):
        super().setUp();self.backups=tempfile.TemporaryDirectory(prefix='day-operation-backups-')
        self.now=NOW;self.app.clock=lambda:self.now;self.sent=[];self.app.notifier=lambda title,body:self.sent.append(body) or {'accepted':True}
        policy=default_policy();policy.update(weekdays=list(range(7)),start='00:00',end='23:59',quietStart='00:00',quietEnd='00:00',dailyBudget=12)
        configure(self.app,{'notification_policy':policy},'test-policy')
    def tearDown(self):self.backups.cleanup();super().tearDown()
    def due(self,title):return self.apply('task_add',{'title':title,'due':(NOW-timedelta(hours=1)).isoformat()})['result']['uuid']
    def test_silence_consumes_no_attempts_or_suppression(self):
        self.due('Deadline');policy=self.app.store.state_value('notification_policy');policy['enabled']=False
        configure(self.app,{'notification_policy':policy},'silence')
        self.assertEqual(self.app.notify()['deferred'],'disabled');self.assertEqual(self.sent,[])
        with self.app.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
        policy['enabled']=True;configure(self.app,{'notification_policy':policy},'resume');self.assertEqual(len(self.app.notify()['attempts']),1)
    def test_unnotified_work_receives_budget_before_unchanged_repeat(self):
        for i in range(5):self.due('Deadline '+str(i))
        self.assertEqual(len(self.app.notify()['attempts']),3)
        self.now+=timedelta(minutes=6);self.assertEqual(len(self.app.notify()['attempts']),2);self.assertEqual(len(set(self.sent)),5)
        self.now+=timedelta(hours=2);self.assertEqual(self.app.notify()['attempts'],[])
    def test_daily_budget_persists_and_unknown_acceptance_is_not_read(self):
        policy=self.app.store.state_value('notification_policy');policy['dailyBudget']=2;configure(self.app,{'notification_policy':policy},'daily-limit')
        for i in range(4):self.due('Budget '+str(i))
        result=self.app.notify();self.assertEqual(len(result['attempts']),2);self.assertTrue(all(x['read_by_user']=='unknown' for x in result['attempts']))
        self.now+=timedelta(minutes=20);self.assertEqual(self.app.notify()['attempts'],[])
    def test_tick_never_constructs_a_harness_and_records_backup_failure(self):
        self.due('Known data')
        with patch('day_attendant.harnesses.create_harness',side_effect=AssertionError('Model forbidden')),patch('day_attendant.archives.snapshot',side_effect=AssistantError('backup_space','No room')):
            result=lifecycle.tick(self.app)
        self.assertEqual(result['status'],'failed');self.assertFalse(result['model_called']);self.assertEqual(len(self.sent),1);self.assertEqual(self.app.store.state_value('last_tick')['error']['code'],'backup_space')
        self.assertEqual(lifecycle.tick(self.app)['skipped'],'interval')
    def test_cron_edit_preserves_other_jobs_and_is_idempotent(self):
        original='MAILTO=""\n17 3 * * * /usr/bin/true\n';block=lifecycle.cron_block(self.app,{'PATH':'/usr/bin:/bin','DISPLAY':':1'})
        self.assertEqual(lifecycle.without_block(original+block,self.app.store.state_value('profile')['id']),original)
        self.assertEqual(lifecycle.without_block(original,self.app.store.state_value('profile')['id']),original)
        with self.assertRaises(AssistantError):lifecycle.without_block(original+block.split(':end')[0],self.app.store.state_value('profile')['id'])
    def test_sourced_human_memory_survives_harness_switch_and_is_bounded(self):
        with self.app.store.connect() as con:
            for n in range(80):con.execute("INSERT INTO messages(key,role,text,status,at) VALUES(?,'user',?,'completed',?)",('human-'+str(n),'Original '+str(n)+' '+('x'*300),self.app.now()))
        summary=continuity_summary(self.app.store);self.assertEqual(len(summary['excerpts']),48);self.assertTrue(all(len(x['humanExcerpt'])<=160 for x in summary['excerpts']))
        configure(self.app,{'harness':'claude'},'switch')
        self.assertEqual(self.app.store.state_value('continuity_summary'),summary);self.assertIsNone(self.app.store.state_value('profile')['model'])
    def test_keep_observed_does_not_execute_a_prepared_original_action(self):
        uid=self.add('Before admission')
        self.app.fault=lambda point:(_ for _ in ()).throw(RuntimeError('interrupted after admission')) if point=='after_admission' else None
        p=self.request('front_activate',{'uuid':uid});self.board.evaluate(p['id']);self.app.fault=lambda point:None
        self.assertFalse(self.app.tasks.get(uid).get('start'));key=self.app.store.unresolved()[0]['key'];observed=recovery.inspect(self.app,key)
        self.assertTrue(observed['can_complete']);resolved=recovery.resolve(self.app,key,'keep_observed',observed['precondition'],self.backups.name)
        self.assertTrue(resolved['closed']);self.assertFalse(self.app.tasks.get(uid).get('start'))
    def test_policy_pause_after_claim_releases_unsent_notice_without_budget(self):
        self.due('Claimed but unsent')
        def pause(point):
            if point=='before_notify':
                policy=self.app.store.state_value('notification_policy');policy['enabled']=False
                configure(self.app,{'notification_policy':policy},'pause-after-claim')
        self.app.fault=pause;result=self.app.notify();self.assertEqual(result['attempts'][0]['status'],'deferred');self.assertEqual(self.sent,[])
        with self.app.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM attempts').fetchone()[0],0);self.assertEqual(con.execute("SELECT count(*) FROM notices WHERE status='sending'").fetchone()[0],0)
    def test_conflict_requires_current_observation_and_keeps_external_data(self):
        uid=self.add('Original');self.app.fault=lambda point:(_ for _ in ()).throw(RuntimeError('process died')) if point=='after_task_0' else None
        p=self.request('front_activate',{'uuid':uid})
        # Admission may throw a simulated process death after native modification.
        self.board.evaluate(p['id'])
        self.assertTrue(self.app.store.unresolved())
        self.app.fault=lambda point:None;self.app.tasks.patch(uid,{'description':'External title'})
        self.app.recover();key=self.app.store.unresolved()[0]['key'];observed=recovery.inspect(self.app,key)
        self.assertFalse(observed['can_complete'])
        with self.assertRaises(AssistantError):recovery.resolve(self.app,key,'complete',observed['precondition'],self.backups.name)
        with self.assertRaises(AssistantError):recovery.resolve(self.app,key,'keep_observed','stale',self.backups.name)
        resolved=recovery.resolve(self.app,key,'keep_observed',observed['precondition'],self.backups.name)
        self.assertTrue(resolved['closed']);self.assertEqual(self.app.tasks.get(uid)['description'],'External title');self.assertEqual(self.app.store.unresolved(),[])
        self.add('Can operate again')

if __name__=='__main__':unittest.main()
