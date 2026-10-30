#!/usr/bin/env python3
"""Focus selection and immediate actions against real disposable SQLite via Chrome AXI.

Usage: python3 -B scripts/test_focus_ui.py --out /tmp/day-u10-proof
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
session = 'day-u10-' + uuid.uuid4().hex[:10]
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

with tempfile.TemporaryDirectory(prefix='day-focus-ui-') as state:
    app = Assistant(state)
    bridge = ReviewFixture()  # Controlled later review; direct actions never call a model.
    board = Connected(app, bridge)
    board.initialize(kind='personal', zone='America/Edmonton')
    def apply(action, payload, key):
        return board.request(action, payload, key, expected_revision=board.snapshot()['revision'])
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    ids = [apply('task_add', {'title': title, 'priority': 'urgent' if i < 2 else 'medium',
                             'group': 'Proyecto', 'due': (now + timedelta(hours=i - 2)).isoformat()},
                 'seed-' + str(i))['result']['uuid']
           for i, title in enumerate(('Preparar propuesta', 'Enviar propuesta', 'Revisar presupuesto',
                                      'Confirmar entrega', 'Archivar notas', 'Revisar recibos',
                                      'Organizar documentos', 'Solicitar respuesta', 'Revisar pendientes',
                                      'Retomar mañana', 'Retomar después', 'Retomar la próxima semana'))]
    apply('dependency_update', {'uuid': ids[1], 'dependsOn': [ids[0]]}, 'seed-link')
    for i in (2, 3, 4):
        apply('front_activate', {'uuid': ids[i]}, 'seed-active-' + str(i))
    for i, hours in ((9, 24), (10, 48), (11, 96)):
        apply('task_snooze', {'uuid': ids[i], 'until': (now + timedelta(hours=hours)).isoformat()}, 'seed-snooze-' + str(i))
    apply('continuity_update', {'uuid': ids[0], 'next': 'Revisar el último borrador', 'awaiting': True}, 'seed-next')
    server = LocalServer(app, bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {'scope': 'Synthetic mouse/touch pointer events and native clicks in Chrome; real HTTP/SQLite'}
    try:
        axi('open', server.origin + '/#session=' + server.token)
        axi('resize', '1440', '1000')
        axi('run', script="await page.wait('[data-task-id]');await page.eval(()=>{const e=document.querySelector('#locale-choice');e.value='es';e.dispatchEvent(new Event('change',{bubbles:true}))});")
        report['desktop'] = json.loads(axi('run', script=(ROOT / 'scripts/test_focus_ui.mjs').read_text()).strip().splitlines()[-1])
        axi('screenshot', str(out / 'focus-1440.png'))
        axi('resize', '390', '844')
        report['phone'] = json.loads(axi('run', script="""
await page.eval(()=>{const e=document.querySelector('#locale-choice');e.value='en';e.dispatchEvent(new Event('change',{bubbles:true}))});
await page.click('#board-view');await page.click('#focus-view');
await page.click('.focus-item [data-action="priority"]');
await page.eval(()=>{if(!document.querySelector('.row-menu.sheet'))throw Error('phone priority sheet');document.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));if(document.activeElement.dataset.menuAct!=='priority')throw Error('keyboard navigation');document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));if(document.querySelector('.row-menu')||document.activeElement.dataset.action!=='priority')throw Error('Escape restores priority focus')});
await page.click('.focus-item [data-action="priority"]');
await page.click('[data-menu-act="priority"][data-value="high"]');
await page.eval(async()=>{const id=document.querySelector('.focus-item').dataset.taskId;for(let i=0;i<150;i++){const data=(await(await fetch('/api/state')).json()).data;if(data.tasks.find(t=>t.id===id).priority==='high')return;await new Promise(r=>setTimeout(r,40))}throw Error('phone priority persistence')});
await page.click('.focus-item [data-action="retire"]');
await page.eval(()=>{if(!document.querySelector('.row-menu.sheet .menu-confirm'))throw Error('phone confirmation');document.querySelector('[data-menu-act="keep"]').click();if(document.querySelector('.row-menu')||document.querySelector('[data-expand]'))throw Error('phone cancel')});

console.log(JSON.stringify(await page.eval(()=>({fits:document.documentElement.scrollWidth<=innerWidth, sections:[...document.querySelectorAll('.focus-section h2')].map(n=>n.textContent), buttons:[...document.querySelectorAll('.focus-actions .btn')].every(n=>n.getBoundingClientRect().height>=34)}))));
""" ).strip().splitlines()[-1])
        assert report['phone']['fits'] and report['phone']['buttons']
        axi('screenshot', str(out / 'focus-390.png'))
        report['console'] = axi('console')
        report['passed'] = True
    except BaseException as error:
        report['passed'] = False
        report['error'] = str(error)
        raise
    finally:
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        server.shutdown()
        thread.join(5)
        server.server_close()
        server.pool.shutdown(wait=True)
        axi('stop')
        print(json.dumps(report))
