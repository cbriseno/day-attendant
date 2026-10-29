"""Context occupancy is carried only as each harness reports it, never estimated."""
import json
import queue
import tempfile
import threading
import unittest

from day_attendant.codexbridge import CodexBridge
from day_attendant.connected import Connected
from day_attendant.conversation import Conversation
from day_attendant.errors import AssistantError
from day_attendant.harnesses import context_usage
from day_attendant.service import Assistant
from test_conversation import ChatFixture

SCHEMA={'type':'object','properties':{'message':{'type':'string'}},'required':['message'],'additionalProperties':False}


class ScriptedCodex(CodexBridge):
    """The real turn loop, fed with app-server notifications instead of a process."""
    def __init__(self,notifications):
        self.lock=threading.Lock();self.subscribers=[];self.closed=False;self.effort='low';self.model='controlled-model'
        self.notifications=notifications
    def call(self,method,params,timeout=20):
        if method=='turn/start':
            for listener in self.subscribers:
                for message in self.notifications:listener.put(message)
            return {'turn':{'id':'turn-1'}}
        return {}


def usage_report(thread='thread-1',total=9706,window=475000):
    return {'method':'thread/tokenUsage/updated','params':{'threadId':thread,'turnId':'turn-1','tokenUsage':{
        'total':{'totalTokens':total*3,'inputTokens':total*3,'cachedInputTokens':0,'outputTokens':0,'reasoningOutputTokens':0},
        'last':{'totalTokens':total,'inputTokens':total-16,'cachedInputTokens':0,'outputTokens':16,'reasoningOutputTokens':0},
        'modelContextWindow':window}}}


def finished(text='{"message":"Hi"}'):
    return [{'method':'item/completed','params':{'threadId':'thread-1','turnId':'turn-1','item':{'type':'agentMessage','text':text}}},
            {'method':'turn/completed','params':{'threadId':'thread-1','turn':{'id':'turn-1','status':'completed'}}}]


class HarnessUsage(unittest.TestCase):
    def test_only_reported_nonnegative_counts_are_kept(self):
        self.assertEqual(context_usage(1200,200000),{'contextTokens':1200,'contextWindow':200000})
        self.assertEqual(context_usage(0,200000),{'contextTokens':0,'contextWindow':200000})
        for window in (None,0,-1,True,'200000'):self.assertEqual(context_usage(5,window),{'contextTokens':5,'contextWindow':None})
        for used in (None,-1,True,1.5,'5'):self.assertIsNone(context_usage(used,200000))

    def test_codex_turn_returns_its_latest_request_usage(self):
        bridge=ScriptedCodex([usage_report(thread='other-thread',total=1),usage_report()]+finished())
        self.assertEqual(bridge.turn('thread-1','Hello',SCHEMA)['usage'],{'contextTokens':9706,'contextWindow':475000})
        bridge=ScriptedCodex([usage_report(window=None)]+finished())
        self.assertEqual(bridge.turn('thread-1','Hello',SCHEMA)['usage'],{'contextTokens':9706,'contextWindow':None})
        self.assertIsNone(ScriptedCodex(finished()).turn('thread-1','Hello',SCHEMA)['usage'])
        malformed={'method':'thread/tokenUsage/updated','params':{'threadId':'thread-1','turnId':'turn-1','tokenUsage':{'last':'unexpected'}}}
        self.assertIsNone(ScriptedCodex([malformed]+finished()).turn('thread-1','Hello',SCHEMA)['usage'])


class UsageFixture(ChatFixture):
    def __init__(self,output,usage):super().__init__(output);self.usage=usage
    def turn(self,thread,prompt,schema,**kwargs):
        result=super().turn(thread,prompt,schema,**kwargs)
        return dict(result,usage=self.usage) if 'intent' in schema['properties'] else result


class FailingFixture(ChatFixture):
    def turn(self,thread,prompt,schema,**kwargs):raise AssistantError('agent_error','Controlled quota failure')


class ConversationUsage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='day-context-status-');self.app=Assistant(self.temp.name)
        self.board=Connected(self.app);self.board.initialize();self.chat=Conversation(self.board)
    def tearDown(self):self.temp.cleanup()
    def reply(self,key):
        message=next(m for m in self.board.snapshot()['messages'] if m['key']==key+':assistant')
        return message,json.loads(message['data'])

    def test_reply_keeps_reported_usage_with_its_provider_and_model(self):
        usage={'contextTokens':1234,'contextWindow':200000}
        self.board.bridge=UsageFixture({'message':'Noted.','uncertainties':[],'intent':None},usage)
        self.chat.submit('How is my day?','usage');self.chat.run('usage')
        message,data=self.reply('usage')
        self.assertEqual(message['status'],'completed');self.assertEqual(data['usage'],usage)
        self.assertEqual((data['provider'],data['model']),('controlled','controlled-fixture'))

    def test_unreported_or_failed_turn_stores_no_usage(self):
        self.board.bridge=ChatFixture({'message':'Noted.','uncertainties':[],'intent':None})
        self.chat.submit('How is my day?','plain');self.chat.run('plain')
        self.assertIsNone(self.reply('plain')[1]['usage'])
        self.board.bridge=FailingFixture({'message':'unused','uncertainties':[],'intent':None})
        self.chat.submit('And now?','failed');self.chat.run('failed')
        message,data=self.reply('failed')
        self.assertEqual(message['status'],'incomplete');self.assertNotIn('usage',data)


if __name__=='__main__':unittest.main()
