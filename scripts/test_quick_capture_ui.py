#!/usr/bin/env python3
"""One-line quick capture against real disposable SQLite via Chrome AXI.

Usage: python3 -B scripts/test_quick_capture_ui.py --out /tmp/day-quick-capture-proof
Measures the visible card and the saved task separately, in Spanish on desktop and English on a phone.
No running personal profile, model calls or external services are used.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import uuid
from zoneinfo import ZoneInfo

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
session = 'day-quick-' + uuid.uuid4().hex[:10]
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

# Types a line, shows its preview, presses Enter, then times the card and the saved task apart.
CAPTURE = """
console.log(JSON.stringify(await page.eval(async()=>{const [locale,line,title]=[LOCALE,LINE,TITLE];
 const q=s=>document.querySelector(s),l=q('#locale-choice');l.value=locale;l.dispatchEvent(new Event('change',{bubbles:true}));
 const input=q('#qa-input');input.focus();input.value=line;input.dispatchEvent(new Event('input',{bubbles:true}));
 const preview=q('#qa-preview').textContent,t0=performance.now();
 input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));q('#quick-add').requestSubmit();
 const card=[...document.querySelectorAll('#board .task')].find(e=>e.textContent.includes(title)),visible=performance.now()-t0;
 for(let i=0;i<200;i++){const data=(await(await fetch('/api/state',{cache:'no-store'})).json()).data;if(data.tasks.some(t=>t.title===title))break;await new Promise(r=>setTimeout(r,10))}
 const saved=performance.now()-t0;
 return {preview,cardShown:!!card,visibleMs:Math.round(visible*10)/10,savedMs:Math.round(saved),dialogs:[...document.querySelectorAll('dialog[open]')].map(d=>d.id),line:input.value,fits:document.documentElement.scrollWidth<=innerWidth};
})));
"""

def capture(locale, line, title):
    script = CAPTURE.replace('LOCALE', json.dumps(locale)).replace('LINE', json.dumps(line)).replace('TITLE', json.dumps(title))
    result = json.loads(axi('run', script=script).strip().splitlines()[-1])
    assert result['cardShown'] and not result['dialogs'] and not result['line'] and result['fits'], result
    return result

def typed(locale, line):
    return axi('run', script="""await page.eval(()=>{const [locale,line]=%s;const l=document.querySelector('#locale-choice');l.value=locale;l.dispatchEvent(new Event('change',{bubbles:true}));const i=document.querySelector('#qa-input');i.value=line;i.dispatchEvent(new Event('input',{bubbles:true}))});""" % json.dumps([locale, line]))

with tempfile.TemporaryDirectory(prefix='day-quick-ui-') as state:
    zone = 'America/Mexico_City'
    app = Assistant(state)
    bridge = ReviewFixture()  # Controlled later review; direct captures never call a model.
    board = Connected(app, bridge)
    board.initialize(kind='personal', zone=zone)
    board.request('task_add', {'title': 'Revisar recibos', 'priority': 'medium', 'group': 'Casa'}, 'seed-home',
                  expected_revision=board.snapshot()['revision'])
    server = LocalServer(app, bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {'scope': 'Native keyboard Enter submit in Chrome; real HTTP/SQLite in a disposable profile', 'zone': zone}
    try:
        axi('open', server.origin + '/#session=' + server.token)
        axi('resize', '1440', '1000')
        axi('run', script="await page.wait('[data-task-id]');")
        typed('es', 'pagar luz mañana !2 #casa')
        axi('screenshot', str(out / 'quick-1440-es-preview.png'))
        report['desktop_es'] = capture('es', 'pagar luz mañana !2 #casa', 'Pagar luz')
        axi('screenshot', str(out / 'quick-1440-es-added.png'))
        axi('resize', '390', '844')
        # The phone opens the assistant as a sheet; it is closed as a person would.
        axi('run', script="await page.eval(()=>document.querySelector('#chat-dialog').open&&document.querySelector('#chat-dialog').close());")
        typed('en', 'call the bank friday 09:30 !urgent #Errands')
        axi('screenshot', str(out / 'quick-390-en-preview.png'))
        report['phone_en'] = capture('en', 'call the bank friday 09:30 !urgent #Errands', 'Call the bank')
        axi('screenshot', str(out / 'quick-390-en-added.png'))
        typed('es', 'revisar !1 !3')
        axi('run', script="await page.eval(()=>document.querySelector('#quick-add').requestSubmit());")
        axi('screenshot', str(out / 'quick-390-es-refused.png'))
        report['refused'] = axi('run', script="console.log(await page.eval(()=>document.querySelector('#qa-preview').textContent+' | line kept: '+document.querySelector('#qa-input').value))").strip().splitlines()[-1]
        report['console'] = axi('console')
        # Persistence: a new service instance reads the same SQLite profile.
        tasks = {t['title']: t for t in Connected(Assistant(state), ReviewFixture()).snapshot()['tasks']}
        local = ZoneInfo(zone)
        pay, bank = tasks['Pagar luz'], tasks['Call the bank']
        report['persisted'] = {k: {f: tasks[k][f] for f in ('priority', 'group', 'due', 'createdAt')} for k in ('Pagar luz', 'Call the bank')}
        assert (pay['priority'], pay['group']) == ('high', 'Casa'), pay
        assert datetime.fromisoformat(pay['due'].replace('Z', '+00:00')).astimezone(local).strftime('%H:%M') == '18:00', pay
        assert (bank['priority'], bank['group']) == ('urgent', 'Errands'), bank
        bank_local = datetime.fromisoformat(bank['due'].replace('Z', '+00:00')).astimezone(local)
        assert (bank_local.strftime('%H:%M'), bank_local.weekday()) == ('09:30', 4), bank
        assert 'revisar' not in {t.lower() for t in tasks}, 'a doubtful line is never saved'
        report['passed'] = True
    except BaseException as error:
        report['passed'] = False
        report['error'] = str(error)
        raise
    finally:
        (out / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        server.shutdown()
        thread.join(5)
        server.server_close()
        server.pool.shutdown(wait=True)
        axi('stop')
        print(json.dumps(report, ensure_ascii=False))
