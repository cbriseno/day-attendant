"""Waiting changes and the later review carry stable outcome codes, so interfaces never show internal errors."""
import json
import tempfile
import unittest

from day_attendant.connected import FOLLOWUP_ACTIONS, Connected
from day_attendant.conversation import Conversation
from day_attendant.errors import AssistantError
from day_attendant.service import Assistant, encoded, fingerprint
from test_connected import NOW


class ScriptedReview:
    model='controlled-fixture'
    available=True

    def __init__(self):
        self.output={'suggestions':[]};self.error=None;self.calls=[]

    def thread(self,previous=None):return 'controlled-thread'

    def turn(self,thread,prompt,schema,**kwargs):
        self.calls.append((prompt,schema))
        if self.error:raise self.error
        return {'output':self.output,'model':self.model,'thread_id':thread,'turn_id':'controlled-turn','seconds':0,'status':'completed'}


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
        self.assertEqual(proposal['phase'],'applied')
        self.board.review_changes()
        return self.board.snapshot()['review']

    def legacy(self,phase,payload=None):
        """A proposal that a service from before instant actions left waiting for its review."""
        self.counter+=1;identity=f'00000000-0000-4000-8000-{self.counter:012d}'
        intent={'action':'task_add','payload':payload or {'title':'Left waiting by an older service'}}
        with self.app.store.connect() as con:
            con.execute("INSERT INTO proposals(id,key,digest,intent,entity,origin,request_text,phase,generation,fingerprint,facts,created_at) VALUES(?,?,?,?,'new:legacy','ui','',?,0,'legacy','[]',?)",
                        (identity,f'legacy-{self.counter}',fingerprint(intent),encoded(intent),phase,self.app.now()))
        return identity

    def shown(self,identity):
        return next(p for p in self.board.snapshot()['proposals'] if p['id']==identity)

    def test_failed_later_review_reports_its_code_and_keeps_the_change(self):
        self.fixture.error=AssistantError('agent_timeout','The model turn exceeded its time limit.')
        review=self.capture()
        self.assertEqual((review['status'],review['code'],review['pending']),('incomplete','agent_timeout',0))
        self.assertIn('Give feedback about the web assistant',[r['description'] for r in self.app.tasks.rows()])
        self.fixture.error=None;self.fixture.output={'verdict':'allow'}
        self.assertEqual(self.capture('Second capture')['code'],'agent_invalid_output')
        self.assertEqual(len(self.app.tasks.rows()),6)

    def test_restart_marks_an_interrupted_review_without_losing_its_changes(self):
        self.board.request('task_add',{'title':'Saved before the restart'},'before-restart')
        with self.app.store.connect() as con:con.execute("INSERT INTO followups(through,status,at) VALUES(1,'running',?)",(self.app.now(),))
        self.board.startup()
        review=self.board.snapshot()['review']
        self.assertEqual((review['status'],review['pending']),('interrupted',1))
        self.board.review_changes()
        self.assertEqual(self.board.snapshot()['review']['pending'],0)

    def test_legacy_waiting_change_is_interrupted_then_applies_on_retry(self):
        identity=self.legacy('evaluating')
        self.board.startup()
        current=self.shown(identity)
        self.assertEqual((current['phase'],current['error_code']),('unavailable','review_interrupted'))
        self.assertEqual(self.board.retry(identity)['phase'],'applied')
        self.assertIn('Left waiting by an older service',[r['description'] for r in self.app.tasks.rows()])

    def test_changed_context_is_reported_as_stale_code(self):
        identity=self.legacy('stale')
        self.assertEqual(self.shown(identity)['error_code'],'context_changed')

    def test_followup_contract_names_supplied_tasks_and_direct_changes_only(self):
        self.capture()
        prompt,schema=self.fixture.calls[-1]
        supplied=json.loads(prompt[prompt.index('{'):])
        item=schema['properties']['suggestions']['items']['properties']
        self.assertEqual(item['taskId']['enum'],supplied['suggest_for'])
        self.assertEqual(len(supplied['suggest_for']),1)
        self.assertLess(set(supplied['suggest_for']),{t['id'] for t in supplied['tasks']})
        self.assertEqual(item['action']['anyOf'][1]['properties']['action']['enum'],list(FOLLOWUP_ACTIONS))
        self.assertEqual(schema['properties']['suggestions']['maxItems'],3)
        self.assertIn('you cannot block, apply or undo anything',prompt)
        self.assertEqual(set(supplied),{'now','timezone','changes','tasks','suggest_for','closed','context','calendar_events','noted','already_suggested'})
        self.assertEqual([c['action'] for c in supplied['changes']],['task_add'])
        self.assertNotIn('reads',json.dumps(supplied['changes']))

    def test_chat_retry_with_existing_proposals_carries_their_codes(self):
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
