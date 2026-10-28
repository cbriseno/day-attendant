"""Explicitly started loopback UI for a fixed synthetic state directory."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
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
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .codexbridge import CodexBridge
from .connected import Connected
from .conversation import Conversation
from .errors import AssistantError
from .service import Assistant, encoded

ASSETS = Path(__file__).resolve().parent / "ui"


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app, bridge, port=0):
        self.board=Connected(app,bridge)
        self.chat=Conversation(self.board)
        self.pool=ThreadPoolExecutor(max_workers=3,thread_name_prefix="day-request")
        self.token=secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1",port),Handler)
        self.origin=f"http://127.0.0.1:{self.server_port}"

    def schedule(self, proposal):
        if proposal['phase']=='evaluating':self.pool.submit(self.board.evaluate,proposal['id'])
        self.pool.submit(self.board.evaluate_notices)

    def server_close(self):
        super().server_close()
        self.pool.shutdown(wait=False,cancel_futures=True)


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
        if self.path in ('/interface.js','/locales.js'):
            self.reply(200,(ASSETS/self.path[1:]).read_bytes(),'text/javascript; charset=utf-8');return
        if not self.authenticated():
            self.reply(401,{'error':{'code':'session_required','message':'Open the local link for this session.'}});return
        if self.path=='/api/state':
            try:self.reply(200,{'data':self.server.board.snapshot()})
            except AssistantError as error:self.reply(409,{'error':{'code':error.code,'message':str(error)}})
            return
        self.reply(404,{'error':{'code':'not_found','message':'Route unavailable.'}})

    def do_POST(self):
        if not self.host_valid():return
        if self.headers.get('Origin')!=self.server.origin or not self.headers.get('Content-Type','').startswith('application/json'):
            self.reply(403,{'error':{'code':'origin_forbidden','message':'The request must originate from this local application.'}});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=65536:raise ValueError('size')
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict):raise ValueError('object')
        except (ValueError,TypeError):
            self.reply(400,{'error':{'code':'invalid_json','message':'Invalid request.'}});return
        if self.path=='/api/session':
            token=body.get('token','')
            if not isinstance(token,str) or not hmac.compare_digest(token,self.server.token):
                self.reply(401,{'error':{'code':'session_required','message':'Invalid session link.'}});return
            self.reply(200,{'data':{'authenticated':True}},cookie='day_session='+self.server.token+'; HttpOnly; SameSite=Strict; Path=/');return
        if not self.authenticated():
            self.reply(401,{'error':{'code':'session_required','message':'Reopen the session link.'}});return
        board=self.server.board
        try:
            if self.path=='/api/proposals':
                if set(body)-{'action','payload','key','expected_revision'}:raise ValueError('fields')
                result=board.request(body['action'],body['payload'],body['key'],origin='cli' if self.headers.get('Authorization','').startswith('Bearer ') else 'ui',expected_revision=body.get('expected_revision'))
                self.server.schedule(result)
            elif match:=re.fullmatch(r'/api/proposals/([a-f0-9-]+)/(?P<action>confirm|cancel|retry)',self.path):
                identity=match[1];action=match['action']
                if action=='confirm':result=board.confirm(identity,body['generation'],without_review=body.get('without_review',False) is True)
                elif action=='cancel':result=board.cancel(identity)
                else:
                    result=board.retry(identity);self.server.schedule(result)
            elif self.path=='/api/undo':
                result=board.undo(int(body['fact_id']),body['key'],body.get('expected_revision'));self.server.schedule(result)
            elif self.path=='/api/chat':
                if set(body)-{'text','key'}:raise ValueError('fields')
                result=self.server.chat.submit(body['text'],body['key'])
                if result['status']=='pending':self.server.pool.submit(self.server.chat.run,body['key'])
            elif self.path=='/api/chat/retry':
                if set(body)!={'key'}:raise ValueError('fields')
                result=self.server.chat.retry(body['key'])
                if result['status']=='pending':self.server.pool.submit(self.server.chat.run,body['key'])
            else:
                self.reply(404,{'error':{'code':'not_found','message':'Route unavailable.'}});return
            self.reply(200,{'data':result})
        except AssistantError as error:self.reply(409,{'error':{'code':error.code,'message':str(error),'details':error.details}})
        except (KeyError,ValueError,TypeError,AttributeError):self.reply(400,{'error':{'code':'invalid_request','message':'Invalid action or fields.'}})


def serve(state,task_bin=None,port=0,zone='America/Edmonton'):
    selected=Path(state).expanduser().resolve()
    if selected.exists() and any(selected.iterdir()):
        database=selected/'assistant.sqlite3'
        try:
            with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as con:
                marker=con.execute("SELECT value FROM state WHERE key='synthetic_bootstrap'").fetchone()
                owned=bool(marker and json.loads(marker[0]) is True)
        except (sqlite3.Error,ValueError):owned=False
        if not owned:
            raise AssistantError('synthetic_state_required','Use a new empty synthetic workspace; existing unmarked state is not opened or migrated.')
    try:ZoneInfo(zone)
    except ZoneInfoNotFoundError as error:raise AssistantError('invalid_zone','Invalid time zone.') from error
    app=Assistant(state,task_bin)
    lifetime=(app.store.path/'server.lock').open('a')
    try:fcntl.flock(lifetime,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError as error:raise AssistantError('server_running','This workspace already has an active service.') from error
    board=Connected(app)
    board.initialize()
    board.startup()
    with app.store.lock(),app.store.connect() as con:con.execute("INSERT OR REPLACE INTO state VALUES('timezone',?)",(encoded(zone),))
    workspace=app.store.path/'agent-workspace';workspace.mkdir(mode=0o700,exist_ok=True)
    bridge=None
    try:
        try:
            selected_model=app.store.state_value('agent_model')
            bridge=CodexBridge(workspace,model=selected_model)
            with app.store.lock(),app.store.connect() as con:
                con.execute("INSERT OR REPLACE INTO state VALUES('agent_model',?)",(encoded(bridge.model),))
                con.execute("DELETE FROM state WHERE key='agent_startup_error'")
        except (AssistantError,OSError,ValueError) as error:
            with app.store.lock(),app.store.connect() as con:
                con.execute("INSERT OR REPLACE INTO state VALUES('agent_startup_error',?)",(encoded(str(error)[:400]),))
            bridge=None
        server=LocalServer(app,bridge,port)
        receipt=app.store.path/'ui-server.json'
        fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:json.dump({'origin':server.origin,'token':server.token,'pid':os.getpid()},f)
        print(server.origin+'/#session='+server.token,flush=True)
        def stop(signum,frame):
            threading.Thread(target=server.shutdown,daemon=True).start()
        original=signal.signal(signal.SIGTERM,stop)
        try:server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:pass
        finally:
            signal.signal(signal.SIGTERM,original)
            server.server_close()
            receipt.unlink(missing_ok=True)
    finally:
        if bridge:bridge.close()
        lifetime.close()


def client(state,path='/api/state',data=None):
    receipt=Path(state).expanduser().resolve()/'ui-server.json'
    try:connection=json.loads(receipt.read_text())
    except (OSError,ValueError) as error:raise AssistantError('server_unavailable','Start web --state WORKSPACE --synthetic to operate the connected workspace.') from error
    if not re.fullmatch(r'http://127\.0\.0\.1:[0-9]+',connection.get('origin','')):
        raise AssistantError('invalid_server_receipt','Invalid local service receipt.')
    request=Request(connection['origin']+path,data=encoded(data).encode() if data is not None else None,
        headers={'Authorization':'Bearer '+connection['token'],'Origin':connection['origin'],'Content-Type':'application/json'})
    try:
        with urlopen(request,timeout=20) as response:return json.load(response)['data']
    except HTTPError as error:
        detail=json.load(error).get('error',{})
        raise AssistantError(detail.get('code','server_error'),detail.get('message','Local service error.')) from error
    except (URLError,OSError) as error:raise AssistantError('server_unavailable','The local service is unavailable.') from error
