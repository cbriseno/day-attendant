"""Portable profile configuration; platform paths are an adapter concern."""
from datetime import datetime
from pathlib import Path
import os
import sys
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import AssistantError


def state_home(platform=None):
    platform = platform or sys.platform
    if platform == 'darwin':
        return Path.home() / 'Library/Application Support/Day Attendant'
    return Path(os.environ.get('XDG_STATE_HOME', Path.home()/'.local/state')) / 'day-attendant'


def default_state():
    return Path(os.environ.get('DAY_ATTENDANT_STATE', state_home()/'profiles/default')).expanduser()


def local_zone():
    candidate = os.environ.get('TZ')
    if not candidate:
        try:
            target = str(Path('/etc/localtime').resolve())
            candidate = target.split('/zoneinfo/', 1)[1] if '/zoneinfo/' in target else None
        except OSError:
            pass
    if not candidate:
        try:candidate = Path('/etc/timezone').read_text().strip()
        except OSError:pass
    try:
        ZoneInfo(candidate or 'UTC')
        return candidate or 'UTC'
    except ZoneInfoNotFoundError:
        return 'UTC'


def new_profile(path, kind, zone=None, name=None):
    if kind not in ('personal', 'synthetic'):
        raise AssistantError('invalid_profile', 'Choose a personal or synthetic profile.')
    zone = zone or local_zone()
    try:ZoneInfo(zone)
    except (ZoneInfoNotFoundError, TypeError) as error:
        raise AssistantError('invalid_zone', 'Choose an available IANA time zone.') from error
    return {'id': str(uuid.uuid4()), 'kind': kind, 'name': name or Path(path).name,
            'timezone': zone, 'locale': 'en', 'harness': 'codex', 'model': None,
            'created_at': datetime.now().astimezone().isoformat()}


def default_policy():
    # A visible, editable delivery default; never a claim of human availability.
    return {'enabled': True, 'timezone': None, 'weekdays': [0,1,2,3,4],
            'start': '09:00', 'end': '18:00', 'quietStart': '22:00', 'quietEnd': '08:00',
            'pausedUntil': None, 'intervalMinutes': 10, 'unchangedRepeatMinutes': 1440,
            'dailyBudget': 12, 'technicalDefault': True}


def validate_policy(value):
    allowed = set(default_policy())
    if not isinstance(value, dict) or set(value)-allowed:
        raise AssistantError('invalid_policy', 'Unknown reminder policy fields.')
    result = dict(default_policy(), **value)
    if not isinstance(result['enabled'], bool):
        raise AssistantError('invalid_policy', 'Reminder delivery must be explicitly enabled or disabled.')
    days = result['weekdays']
    if not isinstance(days,list) or any(type(n) is not int or n not in range(7) for n in days):
        raise AssistantError('invalid_policy','Weekdays must be integers from Monday 0 to Sunday 6.')
    result['weekdays'] = sorted(set(days))
    for key in ('start','end','quietStart','quietEnd'):
        text = result[key]
        if not isinstance(text,str):raise AssistantError('invalid_policy','Use HH:MM delivery times.')
        try:
            parsed = datetime.strptime(text,'%H:%M')
            if parsed.strftime('%H:%M') != text:raise ValueError()
        except ValueError as error:raise AssistantError('invalid_policy','Use HH:MM delivery times.') from error
    if result['start']==result['end']:
        raise AssistantError('invalid_policy','The delivery window must have a nonzero duration.')
    for key,maximum in [('intervalMinutes',1440),('unchangedRepeatMinutes',10080),('dailyBudget',100)]:
        if type(result[key]) is not int or not 1<=result[key]<=maximum:
            raise AssistantError('invalid_policy','Reminder intervals and budgets must be positive bounded integers.')
    if result['timezone'] is not None:
        try:ZoneInfo(result['timezone'])
        except (ZoneInfoNotFoundError,TypeError) as error:raise AssistantError('invalid_zone','Invalid reminder time zone.') from error
    if result['pausedUntil'] is not None:
        from .timeutil import instant
        result['pausedUntil']=instant(result['pausedUntil'])
    if not isinstance(result['technicalDefault'],bool):raise AssistantError('invalid_policy','Invalid default-policy marker.')
    return result


def delivery_allowed(policy, now, zone):
    if not policy['enabled']:return False, 'disabled'
    if policy.get('pausedUntil'):
        from .timeutil import parse
        if parse(policy['pausedUntil'])>now:return False,'paused'
    local=now.astimezone(ZoneInfo(policy.get('timezone') or zone))
    at=local.strftime('%H:%M')
    def inside(start,end):return start<=at<end if start<end else at>=start or at<end
    if policy['quietStart']!=policy['quietEnd'] and inside(policy['quietStart'],policy['quietEnd']):return False,'quiet'
    # An overnight work window belongs to the day on which it started.
    day=local.weekday()
    if policy['start']>policy['end'] and at<policy['end']:day=(day-1)%7
    if day not in policy['weekdays'] or not inside(policy['start'],policy['end']):return False,'outside_window'
    return True,'allowed'


def invalidate_pending(con,at,reason):
    con.execute("UPDATE proposals SET phase='stale',generation=generation+1,error=? WHERE phase IN ('evaluating','ready','review','unavailable','stale')",(reason,))
    con.execute("UPDATE evaluations SET status='interrupted' WHERE status='running'")
    for row in con.execute("SELECT key FROM messages WHERE role='user' AND status='pending'").fetchall():
        import json
        con.execute("INSERT OR IGNORE INTO messages(key,role,text,status,data,at) VALUES(?,'assistant',?,'incomplete',?,?)",
            (row[0]+':assistant',reason,json.dumps({'system_code':'response_interrupted'}),at))
    con.execute("UPDATE messages SET status='incomplete',generation=generation+1 WHERE status IN ('pending','evaluating')")


def configure(app,values,key,expected_revision=None):
    """Authenticated typed settings; no model decides whether a preference is true."""
    from .service import encoded,fingerprint
    allowed={'name','timezone','locale','harness','model','notification_policy','backup_directory'}
    if not isinstance(values,dict) or set(values)-allowed or not isinstance(key,str) or not 1<=len(key)<=200:
        raise AssistantError('invalid_settings','Unknown settings or invalid request key.')
    digest=fingerprint(values)
    with app.store.lock(),app.store.connect() as con:
        old=con.execute('SELECT * FROM settings_changes WHERE key=?',(key,)).fetchone()
        if old:
            if old['digest']!=digest:raise AssistantError('idempotency_conflict','The settings key identifies another request.')
            import json
            return json.loads(old['result'])
        app.store.assert_writable();app.clean_read()
        if expected_revision is not None and expected_revision!=app.store.revision():raise AssistantError('stale_revision','Settings changed since this form was opened. Reopen it to compare.')
        current=app.store.state_value('profile')
        if not current:raise AssistantError('profile_required','Initialize a managed profile first.')
        profile=dict(current)
        for field in ('name','timezone','locale','harness','model'):
            if field in values:profile[field]=values[field]
        if not isinstance(profile['name'],str) or not 1<=len(profile['name'])<=100:raise AssistantError('invalid_settings','Use a short profile name.')
        try:ZoneInfo(profile['timezone'])
        except (TypeError,ZoneInfoNotFoundError) as error:raise AssistantError('invalid_zone','Invalid profile time zone.') from error
        if profile['locale'] not in ('en','es') or profile['harness'] not in ('codex','claude'):raise AssistantError('invalid_settings','Invalid interface locale or harness.')
        if profile['model'] is not None and (not isinstance(profile['model'],str) or not 1<=len(profile['model'])<=150 or any(c.isspace() for c in profile['model'])):
            raise AssistantError('invalid_settings','Use an exact model identifier or select the installed default.')
        if profile['harness']!=current['harness'] and 'model' not in values:profile['model']=None
        policy=app.store.state_value('notification_policy',default_policy())
        if 'notification_policy' in values:policy=validate_policy(values['notification_policy'])
        backup_directory=app.store.state_value('backup_directory')
        if 'backup_directory' in values:
            from .archives import directory
            if not isinstance(values['backup_directory'],str) or not values['backup_directory']:raise AssistantError('invalid_settings','Use an explicit local backup directory.')
            backup_directory=str(directory(app,values['backup_directory']))
        engine_changed=any(profile[k]!=current[k] for k in ('harness','model'))
        semantic_changed=engine_changed or profile['timezone']!=current['timezone']
        changed=profile!=current or policy!=app.store.state_value('notification_policy',default_policy()) or backup_directory!=app.store.state_value('backup_directory')
        revision=app.store.revision()
        if changed:
            con.execute("INSERT OR REPLACE INTO state VALUES('profile',?)",(encoded(profile),))
            con.execute("INSERT OR REPLACE INTO state VALUES('timezone',?)",(encoded(profile['timezone']),))
            con.execute("INSERT OR REPLACE INTO state VALUES('notification_policy',?)",(encoded(policy),))
            if backup_directory:con.execute("INSERT OR REPLACE INTO state VALUES('backup_directory',?)",(encoded(backup_directory),))
            # Locale/name are presentation preferences; changing them never invalidates reasoning.
            if semantic_changed:
                con.execute("INSERT OR REPLACE INTO state VALUES('context_epoch',?)",(encoded(app.store.epoch()+1),))
                invalidate_pending(con,app.now(),'Assistant configuration changed. Review pending reasoning again.')
            if semantic_changed or 'notification_policy' in values:
                revision+=1;con.execute("UPDATE state SET value=? WHERE key='revision'",(str(revision),))
        result={'profile':profile,'notification_policy':policy,'revision':revision,'engine_changed':engine_changed,'changed':changed}
        con.execute('INSERT INTO settings_changes VALUES(?,?,?,?,?)',(key,digest,encoded(values),encoded(result),app.now()))
        return result
