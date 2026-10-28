"""Selected reasoning level: typed settings and the exact native requests each harness receives."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from day_attendant.claudebridge import ClaudeBridge
from day_attendant.codexbridge import CodexBridge
from day_attendant.connected import Connected
from day_attendant.errors import AssistantError
from day_attendant.profiles import configure
from day_attendant.service import Assistant
from test_claude_bridge import SCHEMA

FAKE_CLAUDE='''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
if 'auth' in sys.argv:print(json.dumps({'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty'}));sys.exit(0)
if '--version' in sys.argv:print('2.1.312 (Claude Code)');sys.exit(0)
if '--help' in sys.argv:print('--safe-mode --restricted --tools --json-schema --permission-prompts --no-session-persistence');sys.exit(0)
sys.stdin.readline();(Path(__file__).parent/'args.json').write_text(json.dumps(sys.argv))
print(json.dumps({'type':'system','subtype':'init','cwd':os.getcwd(),'tools':['StructuredOutput'],'mcp_servers':[],'plugins':[],'model':'exact-model'}),flush=True)
print(json.dumps({'type':'result','subtype':'success','is_error':False,'structured_output':{'answer':'ok'},'uuid':'turn'}),flush=True)
'''

# A minimal app-server speaking the same JSON-RPC lines; it records every request.
FAKE_CODEX='''#!/usr/bin/env python3
import json,sys
from pathlib import Path
if '--version' in sys.argv:print('codex-cli 0.164.0');sys.exit(0)
log=Path(__file__).parent/'requests.jsonl'
features={k:False for k in ('shell_tool','apps','plugins','hooks','multi_agent','multi_agent_v2','memories','browser_use','browser_use_external','computer_use','image_generation','view_image','skill_search','goals','sleep_tool')}
features['skip_host_skill_discovery']=True
config={'features':features,'web_search':'disabled','project_doc_max_bytes':0,'notify':[],'mcp_servers':{},'memories':{'use_memories':False,'generate_memories':False}}
catalog=[{'id':'fixture-model','model':'fixture-model','isDefault':True,'defaultReasoningEffort':'high',
          'supportedReasoningEfforts':[{'reasoningEffort':'low','description':''},{'reasoningEffort':'high','description':''}]}]
def send(message):sys.stdout.write(json.dumps(message)+"\\n");sys.stdout.flush()
threads=0
for line in sys.stdin:
    request=json.loads(line)
    with log.open('a') as f:f.write(json.dumps(request)+"\\n")
    method,params=request.get('method'),request.get('params',{})
    if 'id' not in request:continue
    if method=='account/read':result={'account':{'type':'chatgpt'}}
    elif method=='model/list':result={'data':catalog}
    elif method=='config/read':result={'config':config}
    elif method=='thread/start':
        threads+=1;result={'thread':{'id':'thread-'+str(threads),'cwd':params['cwd']}}
    elif method=='turn/start':
        result={'turn':{'id':'turn-1'}};send({'id':request['id'],'result':result})
        send({'method':'item/completed','params':{'threadId':params['threadId'],'turnId':'turn-1','item':{'type':'agentMessage','text':json.dumps({'answer':'ok'})}}})
        send({'method':'turn/completed','params':{'threadId':params['threadId'],'turn':{'id':'turn-1','status':'completed'}}})
        continue
    else:result={}
    send({'id':request['id'],'result':result})
'''


class ProfileEffort(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='day-effort-');self.app=Assistant(self.temp.name)
        Connected(self.app).initialize(kind='personal',zone='America/Edmonton')
    def tearDown(self):self.temp.cleanup()
    def profile(self):return self.app.store.state_value('profile')

    def test_effort_is_typed_per_harness_and_restarts_reasoning(self):
        changed=configure(self.app,{'effort':'minimal'},'codex-minimal')
        self.assertTrue(changed['engine_changed']);self.assertEqual(self.profile()['effort'],'minimal')
        # Claude Code does not offer minimal: an unrequested level resets, a requested one is refused.
        configure(self.app,{'harness':'claude'},'to-claude');self.assertIsNone(self.profile()['effort'])
        with self.assertRaises(AssistantError) as error:configure(self.app,{'effort':'minimal'},'claude-minimal')
        self.assertEqual(error.exception.code,'invalid_settings')
        self.assertEqual(configure(self.app,{'effort':'max'},'claude-max')['profile']['effort'],'max')
        configure(self.app,{'harness':'codex','effort':'high'},'back-to-codex');self.assertEqual(self.profile()['effort'],'high')
        self.assertFalse(configure(self.app,{'effort':'high'},'same-level')['engine_changed'])


class NativeEffort(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='day-native-effort-');self.folder=Path(self.temp.name)
        for name,script in (('claude',FAKE_CLAUDE),('codex',FAKE_CODEX)):
            path=self.folder/name;path.write_text(script);path.chmod(0o700)
        self.bridges=[]
        self.env=patch.dict(os.environ,{'CODEX_HOME':str(self.folder/'codex-home')});self.env.start()
    def tearDown(self):
        for bridge in self.bridges:bridge.close()
        self.env.stop();self.temp.cleanup()
    def claude(self,**kwargs):
        bridge=ClaudeBridge(self.folder,binary=str(self.folder/'claude'),**kwargs);self.bridges.append(bridge);return bridge
    def codex(self,**kwargs):
        bridge=CodexBridge(self.folder,binary=str(self.folder/'codex'),**kwargs);self.bridges.append(bridge);return bridge
    def codex_requests(self,method):
        return [r['params'] for r in map(json.loads,(self.folder/'requests.jsonl').read_text().splitlines()) if r.get('method')==method]

    def test_claude_receives_the_selected_level(self):
        self.claude(effort='xhigh').turn('logical','Hello',SCHEMA)
        args=json.loads((self.folder/'args.json').read_text())
        self.assertEqual(args[args.index('--effort')+1],'xhigh')
        self.claude().turn('logical','Hello',SCHEMA)
        args=json.loads((self.folder/'args.json').read_text());self.assertEqual(args[args.index('--effort')+1],'medium')
        with self.assertRaises(AssistantError) as error:self.claude(effort='minimal')
        self.assertEqual(error.exception.code,'agent_effort_unavailable')

    def test_codex_uses_catalog_levels_and_ephemeral_threads(self):
        bridge=self.codex(effort='low')
        self.assertEqual(bridge.catalog,[{'model':'fixture-model','isDefault':True,'efforts':['low','high'],'defaultEffort':'high'}])
        for _ in range(2):self.assertEqual(bridge.turn(bridge.thread(),'Hello',SCHEMA)['output'],{'answer':'ok'})
        self.assertEqual([p['effort'] for p in self.codex_requests('turn/start')],['low','low'])
        self.assertEqual([p['ephemeral'] for p in self.codex_requests('thread/start')],[True,True])
        self.assertEqual(self.codex_requests('thread/resume'),[])
        # An unset level keeps medium only when offered; otherwise the model's default.
        self.assertEqual(self.codex().effort,'high')
        with self.assertRaises(AssistantError) as error:self.codex(effort='xhigh')
        self.assertEqual(error.exception.code,'agent_effort_unavailable')


if __name__=='__main__':unittest.main()
