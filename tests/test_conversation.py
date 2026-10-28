"""Conversation authority and content-language preservation, controlled outputs."""
import json
import tempfile
import unittest

from day_attendant.connected import Connected
from day_attendant.conversation import Conversation,FIELDS
from day_attendant.errors import AssistantError
from day_attendant.service import Assistant
from test_connected import ReviewFixture


class ChatFixture(ReviewFixture):
    def __init__(self,output):
        self.output=dict(output,additionalIntents=output.get('additionalIntents',[]),unprocessed=output.get('unprocessed',[]));self.calls=0
    def turn(self,thread,prompt,schema,**kwargs):
        self.calls+=1
        if 'intent' not in schema['properties']:return super().turn(thread,prompt,schema,**kwargs)
        return {'output':self.output,'model':self.model,'thread_id':thread,'turn_id':'chat-fixture','seconds':0,'status':'completed'}


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='day-chat-test-');self.app=Assistant(self.temp.name)
        self.board=Connected(self.app);self.board.initialize()
        with self.app.store.connect() as con:self.uid=json.loads(con.execute("SELECT result FROM operations WHERE key='bootstrap-task-2'").fetchone()[0])['uuid']
    def tearDown(self):self.temp.cleanup()

    def fixture(self,text,action=None,**values):
        intent=None
        if action:
            intent={name:None for name in FIELDS}
            intent.update(action=action,clearDue=False,userAuthorized=True,requestEvidence=text,**values)
        fixture=ChatFixture({'message':'A proposal will be checked by the service.','uncertainties':[],'intent':intent})
        self.board.bridge=fixture;return fixture,Conversation(self.board)

    def test_idea_is_not_captured_without_intent(self):
        text='Keep photography as an idea, not a task.';fixture,chat=self.fixture(text)
        chat.submit(text,'idea');chat.run('idea')
        self.assertEqual(len(self.app.tasks.rows()),4)
        self.assertEqual(fixture.calls,1)

    def test_new_capture_keeps_explicit_title_and_remaining_estimate(self):
        text='Add a task titled Review the launch checklist, with fifteen minutes remaining.'
        _,chat=self.fixture(text,'task_add',title='Review the launch checklist',priority='medium',remainingMinutes=15)
        chat.submit(text,'capture');chat.run('capture')
        tasks=self.board.snapshot()['tasks'];target=next(t for t in tasks if t['title']=='Review the launch checklist')
        self.assertEqual(target['remainingMinutes'],15);self.assertEqual(target['state'],'todo');self.assertIsNone(target['due'])

    def test_unrequested_translation_of_existing_title_is_rejected(self):
        original=self.app.tasks.get(self.uid)['description'];text='Sólo cambia la prioridad a alta.'
        _,chat=self.fixture(text,'task_update',taskId=self.uid,priority='high',title='Prepare two points for the meeting')
        chat.submit(text,'bad-translation');chat.run('bad-translation')
        self.assertEqual(self.app.tasks.get(self.uid)['description'],original)
        self.assertEqual(self.app.tasks.get(self.uid)['priority'],'M')
        with self.app.store.connect() as con:self.assertEqual(con.execute("SELECT status FROM messages WHERE key='bad-translation'").fetchone()[0],'incomplete')

    def test_retry_reuses_durable_proposal_after_message_failure(self):
        text='Add a task titled Check the report.';fixture,chat=self.fixture(text,'task_add',title='Check the report',priority='medium')
        original=self.board.request_many
        def fail_after_proposal(*args,**kwargs):
            original(*args,**kwargs)
            raise AssistantError('controlled_failure','Reply failed after proposal persistence.')
        self.board.request_many=fail_after_proposal
        chat.submit(text,'retry-safe');chat.run('retry-safe');self.board.request_many=original
        self.assertEqual(fixture.calls,1);self.assertEqual(len(self.app.tasks.rows()),4)
        result=chat.retry('retry-safe');self.assertEqual(result['status'],'completed');self.assertEqual(fixture.calls,1)
        with self.app.store.connect() as con:
            proposals=con.execute('SELECT id FROM proposals').fetchall()
            self.assertEqual(len(proposals),1)
        self.assertEqual(self.board.proposal(proposals[0][0])['phase'],'unavailable')
        self.board.retry(proposals[0][0]);self.board.evaluate(proposals[0][0])
        self.assertEqual(sum(t['description']=='Check the report' for t in self.app.tasks.rows()),1)

    def test_chat_declaration_expansion_requires_scope_review(self):
        text='Only set the remaining effort to 15; do not change waiting.'
        fixture,chat=self.fixture(text,'continuity_update',taskId=self.uid,remainingMinutes=15,awaiting=False)
        original=fixture.turn
        def reject_scope(thread,prompt,schema,**kwargs):
            if 'intent' in schema['properties']:return original(thread,prompt,schema,**kwargs)
            fixture.calls+=1
            return {'output':{'verdict':'insufficient','message':'The waiting change is not authorized.','concerns':[],'uncertainties':['Scope expansion'],'alternatives':[]},'model':fixture.model,'thread_id':thread,'turn_id':'review','seconds':0}
        fixture.turn=reject_scope
        chat.submit(text,'expanded');chat.run('expanded')
        self.assertEqual(fixture.calls,2)
        p=self.board.snapshot()['proposals'][-1]
        self.assertEqual(p['phase'],'unavailable')
        self.assertEqual(next(t for t in self.board.snapshot()['tasks'] if t['id']==self.uid)['remainingMinutes'],10)

    def test_interrupted_chat_result_cannot_create_an_operation(self):
        text='Add a task titled Must not appear.';fixture,chat=self.fixture(text,'task_add',title='Must not appear',priority='medium')
        original=fixture.turn
        def restarted(*args,**kwargs):
            result=original(*args,**kwargs);self.board.startup();return result
        fixture.turn=restarted
        chat.submit(text,'interrupted');chat.run('interrupted')
        self.assertEqual(len(self.app.tasks.rows()),4)
        self.assertEqual(self.board.snapshot()['proposals'],[])
        with self.app.store.connect() as con:self.assertEqual(con.execute("SELECT status FROM messages WHERE key='interrupted'").fetchone()[0],'incomplete')


if __name__=='__main__':unittest.main()
