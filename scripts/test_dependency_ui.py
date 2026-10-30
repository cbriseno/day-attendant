#!/usr/bin/env python3
"""Dependency pointer events and phone controls against real disposable SQLite via Chrome AXI.

Usage: python3 -B scripts/test_dependency_ui.py --out /tmp/day-u8-proof
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
session = 'day-u8-' + uuid.uuid4().hex[:10]
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

with tempfile.TemporaryDirectory(prefix='day-dependency-ui-') as state:
    app = Assistant(state)
    bridge = ReviewFixture()  # Controlled later review; direct actions never call a model.
    board = Connected(app, bridge)
    board.initialize(kind='personal', zone='America/Edmonton')
    def apply(action, payload, key):
        return board.request(action, payload, key, expected_revision=board.snapshot()['revision'])
    ids = [apply('task_add', {'title': title, 'group': 'Proyecto'}, 'seed-' + str(i))['result']['uuid']
           for i, title in enumerate(('Preparar propuesta', 'Revisar presupuesto', 'Enviar propuesta',
                                      'Confirmar entrega', 'Archivar notas'))]
    apply('dependency_update', {'uuid': ids[2], 'dependsOn': [ids[1]]}, 'seed-link')
    server = LocalServer(app, bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {'scope': 'Synthetic mouse/touch pointer events and native clicks in Chrome; real HTTP/SQLite'}
    try:
        axi('open', server.origin + '/#session=' + server.token)
        axi('resize', '1440', '1000')
        axi('run', script="await page.wait('[data-task-id]');await page.eval(()=>{const e=document.querySelector('#locale-choice');e.value='es';e.dispatchEvent(new Event('change',{bubbles:true}))});")
        desktop, phone = (ROOT / 'scripts/test_dependency_ui.mjs').read_text().split('// PHONE\n')
        report['desktop'] = json.loads(axi('run', script=desktop).strip().splitlines()[-1])
        axi('run', script="await page.eval(()=>u8test.el(u8ids[1]).dispatchEvent(new PointerEvent('pointerover',{bubbles:true,pointerType:'mouse'})));")
        axi('screenshot', str(out / 'dependencies-1440.png'))
        axi('resize', '390', '844')
        report['phone'] = json.loads(axi('run', script=phone).strip().splitlines()[-1])
        axi('run', script="const id=await page.eval(()=>u8ids[1]);await page.click('[data-id=\"'+id+'\"][data-action=\"dependencies\"]');")
        axi('screenshot', str(out / 'relationships-390.png'))
        axi('run', script="await page.click('[data-menu-act=\"connect-needs\"]');")
        axi('screenshot', str(out / 'connect-390.png'))
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
