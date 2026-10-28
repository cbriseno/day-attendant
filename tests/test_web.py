"""Local HTTP boundary and real CLI/domain integration, without inference."""
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from day_attendant.connected import Connected
from day_attendant.service import Assistant
from day_attendant.web import LocalServer,serve
from day_attendant.errors import AssistantError
from test_connected import ReviewFixture

ROOT=Path(__file__).resolve().parent.parent


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='day-http-test-')
        self.app=Assistant(self.temp.name);Connected(self.app).initialize()
        self.server=LocalServer(self.app,ReviewFixture())
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        with self.app.store.connect() as con:self.uid=json.loads(con.execute("SELECT result FROM operations WHERE key='bootstrap-task-1'").fetchone()[0])['uuid']
        Path(self.temp.name,'ui-server.json').write_text(json.dumps({'origin':self.server.origin,'token':self.server.token,'pid':0}))

    def tearDown(self):
        self.server.shutdown();self.thread.join(5);self.server.pool.shutdown(wait=True);self.server.server_close();self.temp.cleanup()

    def request(self,path,body=None,authenticated=True,origin=None,host=None):
        headers={'Content-Type':'application/json','Origin':origin or self.server.origin}
        if authenticated:headers['Authorization']='Bearer '+self.server.token
        if host:headers['Host']=host
        req=Request(self.server.origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urlopen(req,timeout=10) as response:return response.status,dict(response.headers),json.load(response) if path.startswith('/api/') else response.read()
        except HTTPError as error:return error.code,dict(error.headers),json.load(error)

    def test_session_origin_host_and_no_arbitrary_rpc(self):
        self.assertEqual(self.request('/api/state',authenticated=False)[0],401)
        self.assertEqual(self.request('/api/session',{'token':self.server.token},authenticated=False,origin='https://unrelated.example')[0],403)
        status,headers,_=self.request('/api/session',{'token':self.server.token},authenticated=False)
        self.assertEqual(status,200);self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        self.assertEqual(self.request('/api/state',host='unrelated.example')[0],403)
        self.assertEqual(self.request('/api/app-server',{'method':'thread/shellCommand','command':'anything'})[0],404)
        self.assertEqual(self.request('/api/state',authenticated=True)[0],200)

    def test_cli_and_http_share_idempotency_and_authority(self):
        body={'action':'front_activate','payload':{'uuid':self.uid},'key':'shared-input'}
        with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda _:self.request('/api/proposals',body),range(2)))
        self.assertTrue(all(r[0]==200 for r in responses));self.assertEqual(responses[0][2]['data']['id'],responses[1][2]['data']['id'])
        identity=responses[0][2]['data']['id']
        for _ in range(50):
            if self.server.board.proposal(identity)['phase']=='applied':break
            time.sleep(.1)
        result=subprocess.run([sys.executable,'-B','day-attendant.py','--json','--state',self.temp.name,'board','request','front_activate','--payload',json.dumps({'uuid':self.uid}),'--key','shared-input'],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['data']['id'],identity)
        with self.app.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM facts').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT count(*) FROM evaluations').fetchone()[0],1)
        bypass=subprocess.run([sys.executable,'-B','day-attendant.py','--json','--state',self.temp.name,'task','done',self.uid,'--key','bypass'],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(bypass.returncode,2);self.assertEqual(json.loads(bypass.stdout)['error']['code'],'proposal_required')
        self.assertEqual(self.app.tasks.get(self.uid)['status'],'pending')

    def test_invalid_requests_never_create_operations(self):
        before=self.app.store.revision()
        bodies=[{'action':'task_restore','payload':{'uuid':self.uid},'key':'bad-restore'},
                {'action':'front_activate','payload':{'uuid':self.uid},'key':'bad-state','state':'/another/path'},
                {'action':'front_activate','payload':{'uuid':self.uid,'shell':'anything'},'key':'bad-field'}]
        for body in bodies:
            with self.subTest(body=body):self.assertIn(self.request('/api/proposals',body)[0],(400,409))
        self.assertEqual(self.app.store.revision(),before)
        with self.app.store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM proposals').fetchone()[0],0)

    def test_unmarked_existing_state_is_rejected_without_migration(self):
        with tempfile.TemporaryDirectory(prefix='day-unmarked-state-') as state:
            legacy=Assistant(state)
            before=hashlib.sha256(legacy.store.database.read_bytes()).hexdigest()
            with self.assertRaises(AssistantError) as error:serve(state)
            self.assertEqual(error.exception.code,'synthetic_state_required')
            self.assertEqual(hashlib.sha256(legacy.store.database.read_bytes()).hexdigest(),before)


if __name__=='__main__':unittest.main()
