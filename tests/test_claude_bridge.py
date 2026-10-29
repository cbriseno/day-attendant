"""Execute an isolated fake native CLI to check transport failure boundaries."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from day_attendant.claudebridge import ClaudeBridge
from day_attendant.errors import AssistantError

SCHEMA={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}

class ClaudeTransport(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='day-claude-contract-');self.folder=Path(self.temp.name);self.bin=self.folder/'claude';self.control=self.folder/'control.json';self.control.write_text('{}')
        self.bin.write_text('''#!/usr/bin/env python3
import json,os,sys,time
from pathlib import Path
control=json.loads((Path(__file__).parent/'control.json').read_text())
if 'auth' in sys.argv:
 print(json.dumps({'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty'}));sys.exit(0)
if '--version' in sys.argv:print('2.1.312 (Claude Code)');sys.exit(0)
if '--help' in sys.argv:print('--safe-mode --restricted --tools --json-schema --permission-prompts --no-session-persistence');sys.exit(0)
request=json.loads(sys.stdin.readline());(Path(__file__).parent/'request.json').write_text(json.dumps({'args':sys.argv,'input':request,'environment':{k:v for k,v in os.environ.items() if k.startswith('ANTHROPIC_')}}))
print(json.dumps({'type':'system','subtype':'init','cwd':os.getcwd(),'tools':control.get('tools',['StructuredOutput']),'mcp_servers':control.get('mcp',[]),'plugins':[],'model':control.get('model','exact-model')}),flush=True)
if control.get('hang'):time.sleep(30)
if control.get('external'):
 print(json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use','name':'Bash'}]}}),flush=True)
if control.get('malformed'):print('not-json',flush=True);sys.exit(0)
result={'type':'result','subtype':'success','is_error':False,'structured_output':{'answer':'contract-ok'},'uuid':'turn-id'}
result.update(control.get('result',{}));print(json.dumps(result),flush=True)
''');self.bin.chmod(0o700);self.bridge=ClaudeBridge(self.folder,binary=str(self.bin))
    def tearDown(self):self.bridge.close();self.temp.cleanup()
    def control_as(self,**values):self.control.write_text(json.dumps(values))
    def turn(self,**kwargs):return self.bridge.turn('logical-thread','Hello',SCHEMA,**kwargs)
    def test_only_terminal_structured_success_is_accepted(self):
        self.assertEqual(self.turn()['output'],{'answer':'contract-ok'})
        for result in [{'is_error':True,'result':'Quota exhausted'},{'structured_output':None},{'terminal_reason':'max_turns'},{'subtype':'error_max_turns'}]:
            self.control_as(result=result)
            with self.assertRaises(AssistantError):self.turn()
    def test_context_usage_is_the_last_reported_request_and_model_window(self):
        self.assertIsNone(self.turn()['usage'])
        iterations=[{'input_tokens':5,'output_tokens':1,'cache_read_input_tokens':0,'cache_creation_input_tokens':0},
                    {'input_tokens':2,'output_tokens':30,'cache_read_input_tokens':900,'cache_creation_input_tokens':68}]
        usage={'input_tokens':7,'output_tokens':31,'iterations':iterations}
        self.control_as(result={'usage':usage,'modelUsage':{'exact-model':{'contextWindow':200000}}})
        self.assertEqual(self.turn()['usage'],{'contextTokens':1000,'contextWindow':200000})
        # Another model's window or a zero window is not this session's window.
        self.control_as(result={'usage':usage,'modelUsage':{'other-model':{'contextWindow':200000}}})
        self.assertEqual(self.turn()['usage'],{'contextTokens':1000,'contextWindow':None})
        self.control_as(result={'usage':{'input_tokens':10,'output_tokens':2},'modelUsage':{'exact-model':{'contextWindow':0}}})
        self.assertEqual(self.turn()['usage'],{'contextTokens':12,'contextWindow':None})
        for usage in ({'output_tokens':2},{'iterations':['unexpected']},'unexpected'):
            self.control_as(result={'usage':usage,'modelUsage':{'exact-model':{'contextWindow':200000}}})
            self.assertIsNone(self.turn()['usage'])
    def test_unexpected_native_capability_and_model_change_are_rejected(self):
        for control in [{'tools':['StructuredOutput','Bash']},{'mcp':[{'name':'external'}]},{'external':True},{'model':'different-model'},{'malformed':True}]:
            self.control_as(**control)
            if control.get('model'):self.bridge.model='exact-model'
            with self.assertRaises(AssistantError):self.turn()
    def test_cancellation_terminates_only_its_owned_process(self):
        self.control_as(hang=True);canceled=threading.Event()
        with self.assertRaises(AssistantError) as error:self.turn(canceled=canceled,started=lambda _:canceled.set())
        self.assertEqual(error.exception.code,'agent_interrupted');self.assertEqual(self.bridge.processes,set())
    def test_image_input_and_subscription_environment_are_child_local(self):
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'TEST-NOT-A-KEY','ANTHROPIC_BASE_URL':'https://invalid.example'}):
            self.bridge.close();self.bridge=ClaudeBridge(self.folder,binary=str(self.bin));image=self.folder/'fixture.png';image.write_bytes(b'fixture-raster')
            self.turn(images=[{'path':str(image),'mime':'image/png'}]);request=json.loads((self.folder/'request.json').read_text())
            self.assertEqual(request['environment'],{});self.assertEqual(os.environ['ANTHROPIC_API_KEY'],'TEST-NOT-A-KEY')
            self.assertEqual(request['input']['message']['content'][1]['source']['data'],'Zml4dHVyZS1yYXN0ZXI=')
            self.assertEqual(request['args'][request['args'].index('--tools')+1],'')

if __name__=='__main__':unittest.main()
