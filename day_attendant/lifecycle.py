"""Owned local service lifecycle and native Linux/macOS scheduler adapters."""
from datetime import timedelta
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import time

from .errors import AssistantError
from .platforms import capabilities,exclusive
from .profiles import state_home
from .service import encoded
from .timeutil import parse

def argv(app,*args):
    launcher="import runpy,sys;sys.path.insert(0,"+repr(str(Path(__file__).resolve().parent.parent))+");runpy.run_module('day_attendant',run_name='__main__')"
    return [sys.executable,'-B','-c',launcher,'--state',str(app.store.path),*args]

def receipt(app):
    try:
        record=json.loads((app.store.path/'ui-server.json').read_text())
        from .web import client
        data=client(app.store.path)
        if data['profile']['id']!=app.store.state_value('profile')['id']:raise ValueError('profile mismatch')
        return record
    except (OSError,ValueError,KeyError,AssistantError):return None


def status(app):
    rec=receipt(app)
    return {'running':bool(rec),'origin':rec['origin'] if rec else None,'pid':rec['pid'] if rec else None,
            'state':str(app.store.path),'scheduler':app.store.state_value('scheduler'),'last_tick':app.store.state_value('last_tick'),
            'last_backup':app.store.state_value('last_backup'),'notification_policy':app.store.state_value('notification_policy')}


def start(app,port=0):
    with exclusive(app.store.path/'lifecycle.lock',timeout=2):
        existing=receipt(app)
        if existing:return {'running':True,'already_running':True,'origin':existing['origin']}
        try:
            with exclusive(app.store.path/'server.lock',timeout=0):pass
        except AssistantError as error:
            if error.code=='busy':return {'running':False,'starting':True,'state':str(app.store.path)}
            raise
        log=app.store.path/'service.log'
        if log.exists() and log.stat().st_size>2*1024*1024:os.replace(log,log.with_suffix('.previous.log'))
        fd=os.open(log,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
        with os.fdopen(fd,'ab') as output:
            child=subprocess.Popen(argv(app,'web','--supervised','--port',str(port)),stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True,close_fds=True)
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            rec=receipt(app)
            if rec:return {'running':True,'origin':rec['origin'],'pid':rec['pid']}
            if child.poll() is not None:raise AssistantError('service_start_failed','The service exited. Inspect the private service.log or run doctor.',{'log':str(log)})
            time.sleep(.2)
        raise AssistantError('service_starting','The service is still starting. Run service status; another start will not create a second owner.',{'log':str(log)})


def stop(app):
    rec=receipt(app)
    if not rec:return {'running':False,'already_stopped':True}
    from http.client import HTTPException
    from .web import client
    try:client(app.store.path,'/api/shutdown',{})
    except (AssistantError,HTTPException):pass  # A reply cut short by the exit still means it is stopping.
    # The old owner is gone only once it releases the server lock, so a restart can reuse its address.
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        try:
            with exclusive(app.store.path/'server.lock',timeout=0):return {'running':False}
        except AssistantError as error:
            if error.code!='busy':raise
        time.sleep(.1)
    raise AssistantError('service_stopping','Shutdown is still finishing; the service was not forcibly killed.')


def session_url(app):
    rec=receipt(app)
    if not rec:raise AssistantError('server_unavailable','Start the service first.')
    return rec['origin']+'/#session='+rec['token']


def run_checked(args,input=None,allow_missing=False):
    try:run=subprocess.run(args,input=input,text=True,capture_output=True,timeout=20)
    except (OSError,subprocess.TimeoutExpired) as error:raise AssistantError('scheduler_unavailable','The native scheduler command could not complete.') from error
    if run.returncode and not allow_missing:raise AssistantError('scheduler_failed','The native scheduler rejected the operation.',{'command':args[0],'returncode':run.returncode,'error':run.stderr[:400]})
    return run


def scheduler_environment():
    # Only delivery/session discovery values; never persist inherited auth or API keys.
    result={'PATH':os.environ.get('PATH','/usr/local/bin:/usr/bin:/bin')}
    for key in ('DISPLAY','DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR','XAUTHORITY','TZ'):
        if os.environ.get(key):result[key]=os.environ[key]
    return result


def cron_block(app,environment):
    identity=app.store.state_value('profile')['id'];marker='# day-attendant:'+identity
    env=['/usr/bin/env',*[k+'='+v for k,v in environment.items()]]
    # crontab treats percent before shell quoting; always escape it at this layer.
    tick=shlex.join(env+argv(app,'tick')).replace('%',r'\%')
    log=shlex.quote(str(app.store.path/'scheduler.log')).replace('%',r'\%')
    return marker+':begin\n* * * * * '+tick+' >> '+log+' 2>&1\n'+marker+':end\n'


def without_block(text,identity):
    begin='# day-attendant:'+identity+':begin';end='# day-attendant:'+identity+':end'
    output=[];inside=False;found=0
    for line in text.splitlines(keepends=True):
        if line.rstrip('\r\n')==begin:
            if inside:raise AssistantError('scheduler_conflict','The owned scheduler block is malformed.')
            inside=True;found+=1;continue
        if line.rstrip('\r\n')==end:
            if not inside:raise AssistantError('scheduler_conflict','The owned scheduler block is malformed.')
            inside=False;continue
        if not inside:output.append(line)
    if inside or found>1:raise AssistantError('scheduler_conflict','The owned scheduler block is malformed; unrelated jobs were preserved.')
    return ''.join(output)


def scheduler(app,operation):
    if operation not in ('install','uninstall','status'):raise AssistantError('invalid_arguments','Unknown scheduler operation.')
    if operation=='status':return app.store.state_value('scheduler') or {'installed':False}
    caps=capabilities();environment=scheduler_environment()
    backend='launchd' if sys.platform=='darwin' else 'systemd-user' if caps['user_systemd'] else 'cron' if caps['crontab'] else None
    scheduler_home=state_home();scheduler_home.mkdir(parents=True,exist_ok=True,mode=0o700)
    with exclusive(scheduler_home/'scheduler-edit.lock',timeout=5):
        previous=app.store.state_value('scheduler')
        if operation=='uninstall' and previous:backend=previous['backend']
        if not backend:raise AssistantError('scheduler_unavailable','No supported native scheduler is available. Manual tick and service commands remain usable.')
        if operation=='install' and previous and previous['installed'] and previous['backend']!=backend:
            _scheduler_edit(app,'uninstall',previous['backend'],caps,environment)
        return _scheduler_edit(app,operation,backend,caps,environment)


def _scheduler_edit(app,operation,backend,caps,environment):
    identity=app.store.state_value('profile')['id']
    log=app.store.path/'scheduler.log'
    fd=os.open(log,os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600);os.close(fd);log.chmod(0o600)
    label='local.day-attendant.'+identity;paths=[]
    if backend=='cron':
        binary=caps['crontab'] or shutil.which('crontab')
        current=run_checked([binary,'-l'],allow_missing=True)
        if current.returncode and 'no crontab' not in current.stderr.lower():raise AssistantError('scheduler_unavailable','Existing crontab could not be read; it was preserved.')
        cleaned=without_block(current.stdout,identity)
        updated=cleaned
        if operation=='install':updated=cleaned+('' if not cleaned or cleaned.endswith('\n') else '\n')+cron_block(app,environment)
        if updated!=current.stdout:
            fresh=run_checked([binary,'-l'],allow_missing=True)
            if fresh.stdout!=current.stdout or fresh.returncode!=current.returncode:raise AssistantError('scheduler_conflict','The crontab changed while preparing this operation. Retry; no jobs were overwritten.')
            run_checked([binary,'-'],input=updated)
        check=run_checked([binary,'-l'],allow_missing=True).stdout
        if check!=updated:raise AssistantError('scheduler_verification_failed','The installed crontab differs from the requested content; inspect scheduler status.')
    elif backend=='launchd':
        folder=Path.home()/'Library/LaunchAgents';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(label+'.plist');paths=[str(path)];domain='gui/'+str(os.getuid())
        run_checked(['launchctl','bootout',domain+'/'+label],allow_missing=True)
        if operation=='install':
            payload={'Label':label,'ProgramArguments':argv(app,'tick'),'StartInterval':60,'RunAtLoad':True,
                     'EnvironmentVariables':environment,'StandardOutPath':str(app.store.path/'scheduler.log'),'StandardErrorPath':str(app.store.path/'scheduler.log')}
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'wb') as stream:plistlib.dump(payload,stream)
            run_checked(['launchctl','bootstrap',domain,str(path)])
        else:path.unlink(missing_ok=True)
    else:
        folder=Path.home()/'.config/systemd/user';folder.mkdir(parents=True,exist_ok=True)
        service=folder/(label+'.service');timer=folder/(label+'.timer');paths=[str(service),str(timer)]
        run_checked(['systemctl','--user','disable','--now',timer.name],allow_missing=not timer.exists())
        if operation=='install':
            quote=lambda value:'"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
            service.write_text('[Unit]\nDescription=Day Attendant known-data reminders\n[Service]\nType=oneshot\nExecStart='+ ' '.join(quote(x) for x in argv(app,'tick'))+'\n'+''.join('Environment='+quote(k+'='+v)+'\n' for k,v in environment.items()))
            timer.write_text('[Unit]\nDescription=Day Attendant reminder tick\n[Timer]\nOnBootSec=1min\nOnUnitActiveSec=1min\nPersistent=true\n[Install]\nWantedBy=timers.target\n')
            service.chmod(0o600);timer.chmod(0o600);run_checked(['systemctl','--user','daemon-reload']);run_checked(['systemctl','--user','enable','--now',timer.name])
        else:
            service.unlink(missing_ok=True);timer.unlink(missing_ok=True);run_checked(['systemctl','--user','daemon-reload'])
    record={'installed':operation=='install','backend':backend,'profile_id':identity,'label':label,'paths':paths,'at':app.now(),
            'policy':'Known-data reminders and daily backup only; no model requests; reevaluate when runnable, no suspend wake guarantee.'}
    with app.store.lock(),app.store.connect() as con:con.execute("INSERT OR REPLACE INTO state VALUES('scheduler',?)",(encoded(record),))
    return record

def tick(app):
    """One bounded native scheduling invocation. No harness imports or model calls."""
    from zoneinfo import ZoneInfo
    from .archives import snapshot,recover_maintenance
    recover_maintenance(app)
    with exclusive(app.store.path/'tick.lock',timeout=0):
        log=app.store.path/'scheduler.log'
        if log.exists() and log.stat().st_size>1024*1024:os.replace(log,log.with_suffix('.previous.log'))
        now=app.clock();previous=app.store.state_value('last_tick');policy=app.store.state_value('notification_policy')
        if not policy:raise AssistantError('profile_required','Initialize a daily profile before scheduling it.')
        if previous:
            elapsed=(now-parse(previous['at'])).total_seconds()
            if 0<=elapsed<policy['intervalMinutes']*60:return {'skipped':'interval','last_tick':previous}
            if elapsed<0:return {'skipped':'clock_moved_back','last_tick':previous}
        record={'at':app.now(),'status':'running','model_called':False}
        with app.store.lock(),app.store.connect() as con:con.execute("INSERT OR REPLACE INTO state VALUES('last_tick',?)",(encoded(record),))
        record['status']='completed'
        try:record['delivery']=app.notify()
        except AssistantError as error:
            record.update(status='failed',error={'code':error.code,'message':str(error)})
            record['delivery']={'error':record['error']}
        try:
            last=app.store.state_value('last_backup');zone=ZoneInfo(app.store.state_value('timezone','UTC'))
            if not last or parse(last['captured_at']).astimezone(zone).date()<now.astimezone(zone).date():
                record['backup']=snapshot(app,category='daily',key='daily:'+now.astimezone(zone).date().isoformat())
        except AssistantError as error:
            failure={'code':error.code,'message':str(error)}
            record.update(status='failed',backup={'error':failure});record.setdefault('error',failure)
        finally:
            with app.store.lock(),app.store.connect() as con:con.execute("INSERT OR REPLACE INTO state VALUES('last_tick',?)",(encoded(record),))
        return record
