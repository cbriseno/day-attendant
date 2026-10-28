#!/usr/bin/env python3
"""Real cron lifecycle acceptance on an isolated D-Bus, never the desktop bus."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from day_attendant.connected import Connected
from day_attendant.service import Assistant
from day_attendant.profiles import configure,default_policy
from day_attendant import lifecycle


def main():
    if os.environ.get('DAY_ATTENDANT_ISOLATED_BUS')!='1':
        env=dict(os.environ,DAY_ATTENDANT_ISOLATED_BUS='1')
        return subprocess.call(['dbus-run-session','--config-file='+str(ROOT/'tests/fixtures/session.conf'),'--',sys.executable,'-B',__file__,*sys.argv[1:]],env=env)
    destination=Path(sys.argv[1]);report={'scope':'Actual user crontab + running cron daemon, private D-Bus, real notify-send, empty personal fixture; no model calls','checks':{}}
    original=subprocess.run(['crontab','-l'],capture_output=True,text=True).stdout
    report['original_crontab_sha256']=hashlib.sha256(original.encode()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='day-native-cron-') as tmp:
        folder=Path(tmp);(folder/'control.json').write_text('{"mode":"accept"}')
        bus=subprocess.Popen([sys.executable,'-B',str(ROOT/'tests/fixtures/notifications.py'),tmp],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        app=Assistant(folder/'profile');Connected(app).initialize(kind='personal',zone='America/Edmonton')
        policy=default_policy();policy.update(weekdays=list(range(7)),start='00:00',end='23:59',quietStart='00:00',quietEnd='00:00',intervalMinutes=1)
        configure(app,{'notification_policy':policy,'backup_directory':str(folder/'backups')},'cron-policy')
        # Fixture authored through the domain before connected mode is marked;
        # use a typed fact proposal whose controlled reviewer is irrelevant to cron.
        from datetime import timedelta
        sys.path.insert(0,str(ROOT/'tests'))
        from test_connected import ReviewFixture
        board=Connected(app,ReviewFixture());p=board.request('task_add',{'title':'[ACCEPTANCE] Private cron reminder','due':(app.clock()-timedelta(minutes=5)).isoformat()},'cron-known-task');board.evaluate(p['id'])
        installed=False
        try:
            start=time.monotonic()
            while not (folder/'ready').exists():
                if time.monotonic()-start>10:raise AssertionError('Private notification server not ready')
                time.sleep(.05)
            first=lifecycle.scheduler(app,'install');installed=True
            report['checks']['native_cron_selected']=first['backend']=='cron'
            before_second=subprocess.run(['crontab','-l'],capture_output=True,text=True).stdout
            second=lifecycle.scheduler(app,'install');after_second=subprocess.run(['crontab','-l'],capture_output=True,text=True).stdout
            report['checks']['install_idempotent']=before_second==after_second
            print(json.dumps({'stage':'installed','backend':first['backend'],'waiting_for_real_daemon':True}),flush=True)
            deadline=time.monotonic()+125
            while time.monotonic()<deadline:
                tick=app.store.state_value('last_tick')
                if tick and tick.get('status') in ('completed','failed'):break
                time.sleep(.5)
            else:raise AssertionError('Native cron daemon did not execute within 125 seconds')
            report['tick']=tick
            report['checks']['native_tick_completed']=tick['status']=='completed'
            report['checks']['no_model_called']=tick['model_called'] is False
            with app.store.connect() as con:
                attempts=[dict(r) for r in con.execute('SELECT status FROM attempts')]
            report['checks']['private_bus_accepted']=len(attempts)==1 and attempts[0]['status']=='accepted'
            report['checks']['daily_backup_verified']=bool(tick.get('backup'))
        except BaseException as error:
            report['error']={'type':type(error).__name__,'message':str(error)}
            raise
        finally:
            if installed:
                lifecycle.scheduler(app,'uninstall');lifecycle.scheduler(app,'uninstall')
            after=subprocess.run(['crontab','-l'],capture_output=True,text=True).stdout
            report['checks']['unrelated_crontab_unchanged']=after==original
            report['final_crontab_sha256']=hashlib.sha256(after.encode()).hexdigest()
            if bus.poll() is None:bus.terminate()
            _,err=bus.communicate(timeout=5)
            report['checks']['private_bus_clean_exit']=not bool(err)
            report['passed']=not report.get('error') and all(report['checks'].values())
            destination.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'checks':report['checks']}),flush=True)
        if not report['passed']:raise AssertionError('Native cron acceptance failed')
    return 0

if __name__=='__main__':raise SystemExit(main())
