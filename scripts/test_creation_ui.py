#!/usr/bin/env python3
"""Creation age in every task view against real disposable SQLite via Chrome AXI.

Usage: python3 -B scripts/test_creation_ui.py --out /tmp/day-creation-proof
No running personal profile, model calls or external services are used.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from day_attendant.connected import Connected
from day_attendant.service import Assistant
from day_attendant.web import LocalServer
from test_connected import ReviewFixture

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', required=True)
args = parser.parse_args()
out = Path(args.out).resolve()
out.mkdir(parents=True, exist_ok=True)
session = 'day-creation-' + uuid.uuid4().hex[:10]
env = dict(os.environ, CHROME_DEVTOOLS_AXI_SESSION=session)
# Always launch an isolated browser, regardless of the caller's browser defaults.
for key in ('CHROME_DEVTOOLS_AXI_BROWSER_URL', 'CHROME_DEVTOOLS_AXI_AUTO_CONNECT',
            'CHROME_DEVTOOLS_AXI_USER_DATA_DIR', 'CHROME_DEVTOOLS_AXI_PORT'):
    env.pop(key, None)

def axi(*command, script=None):
    result = subprocess.run(['chrome-devtools-axi', *command], input=script, env=env,
                            text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout

with tempfile.TemporaryDirectory(prefix='day-creation-ui-') as state:
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 10, 29, 18, 0, 0, tzinfo=timezone.utc)
    app = Assistant(state, clock=lambda: now)
    bridge = ReviewFixture()
    board = Connected(app, bridge)
    board.initialize(kind='personal', zone='America/Edmonton')
    def apply(action, payload, key):
        return board.request(action, payload, key, expected_revision=board.snapshot()['revision'])
    titles = ['Preparar una propuesta completa para la próxima reunión del equipo', 'Confirmar entrega', 'Organizar documentos']
    ids = [apply('task_add', {'title': title, 'group': 'Proyecto', 'priority': 'high'}, 'seed-'+str(i))['result']['uuid'] for i, title in enumerate(titles)]
    apply('front_activate', {'uuid': ids[0]}, 'activate')
    now += timedelta(days=2)
    apply('task_done', {'uuid': ids[2]}, 'close')
    from uuid import uuid4
    app.tasks.create({'uuid':str(uuid4()),'description':'Registro histórico sin fecha','entry':None})
    server = LocalServer(app, bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {'scope':'Real disposable SQLite, HTTP, isolated Chrome AXI; controlled profile clock; no model calls', 'views':[]}
    def run(script):
        return json.loads(axi('run', script=script).strip().splitlines()[-1])
    try:
        axi('open', server.origin + '/#session=' + server.token)
        axi('run', script="await page.wait('[data-task-id]');")
        for width in (1440,390):
            axi('resize',str(width),'1000' if width==1440 else '844')
            for locale in ('es','en'):
                axi('run',script="await page.eval(()=>{const e=document.querySelector('#locale-choice');e.value='"+locale+"';e.dispatchEvent(new Event('change',{bubbles:true}))});")
                for view in ('list','board','group','focus','closed'):
                    script = """
console.log(JSON.stringify(await page.eval(()=>{
 const view=VIEW;
 const q=s=>document.querySelector(s);
 q(view==='focus'?'#focus-view':view==='board'?'#board-view':'#list-view').click();
 const order=q('#order-choice');order.value=view==='group'?'group':'priority';order.dispatchEvent(new Event('change',{bubbles:true}));
 q('.closed-list').open=view==='closed';
 if(q('#chat-dialog').open)q('#chat-close').click();
 const scope=q(view==='closed'?'#closed':'#board'),rows=[...scope.querySelectorAll(view==='closed'?'.closed-row':'.task')];
 const times=rows.map(r=>r.querySelector('.task-created'));
 if(q('#chat-dialog').open)throw Error('Task view obscured by chat');
 if(!rows.length||times.some(t=>!t))throw Error('Missing creation label '+view);
 if(times.some(t=>!t.title||!t.getAttribute('aria-label')))throw Error('Missing accessible exact date');
 if(times.filter(t=>t.tagName==='TIME').some(t=>!t.dateTime||!t.textContent.includes('2')))throw Error('Wrong native age');
 if(document.documentElement.scrollWidth>innerWidth)throw Error('Page overflow');
 const connectorExtent=rows.map(r=>{const port=r.querySelector('.port-slot');if(!port)return 0;const raw=r.scrollWidth,hidden=port.hidden;port.hidden=true;const content=r.scrollWidth;port.hidden=hidden;return raw-content});
 const overflow=rows.filter(r=>{const port=r.querySelector('.port-slot'),hidden=port?.hidden;if(port)port.hidden=true;const over=r.scrollWidth>r.clientWidth+1;if(port)port.hidden=hidden;return over}).map(r=>({width:r.clientWidth,scroll:r.scrollWidth,title:r.querySelector('.task-title')?.textContent,children:[...r.querySelectorAll('.row-main,.row-text,.row-actions,.row-meta,.task-created')].map(n=>({cls:n.className,width:n.getBoundingClientRect().width,scroll:n.scrollWidth}))}));
 return {view,width:innerWidth,locale:document.documentElement.lang,ages:times.map(t=>t.textContent),overflow,connectorExtent,fits:true};
})));
"""
                    report['views'].append(run(script.replace('VIEW',json.dumps(view))))
                    if locale=='es':axi('screenshot',str(out/f'{view}-{width}.png'))
        # Observe an unchanged-state poll advancing the shared clock, without a reload or mutation.
        axi('run',script="await page.click('#list-view');await page.eval(()=>{window.creationNode=document.querySelector('time.task-created');window.createdBefore=creationNode.textContent});")
        revision=board.snapshot()['revision']
        import time
        refresh_started=time.monotonic()
        now += timedelta(days=1)
        report['refresh']=run("""
await page.eval(async()=>{for(let i=0;i<100;i++){if(creationNode.textContent!==createdBefore)return;await new Promise(r=>setTimeout(r,50))}throw Error('Age refresh timeout')});
console.log(JSON.stringify(await page.eval(()=>({before:createdBefore,after:creationNode.textContent,sameNode:creationNode===document.querySelector('time.task-created')}))));
""")
        report['refresh']['observed_ms']=round((time.monotonic()-refresh_started)*1000)
        assert report['refresh']['sameNode']
        assert board.snapshot()['revision']==revision
        report['console']=axi('console')
        assert not any(v['overflow'] for v in report['views']), 'Card overflow: see measurements and screenshots'
        report['passed']=True
    except BaseException as error:
        report['passed']=False
        report['error']=str(error)
        raise
    finally:
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        server.shutdown();thread.join(5);server.server_close();server.pool.shutdown(wait=True)
        axi('stop')
        print(json.dumps(report))
