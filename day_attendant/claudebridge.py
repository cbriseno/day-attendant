"""Bounded authenticated Claude CLI adapter, with no external tools or MCP."""
import base64
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import threading
import time
import uuid

from .errors import AssistantError
from .profiles import CLAUDE_EFFORTS,DEFAULT_EFFORT

# Shared behavioral contract, independent of either native transport.
from .instructions import PERSONAL_ASSISTANT


def subscription_environment():
    env=dict(os.environ)
    # Never let an inherited API/provider setting redirect subscription usage.
    for name in ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','ANTHROPIC_BASE_URL',
                 'CLAUDE_CODE_USE_BEDROCK','CLAUDE_CODE_USE_VERTEX','CLAUDE_CODE_USE_FOUNDRY',
                 'ANTHROPIC_BEDROCK_BASE_URL','CLAUDE_CODE_API_KEY_HELPER_TTL_MS'):
        env.pop(name,None)
    return env


class ClaudeBridge:
    provider='claude'

    def __init__(self,workspace,model=None,binary=None,effort=None):
        self.workspace=str(Path(workspace).resolve())
        self.effort=effort or DEFAULT_EFFORT
        if self.effort not in CLAUDE_EFFORTS:raise AssistantError('agent_effort_unavailable','Claude Code does not offer this reasoning level; no other level was selected.')
        self.catalog=None
        self.binary=binary or shutil.which('claude')
        if not self.binary:raise AssistantError('agent_missing','Claude Code is not installed.')
        self.environment=subscription_environment()
        self.model=model
        self.closed=False
        self.lock=threading.Lock()
        self.processes=set()
        self.events=[]
        try:
            auth=subprocess.run([self.binary,'--safe-mode','auth','status'],env=self.environment,
                cwd=self.workspace,capture_output=True,text=True,timeout=20)
            account=json.loads(auth.stdout)
        except (OSError,ValueError,subprocess.TimeoutExpired) as error:
            raise AssistantError('agent_auth_required','Cannot verify the existing Claude subscription login.') from error
        if auth.returncode or not account.get('loggedIn') or account.get('authMethod') not in ('claude.ai','oauth_token') or account.get('apiProvider')!='firstParty':
            raise AssistantError('agent_auth_required','An existing first-party Claude subscription login is required; API fallback is disabled.')
        self.authentication=account['authMethod']
        self.server_version=subprocess.check_output([self.binary,'--version'],env=self.environment,text=True,timeout=10).strip()
        help_text=subprocess.check_output([self.binary,'--help'],env=self.environment,text=True,timeout=10)
        if any(flag not in help_text for flag in ('--safe-mode','--restricted','--tools','--json-schema','--permission-prompts','--no-session-persistence')):
            raise AssistantError('agent_version_unsupported','Claude Code must support restricted safe-mode structured print sessions.')

    @property
    def available(self):return not self.closed

    def thread(self,previous=None):
        # Portable conversation is supplied by Day Attendant, not global CLI history.
        return previous or str(uuid.uuid4())

    def command(self,schema):
        args=[self.binary,'--safe-mode','--restricted','--tools','',
              '--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
              '--disable-slash-commands','--no-chrome','--permission-prompts','none',
              '--permission-mode','dontAsk','--setting-sources','',
              '--no-session-persistence','--system-prompt-snapshot','off',
              '--effort',self.effort,'--system-prompt',PERSONAL_ASSISTANT,
              '-p','--input-format','stream-json','--output-format','stream-json',
              '--verbose','--json-schema',json.dumps(schema)]
        if self.model:args.extend(['--model',self.model])
        return args

    @staticmethod
    def stop(process):
        if process.poll() is not None:return
        try:
            if os.name=='posix':os.killpg(process.pid,signal.SIGTERM)
            else:process.terminate()
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name=='posix':os.killpg(process.pid,signal.SIGKILL)
            else:process.kill()
            process.wait(timeout=3)
        except ProcessLookupError:pass

    def turn(self,thread_id,prompt,schema,timeout=120,canceled=None,started=None,images=None):
        if self.closed:raise AssistantError('agent_disconnected','The Claude adapter is closed.')
        content=[{'type':'text','text':prompt}]
        for selected in images or []:
            path=Path(selected['path'])
            data=path.read_bytes()
            content.append({'type':'image','source':{'type':'base64','media_type':selected['mime'],'data':base64.b64encode(data).decode()}})
        record={'type':'user','message':{'role':'user','content':content}}
        begin=time.monotonic();process=None;readers=[]
        try:
            process=subprocess.Popen(self.command(schema),cwd=self.workspace,env=self.environment,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                start_new_session=os.name=='posix')
            with self.lock:self.processes.add(process)
            stream=queue.Queue();errors=[]
            def consume():
                try:
                    for line in process.stdout:
                        if len(line)>4_000_000:raise ValueError('Oversized Claude event')
                        stream.put(json.loads(line))
                except Exception as error:stream.put(error)
                finally:stream.put(None)
            def stderr():
                for line in process.stderr:
                    errors.append(line)
                    if len(errors)>20:errors.pop(0)
            readers=[threading.Thread(target=consume,daemon=True),threading.Thread(target=stderr,daemon=True)]
            for reader in readers:reader.start()
            process.stdin.write(json.dumps(record)+'\n');process.stdin.close()
            if started:started(str(process.pid))
            initialized=False;result=None
            while time.monotonic()-begin<timeout:
                if self.closed or canceled is not None and canceled.is_set():
                    raise AssistantError('agent_interrupted','The evaluation was canceled.')
                try:event=stream.get(timeout=.2)
                except queue.Empty:continue
                if event is None:break
                if isinstance(event,Exception):raise AssistantError('agent_invalid_output','Invalid Claude event stream.') from event
                if event.get('type')=='system' and event.get('subtype')=='init':
                    external_plugins=[p for p in event.get('plugins',[]) if p.get('path')!='builtin' or not str(p.get('source','')).endswith('@builtin')]
                    if set(event.get('tools',[]))-{'StructuredOutput'} or event.get('mcp_servers') or external_plugins:
                        raise AssistantError('agent_isolation_unverified','Claude exposed an unexpected external capability.')
                    if str(Path(event.get('cwd','')).resolve())!=self.workspace:
                        raise AssistantError('agent_workspace_mismatch','Claude used a different workspace.')
                    actual=event.get('model')
                    if not actual:raise AssistantError('agent_invalid_output','Claude did not identify its model.')
                    if self.model and actual!=self.model:
                        raise AssistantError('agent_model_changed','Claude resolved a different model; select it explicitly before continuing.')
                    self.model=actual;initialized=True
                if event.get('type')=='assistant':
                    for item in event.get('message',{}).get('content',[]):
                        if item.get('type')=='tool_use' and item.get('name')!='StructuredOutput':
                            raise AssistantError('agent_tool_forbidden','External agent tools are unavailable in this workflow.')
                if event.get('type')=='result':result=event
            if result is None:
                code='agent_timeout' if time.monotonic()-begin>=timeout else 'agent_invalid_output'
                raise AssistantError(code,'Claude did not produce a complete structured result.')
            process.wait(timeout=5)
            if not initialized or process.returncode or result.get('is_error') or result.get('subtype')!='success' or result.get('terminal_reason','completed')!='completed':
                message=str(result.get('result') or result.get('errors') or 'Claude turn failed.')[:400]
                raise AssistantError('agent_error',message,{'provider':'claude','terminal_reason':result.get('terminal_reason'),'api_error_status':result.get('api_error_status')})
            output=result.get('structured_output')
            if not isinstance(output,dict):raise AssistantError('agent_invalid_output','Claude returned no structured object.')
            return {'output':output,'model':self.model,'provider':self.provider,'thread_id':thread_id,
                    'turn_id':result.get('uuid') or str(uuid.uuid4()),'seconds':round(time.monotonic()-begin,3),'status':'completed'}
        except (OSError,subprocess.TimeoutExpired) as error:
            raise AssistantError('agent_disconnected','The Claude process did not complete.') from error
        finally:
            if process:
                self.stop(process)
                with self.lock:self.processes.discard(process)
                for reader in readers:reader.join(timeout=1)
                for handle in (process.stdin,process.stdout,process.stderr):
                    if handle:handle.close()

    def close(self):
        self.closed=True
        with self.lock:processes=list(self.processes)
        for process in processes:self.stop(process)
