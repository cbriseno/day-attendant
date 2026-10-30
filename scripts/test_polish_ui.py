#!/usr/bin/env python3
"""U11: real HTTP/SQLite, safe chat, closed pages, localized calm themes via isolated Chrome AXI."""
import argparse
from datetime import datetime, timedelta, timezone
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
from day_attendant.conversation import Conversation
from day_attendant.service import Assistant
from day_attendant.web import LocalServer
from test_conversation import ChatFixture

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', required=True)
parser.add_argument('--screens-only', action='store_true', help='Capture visual follow-up without repeating the behavior suite')
args = parser.parse_args()
out = Path(args.out).resolve()
out.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, CHROME_DEVTOOLS_AXI_SESSION='day-u11-' + uuid.uuid4().hex[:10])
for key in ('CHROME_DEVTOOLS_AXI_BROWSER_URL', 'CHROME_DEVTOOLS_AXI_AUTO_CONNECT', 'CHROME_DEVTOOLS_AXI_USER_DATA_DIR', 'CHROME_DEVTOOLS_AXI_PORT'):
    env.pop(key, None)
def axi(*command, script=None):
    result = subprocess.run(['chrome-devtools-axi', *command], input=script, env=env, text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout

with tempfile.TemporaryDirectory(prefix='day-polish-ui-') as state:
    clock = datetime.now(timezone.utc) - timedelta(days=3)
    app = Assistant(state, clock=lambda: clock)
    sample = '**Listo** · *revisa* [Ayuda](https://example.com)\n\n- Primera opción\n- Segunda opción\n\n`<img src=x onerror=alert(1)>`\n\n```html\n<script>alert(1)</script>\n```\n\n<img src=x onerror=alert(1)> [No abrir](javascript:alert)'
    bridge = ChatFixture({'message': sample, 'uncertainties': [], 'intent': None})
    board = Connected(app, bridge)
    board.initialize(kind='personal', zone='America/Edmonton')
    def apply(action, payload, key):
        return board.request(action, payload, key, expected_revision=board.snapshot()['revision'])
    for i in range(23):
        clock += timedelta(hours=3)
        uid = apply('task_add', {'title': f'Responsabilidad cerrada {i + 1}'}, f'add-{i}')['result']['uuid']
        apply('task_done' if i % 4 else 'front_retire', {'uuid': uid}, f'close-{i}')
    clock = datetime.now(timezone.utc)
    for i, priority in enumerate(('urgent', 'high', 'medium', 'low')):
        apply('task_add', {'title': ['Enviar propuesta', 'Revisar presupuesto', 'Confirmar entrega', 'Organizar documentos'][i], 'priority': priority, 'group': 'Proyecto', 'due': (clock - timedelta(hours=18)).isoformat()}, f'open-{i}')
    chat = Conversation(board)
    chat.submit('Dame un resumen', 'formatting')
    chat.run('formatting')
    server = LocalServer(app, bridge)
    server.catalogs = {'codex': [{'model': 'controlled-fixture', 'efforts': ['medium', 'high'], 'defaultEffort': 'medium'}], 'claude': []}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {}
    try:
        axi('open', server.origin + '/#session=' + server.token)
        axi('resize', '1440', '1000')
        if not args.screens_only:
            report['checks'] = json.loads(axi('run', script=(ROOT / 'scripts/test_polish_ui.mjs').read_text()).strip().splitlines()[-1])
        axi('run', script="await page.eval(()=>location.reload());await page.wait('[data-task-id]');")
        for width, height in ((1440, 1100), (390, 844)):
            axi('resize', str(width), str(height))
            for locale in ('es', 'en'):
                for theme in ('light', 'dark'):
                    axi('run', script=f"await page.eval(()=>{{for(const [id,v] of [['locale-choice','{locale}'],['theme-choice','{theme}']]){{const e=document.getElementById(id);e.value=v;e.dispatchEvent(new Event('change',{{bubbles:true}}))}}document.querySelector('.closed-list').open=true}});")
                    fits = json.loads(axi('run', script="console.log(JSON.stringify(await page.eval(()=>document.documentElement.scrollWidth<=innerWidth)));" ).strip().splitlines()[-1])
                    assert fits
                    axi('screenshot', str(out / f'today-{width}-{locale}-{theme}.png'))
                    if theme == 'dark':
                        axi('run', script="await page.click('#settings-open');await page.wait('#setting-effort');")
                        axi('screenshot', str(out / f'settings-{width}-{locale}-{theme}.png'))
                        axi('run', script="console.log(JSON.stringify(await page.eval(()=>{const el=document.querySelector('#setting-effort');if([...el.options].some(o=>o.textContent==='medium')||document.querySelector('#settings-dialog').textContent.includes('harness'))throw Error('Settings jargon');document.querySelector('#settings-dialog').close();return true})));")
            axi('run', script="await page.eval(()=>{if(document.querySelector('#chat-toggle').getAttribute('aria-expanded')!=='true')document.querySelector('#chat-toggle').click()});")
            axi('screenshot', str(out / f'chat-{width}-en-dark.png'))
            axi('run', script="await page.click('#chat-close');await page.eval(()=>{document.querySelector('[data-closed-more]').scrollIntoView({block:'center'})});")
            axi('screenshot', str(out / f'closed-{width}-en-dark.png'))
            axi('run', script="await page.eval(()=>scrollTo(0,0));await page.click('#capture-open');await page.eval(()=>{const el=document.querySelector('#capture-due');el.value='2026-11-09T14:30';el.dispatchEvent(new Event('input',{bubbles:true}))});")
            axi('screenshot', str(out / f'capture-{width}-en-dark.png'))
            axi('run', script="await page.eval(()=>document.querySelector('#capture-dialog').close());")
        assert len(board.snapshot()['tasks']) == 27
        report['dateChecks'] = json.loads(axi('run', script="""
await page.click('#capture-open');
console.log(JSON.stringify(await page.eval(()=>{const el=document.querySelector('#capture-due');el.value='2026-11-09T14:30';el.dispatchEvent(new Event('input',{bubbles:true}));const note=el.nextElementSibling.textContent;if(!note.includes('Nov')||!note.includes('14:30'))throw Error('Readable date missing: '+note);el.value='2025-11-02T01:30';el.dispatchEvent(new Event('input',{bubbles:true}));if(!el.nextElementSibling.textContent.includes('unambiguous'))throw Error('Ambiguous date must not invent an instant');document.querySelector('#capture-dialog').close();return {readableDate:note,contextStatusHidden:document.querySelector('#context-status').hidden}})));
""").strip().splitlines()[-1])
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
