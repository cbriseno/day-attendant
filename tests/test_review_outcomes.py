"""Pending proposals carry stable outcome codes so interfaces never show internal review errors."""
import json
import tempfile
import unittest

from day_attendant.connected import REVIEW_SCHEMA, Connected
from day_attendant.conversation import Conversation
from day_attendant.errors import AssistantError
from day_attendant.service import Assistant
from test_connected import NOW


class ScriptedReview:
    model='controlled-fixture'
    available=True

    def __init__(self):
        self.output=None;self.error=None;self.calls=[]

    def thread(self,previous=None):return 'controlled-thread'

    def turn(self,thread,prompt,schema,**kwargs):
        self.calls.append((prompt,schema))
        if self.error:raise self.error
        return {'output':self.output,'model':self.model,'thread_id':thread,'turn_id':'controlled-turn','seconds':0,'status':'completed'}


def review(verdict='allow',concerns=(),message='Controlled review.'):
    return {'verdict':verdict,'message':message,'concerns':list(concerns),'uncertainties':[],'alternatives':[]}


class ReviewOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='day-review-outcome-')
        self.addCleanup(self.temp.cleanup)
        self.app=Assistant(self.temp.name,clock=lambda:NOW)
        self.fixture=ScriptedReview()
        self.board=Connected(self.app,self.fixture)
        self.board.initialize()
        self.counter=0

    def capture(self,title='Give feedback about the web assistant'):
        self.counter+=1
        proposal=self.board.request('task_add',{'title':title},f'outcome-{self.counter}')
        self.board.evaluate(proposal['id'])
        return next(p for p in self.board.snapshot()['proposals'] if p['id']==proposal['id'])

    def test_ungrounded_concern_is_reported_as_failed_review_code(self):
        self.fixture.output=review('review',[{'code':'contextual','message':'Possible duplicate.','refs':['user_request']}])
        proposal=self.capture()
        self.assertEqual(proposal['phase'],'unavailable')
        self.assertEqual(proposal['error_code'],'agent_invalid_evidence')
        # The diagnostic text stays for CLI audit; the code is what interfaces translate.
        self.assertEqual(proposal['error'],'The concern is not grounded in the supplied evidence.')
        self.assertNotIn('Give feedback about the web assistant',[r['description'] for r in self.app.tasks.rows()])

    def test_failure_codes_cover_unavailable_invalid_output_and_insufficient(self):
        self.fixture.error=AssistantError('agent_timeout','The model turn exceeded its time limit.')
        self.assertEqual(self.capture()['error_code'],'agent_timeout')
        self.fixture.error=None;self.fixture.output={'verdict':'allow'}
        self.assertEqual(self.capture()['error_code'],'agent_invalid_output')
        self.fixture.output=review('insufficient',message='Which project is this for?')
        proposal=self.capture()
        self.assertEqual((proposal['phase'],proposal['error_code']),('unavailable','review_insufficient'))
        self.assertIsNone(proposal['error'])

    def test_retry_clears_previous_failure_and_restart_reports_interruption(self):
        self.fixture.output=review('review',[{'code':'contextual','message':'Ungrounded.','refs':['tasks']}])
        proposal=self.capture()
        retried=self.board.retry(proposal['id'])
        current=next(p for p in self.board.snapshot()['proposals'] if p['id']==retried['id'])
        self.assertEqual((current['phase'],current['error_code']),('evaluating',None))
        self.board.startup()
        current=next(p for p in self.board.snapshot()['proposals'] if p['id']==retried['id'])
        self.assertEqual((current['phase'],current['error_code']),('unavailable','review_interrupted'))

    def test_changed_context_is_reported_as_stale_code(self):
        self.fixture.error=AssistantError('agent_unavailable','offline')
        proposal=self.capture()
        with self.app.store.connect() as con:
            con.execute("UPDATE proposals SET phase='stale',error='Changed by admitted proposal:other' WHERE id=?",(proposal['id'],))
        current=next(p for p in self.board.snapshot()['proposals'] if p['id']==proposal['id'])
        self.assertEqual(current['error_code'],'context_changed')

    def test_review_contract_limits_refs_to_supplied_evidence(self):
        self.fixture.output=review()
        proposal=self.capture()
        self.assertEqual(proposal['phase'],'applied')
        prompt,schema=self.fixture.calls[-1]
        supplied=json.loads(prompt[prompt.index('{'):])
        refs=schema['properties']['concerns']['items']['properties']['refs']['items']
        self.assertEqual(refs['enum'],sorted(supplied['evidence']))
        self.assertNotIn('enum',REVIEW_SCHEMA['properties']['concerns']['items']['properties']['refs']['items'])
        self.assertIn('Every concern ref must be an exact key of the evidence object',prompt)
        self.assertIn('user_request, intent, tasks, human_context, proposed_task',prompt)

    def test_context_notice_failure_and_repeated_retry_carry_codes(self):
        key='context-notice:test'
        inputs=self.board._input({'id':key,'key':key,'intent':{'action':'context_update','payload':{}},'generation':0,'origin':'ui','request_text':''})
        with self.app.store.connect() as con:
            con.execute("INSERT INTO messages(key,role,text,status,data,at) VALUES(?,'assistant','pending','pending',?,?)",
                        (key,json.dumps({'kind':'context_notice','input':{k:v for k,v in inputs.items() if k!='plan'}}),self.app.now()))
        self.fixture.error=AssistantError('agent_timeout','The model turn exceeded its time limit.')
        self.board.evaluate_notices()
        with self.app.store.connect() as con:
            data=json.loads(con.execute('SELECT data FROM messages WHERE key=?',(key,)).fetchone()[0])
        self.assertEqual(data['code'],'agent_timeout')
        chat=Conversation(self.board)
        with self.app.store.connect() as con:
            con.execute("INSERT INTO messages(key,role,text,status,at) VALUES('chat-1','user','Add it','incomplete',?)",(self.app.now(),))
            con.execute("INSERT INTO proposals(id,key,digest,intent,entity,origin,request_text,phase,generation,facts,created_at,message_key) VALUES('p-1','chat-action:chat-1','d','{}','e','chat','Add it','unavailable',0,'[]',?,'chat-1')",(self.app.now(),))
            con.execute("INSERT INTO messages(key,role,text,status,data,at) VALUES('chat-1:assistant','assistant','The response is incomplete.','incomplete','{}',?)",(self.app.now(),))
        chat.retry('chat-1')
        with self.app.store.connect() as con:
            data=json.loads(con.execute("SELECT data FROM messages WHERE key='chat-1:assistant'").fetchone()[0])
        self.assertEqual((data['notice'],data['proposal_ids']),('existing_proposals',['p-1']))


if __name__ == '__main__':
    unittest.main()
