"""Explicitly started loopback UI for a selected personal or synthetic profile."""
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import sys
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .archives import upgrade_tasks
from .harnesses import create_harness,discover_catalog
from .connected import Connected, FOLLOWUP_DELAY
from .conversation import Conversation
from .errors import AssistantError
from .service import Assistant, encoded
from .platforms import exclusive
from .profiles import configure

ASSETS = Path(__file__).resolve().parent / "ui"


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app, bridge, port=0, token=None):
        self.board=Connected(app,bridge)
        self.chat=Conversation(self.board)
        self.pool=ThreadPoolExecutor(max_workers=3,thread_name_prefix="day-request")
        self.token=token or secrets.token_urlsafe(32)
        self.harness_lock=threading.Lock()
        self.catalogs={}
        self.review_lock=threading.Lock()
        self.review_timer=None
        super().__init__(("127.0.0.1",port),Handler)
        self.origin=f"http://127.0.0.1:{self.server_port}"

    def schedule(self, result=None):
        """Changes apply in their own request; the assistant looks at them once activity pauses."""
        def review():
            try:self.pool.submit(self.board.review_changes)
            except RuntimeError:pass  # The service is stopping.
        with self.review_lock:
            if self.review_timer:self.review_timer.cancel()
            self.review_timer=threading.Timer(FOLLOWUP_DELAY,review)
            self.review_timer.daemon=True
            self.review_timer.start()

    def run_chat(self, key):
        try:self.chat.run(key)
        finally:self.schedule()

    def server_close(self):
        super().server_close()
        with self.review_lock:
            if self.review_timer:self.review_timer.cancel()
        self.pool.shutdown(wait=False,cancel_futures=True)

    def models(self):
        """Each harness's own model list, discovered once per service and never guessed."""
        with self.harness_lock:
            bridge=self.board.bridge;result={'errors':{}}
            for provider in ('codex','claude'):
                if bridge and getattr(bridge,'provider',None)==provider and getattr(bridge,'catalog',None):self.catalogs[provider]=bridge.catalog
                if provider not in self.catalogs:
                    workspace=self.board.app.store.path/'agent-workspace';workspace.mkdir(mode=0o700,exist_ok=True)
                    try:self.catalogs[provider]=discover_catalog(provider,workspace)
                    except (AssistantError,OSError,ValueError) as error:result['errors'][provider]=getattr(error,'code','agent_unavailable')
                result[provider]=self.catalogs.get(provider,[])
            return result

    def configure_harness(self):
        with self.harness_lock:
            app=self.board.app;profile=app.store.state_value('profile');epoch=app.store.epoch()
            old=self.board.bridge;self.board.bridge=None
            if old:old.close()
            workspace=app.store.path/'agent-workspace';workspace.mkdir(mode=0o700,exist_ok=True)
            bridge=None
            try:
                bridge=create_harness(profile['harness'],workspace,profile.get('model'),profile.get('effort'))
                with app.store.lock(),app.store.connect() as con:
                    current=app.store.state_value('profile')
                    if app.store.epoch()!=epoch or any(current.get(k)!=profile.get(k) for k in ('harness','model','effort')):
                        bridge.close();return
                    if bridge.model and not current.get('model'):
                        current['model']=bridge.model;con.execute("INSERT OR REPLACE INTO state VALUES('profile',?)",(encoded(current),))
                    con.execute("DELETE FROM state WHERE key='agent_startup_error'")
                self.board.bridge=bridge
            except (AssistantError,OSError,ValueError) as error:
                if bridge:bridge.close()
                with app.store.lock(),app.store.connect() as con:con.execute("INSERT OR REPLACE INTO state VALUES('agent_startup_error',?)",(encoded({'code':getattr(error,'code',None),'message':str(error)[:400]}),))


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass  # Never log session fragments, headers or message contents.

    def reply(self,status,data,content_type="application/json; charset=utf-8",cookie=None):
        body=encoded(data).encode() if content_type.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass

    def host_valid(self):
        if self.headers.get('Host')!=self.server.origin.removeprefix('http://'):
            self.reply(403,{'error':{'code':'host_forbidden','message':'Invalid local origin.'}})
            return False
        return True

    def authenticated(self):
        bearer=self.headers.get('Authorization','')
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:return False
        candidate=bearer[7:] if bearer.startswith('Bearer ') else cookie['day_session'].value if 'day_session' in cookie else ''
        return bool(candidate) and hmac.compare_digest(candidate,self.server.token)

    def do_GET(self):
        if not self.host_valid():return
        if self.path in ('/','/index.html'):
            self.reply(200,(ASSETS/'index.html').read_bytes(),'text/html; charset=utf-8');return
        if self.path=='/favicon.ico':
            self.reply(204,b'', 'image/x-icon');return
        if self.path in ('/interface.js','/locales.js','/daily.js','/ordering.js'):
            self.reply(200,(ASSETS/self.path[1:]).read_bytes(),'text/javascript; charset=utf-8');return
        if not self.authenticated():
            self.reply(401,{'error':{'code':'session_required','message':'Open the local link for this session.'}});return
        if match:=re.fullmatch(r'/api/attachments/([a-f0-9-]+)',self.path):
            try:
                from .attachments import load
                data,mime=load(self.server.board.app,match[1]);self.reply(200,data,mime)
            except AssistantError as error:self.reply(404,{'error':{'code':error.code,'message':str(error)}})
            return
        route,_,query=self.path.partition('?')
        if route=='/api/state' or self.path in ('/api/doctor','/api/backups','/api/models'):
            try:
                if self.path=='/api/doctor':data=self.server.board.app.doctor()
                elif self.path=='/api/models':data=self.server.models()
                elif self.path=='/api/backups':
                    from .archives import list_backups
                    data=list_backups(self.server.board.app)
                else:
                    options=parse_qs(query)
                    if options.get('view')==['full']:data=self.server.board.snapshot()
                    else:
                        data=self.server.board.view()
                        # A poll that already holds this content receives only the clock.
                        if options.get('since')==[data['tag']]:data={'unchanged':True,'tag':data['tag'],'now':data['now']}
                self.reply(200,{'data':data})
            except AssistantError as error:self.reply(409,{'error':{'code':error.code,'message':str(error)}})
            return
        if route=='/api/zone-rules':
            try:self.reply(200,{'data':self.server.board.zone_year(int(parse_qs(query)['year'][0]))})
            except (KeyError,ValueError):self.reply(400,{'error':{'code':'invalid_time','message':'Choose a calendar year.'}})
            except AssistantError as error:self.reply(400,{'error':{'code':error.code,'message':str(error)}})
            return
        self.reply(404,{'error':{'code':'not_found','message':'Route unavailable.'}})

    def do_POST(self):
        if not self.host_valid():return
        if self.headers.get('Origin')!=self.server.origin or not self.headers.get('Content-Type','').startswith('application/json'):
            self.reply(403,{'error':{'code':'origin_forbidden','message':'The request must originate from this local application.'}});return
        session=self.path=='/api/session'
        if not session and not self.authenticated():
            self.reply(401,{'error':{'code':'session_required','message':'Reopen the session link.'}});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            # Only the session exchange reads an unauthenticated body; authenticated messages and images are not product-capped.
            if size<=0 or session and size>4096:raise ValueError('size')
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict):raise ValueError('object')
        except (ValueError,TypeError):
            self.reply(400,{'error':{'code':'invalid_json','message':'Invalid request.'}});return
        if session:
            token=body.get('token','')
            if not isinstance(token,str) or not hmac.compare_digest(token,self.server.token):
                self.reply(401,{'error':{'code':'session_required','message':'Invalid session link.'}});return
            self.reply(200,{'data':{'authenticated':True}},cookie='day_session='+self.server.token+'; HttpOnly; SameSite=Strict; Path=/');return
        board=self.server.board
        review=False  # The later review is scheduled after the reply, never before it.
        try:
            if self.path=='/api/attachments':
                if set(body)!={'name','mime','base64','key'}:raise ValueError('fields')
                from .attachments import save
                result=save(board.app,body['name'],body['mime'],body['base64'],body['key'])
            elif self.path=='/api/proposals':
                if set(body)-{'action','payload','key','expected_revision'}:raise ValueError('fields')
                result=board.announced(board.request(body['action'],body['payload'],body['key'],origin='cli' if self.headers.get('Authorization','').startswith('Bearer ') else 'ui',expected_revision=body.get('expected_revision')))
                review=True
            elif match:=re.fullmatch(r'/api/proposals/([a-f0-9-]+)/(?P<action>confirm|cancel|retry)',self.path):
                identity=match[1];action=match['action']
                if action=='confirm':result=board.confirm(identity,body['generation'],without_review=body.get('without_review',False) is True);review=True
                elif action=='cancel':result=board.cancel(identity)
                else:
                    result=board.retry(identity);review=True
            elif self.path=='/api/undo':
                if 'message_key' in body:
                    if set(body)-{'message_key','key','expected_revision'}:raise ValueError('fields')
                    result=board.undo_message(body['message_key'],body['key'],body.get('expected_revision'))
                elif 'proposal_ids' in body:
                    if set(body)-{'proposal_ids','key','expected_revision'}:raise ValueError('fields')
                    result=[board.announced(p) for p in board.undo_proposals(body['proposal_ids'],body['key'],body.get('expected_revision'))]
                else:result=board.announced(board.undo(int(body['fact_id']),body['key'],body.get('expected_revision')))
                review=True
            elif match:=re.fullmatch(r'/api/suggestions/([a-f0-9-]+)/(?P<action>accept|dismiss)',self.path):
                if set(body)-{'key'}:raise ValueError('fields')
                if match['action']=='accept':result=board.announced(board.accept_suggestion(match[1]));review=True
                else:result=board.dismiss_suggestion(match[1])
            elif self.path=='/api/chat':
                if set(body)-{'text','key','attachments','capture_only'}:raise ValueError('fields')
                result=self.server.chat.submit(body['text'],body['key'],body.get('attachments'),body.get('capture_only',False))
                if result['status']=='pending':self.server.pool.submit(self.server.run_chat,body['key'])
            elif self.path=='/api/suggestion':
                if set(body)!={'key'}:raise ValueError('fields')
                result=board.request_suggestion(body['key'])
                if result['request'].get('key')==body['key'] and result['request'].get('status')=='pending':self.server.pool.submit(board.suggest_order,body['key'])
            elif self.path=='/api/chat/clear':
                if set(body)!={'key'}:raise ValueError('fields')
                result=self.server.chat.clear(body['key'])
            elif self.path=='/api/chat/retry':
                if set(body)!={'key'}:raise ValueError('fields')
                result=self.server.chat.retry(body['key'])
                if result['status']=='pending':self.server.pool.submit(self.server.run_chat,body['key'])
            elif self.path=='/api/settings':
                if set(body)-{'values','key','expected_revision'}:raise ValueError('fields')
                result=configure(board.app,body['values'],body['key'],body.get('expected_revision'))
                if result['engine_changed']:self.server.pool.submit(self.server.configure_harness)
            elif self.path=='/api/backups':
                if set(body)-{'key'}:raise ValueError('fields')
                from .archives import snapshot
                result=snapshot(board.app,key=body.get('key'))
            elif self.path=='/api/recovery/inspect':
                if set(body)!={'operation_key'}:raise ValueError('fields')
                from .recovery import inspect
                result=inspect(board.app,body['operation_key'])
            elif self.path=='/api/recovery/resolve':
                if set(body)!={'operation_key','resolution','precondition'}:raise ValueError('fields')
                from .recovery import resolve
                result=resolve(board.app,body['operation_key'],body['resolution'],body['precondition'])
            elif self.path=='/api/shutdown':
                # Answer completely before stopping, so the caller never sees a truncated reply.
                self.reply(200,{'data':{'stopping':True}})
                threading.Thread(target=self.server.shutdown,daemon=True).start();return
            else:
                self.reply(404,{'error':{'code':'not_found','message':'Route unavailable.'}});return
            self.reply(200,{'data':result})
            if review:self.server.schedule(result)
        except AssistantError as error:self.reply(409,{'error':{'code':error.code,'message':str(error),'details':error.details}})
        except (KeyError,ValueError,TypeError,AttributeError):self.reply(400,{'error':{'code':'invalid_request','message':'Invalid action or fields.'}})


def preflight(state):
    selected=Path(state).expanduser().resolve()
    if selected.exists() and any(selected.iterdir()):
        database=selected/'assistant.sqlite3'
        try:
            with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as con:
                profile=con.execute("SELECT value FROM state WHERE key='profile'").fetchone()
                marker=con.execute("SELECT value FROM state WHERE key='synthetic_bootstrap'").fetchone()
                owned=bool(profile and json.loads(profile[0]).get('kind') in ('personal','synthetic') or marker and json.loads(marker[0]) is True)
        except (sqlite3.Error,ValueError,TypeError,AttributeError):owned=False
        if not owned:raise AssistantError('synthetic_state_required','Use an empty directory or an initialized Day Attendant profile; unmarked data is not opened or migrated.')
    return selected


def initialize_profile(state,kind='personal',zone=None,name=None):
    selected=preflight(state)
    app=Assistant(selected)
    Connected(app).initialize(kind=kind,zone=zone,name=name)
    return app


def bind(app,port=0):
    """Reuse the last address and session so an open tab keeps working after a restart."""
    path=app.store.path/'ui-session.json'
    try:
        saved=json.loads(path.read_text())
        token=saved['token'] if isinstance(saved.get('token'),str) and len(saved['token'])>=32 else None
        previous=saved['port'] if isinstance(saved.get('port'),int) and 1024<=saved['port']<=65535 else 0
    except (OSError,ValueError,AttributeError):token,previous=None,0
    candidates=[port] if port else ([previous] if previous else [])+[0]
    for index,candidate in enumerate(candidates):
        try:server=LocalServer(app,None,candidate,token);break
        except OSError:
            if index==len(candidates)-1:raise  # The saved port is taken: fall back to a free one.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump({'port':server.server_port,'token':server.token},f)
    return server


def serve(state,port=0,zone=None,kind=None,supervised=False):
    selected=preflight(state)
    app=Assistant(selected)
    # Lifetime ownership uses the platform lock adapter on both Linux and macOS.
    with exclusive(app.store.path/'server.lock',timeout=0):
        board=Connected(app)
        profile=app.store.state_value('profile')
        selected_kind=kind or (profile or {}).get('kind') or ('synthetic' if app.store.state_value('synthetic_bootstrap',False) else 'personal')
        board.initialize(kind=selected_kind,zone=zone)
        try:upgrade_tasks(app)
        except AssistantError as error:
            # Everything else keeps working; only urgent waits for the next start or profile upgrade.
            print('Urgent priority not enabled yet: '+str(error),file=sys.stderr,flush=True)
        board.startup()
        server=bind(app,port)
        receipt=app.store.path/'ui-server.json'
        try:
            server.configure_harness()
            server.schedule()  # Changes left unreviewed by a restart; without any, no model runs.
            fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w') as f:json.dump({'origin':server.origin,'token':server.token,'pid':os.getpid(),'profile_id':app.store.state_value('profile')['id']},f)
            print(server.origin if supervised else server.origin+'/#session='+server.token,flush=True)
            def stop(signum,frame):threading.Thread(target=server.shutdown,daemon=True).start()
            original=signal.signal(signal.SIGTERM,stop)
            try:server.serve_forever(poll_interval=.2)
            except KeyboardInterrupt:pass
            finally:signal.signal(signal.SIGTERM,original)
        finally:
            if server.board.bridge:server.board.bridge.close()
            server.server_close();receipt.unlink(missing_ok=True)


def client(state,path='/api/state',data=None):
    receipt=Path(state).expanduser().resolve()/'ui-server.json'
    try:connection=json.loads(receipt.read_text())
    except (OSError,ValueError) as error:raise AssistantError('server_unavailable','Start the local service for this profile before using its connected commands.') from error
    if not re.fullmatch(r'http://127\.0\.0\.1:[0-9]+',connection.get('origin','')):
        raise AssistantError('invalid_server_receipt','Invalid local service receipt.')
    request=Request(connection['origin']+path,data=encoded(data).encode() if data is not None else None,
        headers={'Authorization':'Bearer '+connection['token'],'Origin':connection['origin'],'Content-Type':'application/json'})
    try:
        with urlopen(request,timeout=20) as response:return json.load(response)['data']
    except HTTPError as error:
        detail=json.load(error).get('error',{})
        raise AssistantError(detail.get('code','server_error'),detail.get('message','Local service error.'),detail.get('details')) from error
    except (URLError,OSError) as error:raise AssistantError('server_unavailable','The local service is unavailable.') from error
