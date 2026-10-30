#!/usr/bin/env python3
"""Profile-zone dates follow the service's rules even when the browser's time zone data differs.

The service reads a synthetic America/Edmonton (UTC-5 with US-style DST) compiled with zic, so it
always disagrees with the browser's own data for that zone. Real HTTP/SQLite + isolated Chrome AXI."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import uuid
import zoneinfo

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', required=True)
args = parser.parse_args()
out = Path(args.out).resolve()
out.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, CHROME_DEVTOOLS_AXI_SESSION='day-zone-' + uuid.uuid4().hex[:10])
for key in ('CHROME_DEVTOOLS_AXI_BROWSER_URL', 'CHROME_DEVTOOLS_AXI_AUTO_CONNECT', 'CHROME_DEVTOOLS_AXI_USER_DATA_DIR', 'CHROME_DEVTOOLS_AXI_PORT'):
    env.pop(key, None)
def axi(*command, script=None):
    result = subprocess.run(['chrome-devtools-axi', *command], input=script, env=env, text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout
def page(body):
    return json.loads(axi('run', script='console.log(JSON.stringify(await page.eval(' + body + ')));').strip().splitlines()[-1])
def check(condition, message):
    if not condition:
        raise AssertionError(message)

with tempfile.TemporaryDirectory(prefix='day-zone-rules-') as state, tempfile.TemporaryDirectory(prefix='day-zone-tz-') as tzdir:
    source = Path(tzdir, 'probe.zi')
    source.write_text('Rule Probe 2007 max - Mar Sun>=8 2:00 1:00 D\nRule Probe 2007 max - Nov Sun>=1 2:00 0 S\nZone America/Edmonton -5:00 Probe E%sT\n')
    subprocess.run(['zic', '-d', tzdir, str(source)], check=True)
    zoneinfo.reset_tzpath([tzdir])
    from day_attendant.connected import Connected
    from day_attendant.service import Assistant
    from day_attendant.web import LocalServer
    from test_conversation import ChatFixture

    clock = datetime(2026, 11, 5, 17, 0, tzinfo=timezone.utc)
    app = Assistant(state, clock=lambda: clock)
    bridge = ChatFixture({'message': 'ok', 'uncertainties': [], 'intent': None})
    board = Connected(app, bridge)
    board.initialize(kind='personal', zone='America/Edmonton')
    def apply(action, payload, key):
        return board.request(action, payload, key, expected_revision=board.snapshot()['revision'])
    snoozed = apply('task_add', {'title': 'Posponer prueba'}, 'snooze-task')['result']['uuid']
    apply('event_add', {'title': 'Cita repetida', 'start': '2027-11-07T01:30', 'end': '2027-11-07T03:00', 'zone': 'America/Edmonton', 'fold': 1}, 'event')
    check([(e['start_at'], e['end_at']) for e in board.snapshot()['calendar_events']] == [('2027-11-07T06:30:00Z', '2027-11-07T08:00:00Z')], 'Service uses the synthetic zone')
    server = LocalServer(app, bridge)
    server.catalogs = {'codex': [{'model': 'controlled-fixture', 'efforts': ['medium'], 'defaultEffort': 'medium'}], 'claude': []}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {}
    try:
        axi('open', server.origin + '/#session=' + server.token)
        axi('resize', '1440', '1000')
        axi('run', script="await page.wait('[data-task-id]');")
        report['browserOwnOffset'] = page("()=>new Intl.DateTimeFormat('en-US',{timeZone:'America/Edmonton',timeZoneName:'shortOffset'}).format(new Date('2026-11-09T19:30:00Z'))")
        check('GMT-5' not in report['browserOwnOffset'], 'The browser must disagree with the service for this check')
        for locale in ('es', 'en'):
            page(f"()=>{{const e=document.getElementById('locale-choice');e.value='{locale}';e.dispatchEvent(new Event('change',{{bubbles:true}}));return true}}")
            axi('run', script="await page.click('#capture-open');await page.wait('#capture-due');")
            previews = page("""()=>['2026-11-09T14:30','2026-03-08T02:30','2026-11-01T01:30','2026-11-01T02:00'].map(v=>{const el=document.querySelector('#capture-due');el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}));return el.nextElementSibling.textContent})""")
            report[locale] = {'previews': previews}
            check('14:30 GMT-5' in previews[0] and ('lun' in previews[0] if locale == 'es' else 'Mon' in previews[0]), f'Localized preview in service offset: {previews[0]}')
            refusal = 'Elige una fecha y hora local inequívoca.' if locale == 'es' else 'Choose an unambiguous local date and time.'
            check(previews[1] == previews[2] == refusal, f'Nonexistent and repeated times are refused: {previews}')
            check('02:00 GMT-5' in previews[3], f'Hour after the repeat is exact: {previews[3]}')
            check(page("()=>document.querySelector('#capture-dialog .modal-footer, #capture-dialog footer')?.textContent.trim()") in ('Guardar', 'Save'), 'Save label')
            before = len(board.snapshot()['tasks'])
            # Beyond the state's window: a year's rules arrive from the service and its DST limits still hold.
            far = []
            for value in ('2040-01-15T10:00', '2041-03-10T02:30', '2041-11-03T01:30', '9999-06-01T10:00'):
                page(f"()=>{{const el=document.querySelector('#capture-due');el.value='{value}';el.dispatchEvent(new Event('input',{{bubbles:true}}));return true}}")
                axi('run', script="await page.wait(500);")
                far.append(page("()=>document.querySelector('#capture-due').nextElementSibling.textContent"))
            report[locale]['far'] = far
            check('10:00 GMT-5' in far[0] and ('ene' in far[0] if locale == 'es' else 'Jan' in far[0]), f'Far date in service rules: {far}')
            check(far[1] == far[2] == refusal, f'Far nonexistent and repeated hours keep their refusal: {far}')
            check(far[3] == ('No se pudo confirmar esta fecha. No se guardó nada; inténtalo de nuevo.' if locale == 'es' else 'This date could not be confirmed. Nothing was saved; try again.'), f'Unconfirmed is not called nonexistent: {far}')
            for value in ('2026-11-01T01:30', '2041-11-03T01:30', '9999-06-01T10:00'):
                page(f"()=>{{document.querySelector('#capture-title').value='Rechazada';const el=document.querySelector('#capture-due');el.value='{value}';el.dispatchEvent(new Event('input',{{bubbles:true}}));document.querySelector('#capture-dialog form').requestSubmit();return true}}")
                axi('run', script="await page.wait(500);")
            check(len(board.snapshot()['tasks']) == before, 'Refused times save nothing')
            title = 'Pagar luz ' + locale
            page(f"()=>{{document.querySelector('#capture-title').value='{title}';const el=document.querySelector('#capture-due');el.value='2026-11-09T14:30';el.dispatchEvent(new Event('input',{{bubbles:true}}));document.querySelector('#capture-dialog form').requestSubmit();return true}}")
            axi('run', script=f"await page.wait(800);")
            if locale == 'es':
                axi('screenshot', str(out / 'today-es.png'))
            # A far deadline and event from the forms, with a year not yet loaded in this page.
            year = {'es': 2042, 'en': 2043}[locale]
            axi('run', script="await page.click('#capture-open');await page.wait('#capture-due');")
            page(f"()=>{{document.querySelector('#capture-title').value='Lejana {locale}';const el=document.querySelector('#capture-due');el.value='{year}-01-15T10:00';document.querySelector('#capture-dialog form').requestSubmit();return true}}")
            axi('run', script="await page.wait(900);")
            far_due = next(t['due'] for t in board.snapshot()['tasks'] if t['title'] == f'Lejana {locale}')
            check(far_due == f'{year}-01-15T15:00:00Z', f'Far deadline: {far_due}')
            axi('run', script="await page.click('#capture-open');await page.wait('#capture-kind');")
            page(f"()=>{{const k=document.querySelector('#capture-kind');k.value='event';k.dispatchEvent(new Event('change',{{bubbles:true}}));document.querySelector('#capture-title').value='Evento lejano {locale}';document.querySelector('#capture-start').value='{year + 2}-07-01T10:00';document.querySelector('#capture-end').value='{year + 2}-07-01T11:00';document.querySelector('#capture-dialog form').requestSubmit();return true}}")
            axi('run', script="await page.wait(900);")
            far_event = next((e['start_at'], e['end_at']) for e in board.snapshot()['calendar_events'] if e['title'] == f'Evento lejano {locale}')
            check(far_event == (f'{year + 2}-07-01T14:00:00Z', f'{year + 2}-07-01T15:00:00Z'), f'Far event: {far_event}')
            # A fresh page has only the state: stored far dates still show in the service's rules.
            axi('run', script="await page.eval(()=>location.reload());await page.wait('[data-task-id]');await page.wait(400);")
            shown = page(f"()=>[[...document.querySelectorAll('[data-task-id]')].map(c=>c.getAttribute('data-task-id')&&c.textContent).find(x=>x?.includes('Lejana {locale}')),[...document.querySelectorAll('.daily-row')].map(x=>x.textContent).find(t=>t.includes('Evento lejano {locale}'))]")
            check('10:00' in shown[0] and '10:00 GMT-4' in shown[1] and '11:00 GMT-4' in shown[1], f'Stored far dates after reload: {shown}')
            report[locale]['farStored'] = {'due': far_due, 'event': far_event, 'shown': shown}
            page(f"()=>{{const e=document.getElementById('locale-choice');e.value='{locale}';e.dispatchEvent(new Event('change',{{bubbles:true}}));return true}}")
            due = next(t['due'] for t in board.snapshot()['tasks'] if t['title'] == title)
            check(due == '2026-11-09T19:30:00Z', f'Typed 14:30 is 14:30 for the service: {due}')
            row = page(f"()=>[...document.querySelectorAll('[data-task-id]')].map(c=>c.textContent).find(x=>x.includes('{title}'))")
            check('14:30' in row, f'Row shows the typed time: {row}')
            report[locale]['row'] = row
            events = page("()=>[...document.querySelectorAll('.daily-row')].map(x=>x.textContent).find(t=>t.includes('Cita repetida'))")
            check('01:30 GMT-5' in events and '03:00 GMT-5' in events, f'Stored event shows its service-local times: {events}')
            report[locale]['event'] = events
        # Header and snooze presets resolve in the service's rules.
        # The row's deadline menu reaches the same rules.
        axi('run', script="await page.eval(()=>[...document.querySelectorAll('[data-task-id]')].find(c=>c.textContent.includes('Lejana en')).querySelector('.task-due').click());await page.wait('[data-menu-input=due]');")
        page("()=>{const el=document.querySelector('[data-menu-input=due]');el.value='2045-02-01T08:00';el.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-menu-act=due-custom]').click();return true}")
        axi('run', script="await page.wait(900);")
        moved = next(t['due'] for t in board.snapshot()['tasks'] if t['title'] == 'Lejana en')
        check(moved == '2045-02-01T13:00:00Z', f'Far deadline from the row menu: {moved}')
        report['header'] = page("()=>document.querySelector('.date').textContent")
        check('thursday, november 5' in report['header'].lower(), report['header'])
        axi('run', script=f"await page.eval(()=>[...document.querySelectorAll('[data-task-id]')].find(c=>c.textContent.includes('Posponer prueba')).querySelector('[data-action=snooze]').click());await page.wait('.row-menu');")
        axi('screenshot', str(out / 'snooze-en.png'))
        report['snooze'] = page("()=>[...document.querySelectorAll('.row-menu [data-menu-act=snooze]')].map(b=>[b.textContent,b.dataset.value,b.title])")
        check(report['snooze'][0][1].startswith('2026-11-05T18:00') and '13:00' in report['snooze'][0][0], f'In one hour: {report["snooze"][0]}')
        page("()=>{[...document.querySelectorAll('.row-menu [data-menu-act=snooze]')].find(b=>/Tomorrow/.test(b.textContent)).click();return true}")
        axi('run', script="await page.wait(800);")
        until = next(t['until'] for t in board.snapshot()['tasks'] if t['id'] == snoozed)
        check(until == '2026-11-06T14:00:00Z', f'Tomorrow 09:00 is 09:00 for the service: {until}')
        # Editing the stored repeated-hour event: unchanged start keeps its instant; a new end uses service rules.
        axi('run', script="await page.eval(()=>[...document.querySelectorAll('.daily-row .link-button')].find(b=>b.textContent==='Cita repetida').click());await page.wait('#capture-start');")
        report['eventEdit'] = page("()=>{const el=document.querySelector('#capture-end');el.value='2027-11-07T04:00';el.dispatchEvent(new Event('input',{bubbles:true}));const values=[document.querySelector('#capture-start').value,el.value,el.nextElementSibling.textContent];document.querySelector('#capture-dialog form').requestSubmit();return values}")
        axi('run', script="await page.wait(800);")
        stored = [(e['start_at'], e['end_at']) for e in board.snapshot()['calendar_events']]
        check(('2027-11-07T06:30:00Z', '2027-11-07T09:00:00Z') in stored, f'Event edit keeps start and resolves end in service rules: {stored}')
        console = axi('console')
        check('<no console messages found>' in console, console)
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
