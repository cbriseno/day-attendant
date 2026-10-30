"""Coherent local snapshots and offline, verified restoration to a new profile."""
from contextlib import contextmanager
from functools import wraps
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
import zipfile

from . import __version__
from .errors import AssistantError
from .platforms import exclusive
from .profiles import state_home,invalidate_pending,default_policy
from .taskstore import canonical
from .timeutil import parse,stamp

FORMAT=2
MAX_TOTAL=2*1024**3
RUNTIME_KEYS=('maintenance','chat_thread','chat_threads','agent_startup_error','agent_runtime','service_runtime','backup_running')
BASE_FILES=('assistant.sqlite3','tasks.sqlite3')
LEGACY_FILES=('assistant.sqlite3','taskwarrior/taskrc','taskwarrior/data/taskchampion.sqlite3')


def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def digest(data):return hashlib.sha256(data).hexdigest()
def task_digest(rows):return digest(encoded(sorted((canonical(r) for r in rows),key=lambda r:r['uuid'])).encode())


def storage_errors(function):
    @wraps(function)
    def checked(*args,**kwargs):
        try:return function(*args,**kwargs)
        except AssistantError:raise
        except (OSError,sqlite3.Error,zipfile.BadZipFile,ValueError) as error:
            raise AssistantError('storage_error','The storage operation did not finish. Existing profiles and backups were not overwritten.',{'operation':function.__name__,'errno':getattr(error,'errno',None)}) from error
    return checked


def directory(app,override=None):
    profile=app.store.state_value('profile') or {'id':str(uuid.uuid5(uuid.NAMESPACE_URL,str(app.store.path)))}
    selected=Path(override or app.store.state_value('backup_directory') or state_home()/'backups'/profile['id']).expanduser().resolve()
    checkout=Path(__file__).resolve().parent.parent
    if selected==app.store.path or app.store.path in selected.parents or selected==checkout or checkout in selected.parents:
        raise AssistantError('unsafe_backup_path','Backups must be outside the profile and checkout.')
    selected.mkdir(parents=True,exist_ok=True,mode=0o700)
    return selected


def alive(pid):
    try:os.kill(int(pid),0);return True
    except ProcessLookupError:return False
    except (OSError,ValueError,TypeError):return True


def recover_maintenance(app):
    """Kernel ownership, not PID age/reuse, decides whether maintenance is live."""
    if not app.store.state_value('maintenance'):return {'recovered':False}
    try:
        with exclusive(app.store.path/'maintenance.lock',timeout=0):
            with app.store.lock(),app.store.connect() as con:
                previous=app.store.state_value('maintenance')
                if not previous:return {'recovered':False}
                con.execute("DELETE FROM state WHERE key='maintenance'")
                con.execute("INSERT OR REPLACE INTO state VALUES('maintenance_recovery',?)",(encoded({'previous':previous,'at':app.now(),'reason':'Kernel maintenance owner no longer holds its lock'}),))
                con.execute("INSERT OR REPLACE INTO state VALUES('context_epoch',?)",(encoded(app.store.epoch()+1),))
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='proposals'").fetchone():invalidate_pending(con,app.now(),'Interrupted maintenance was recovered. Review pending reasoning again.')
                return {'recovered':True,'previous':previous}
    except AssistantError as error:
        if error.code=='busy':return {'recovered':False,'active':True}
        raise


@contextmanager
def maintenance(app,reason,timeout=20):
    token=str(uuid.uuid4());entered=False
    with exclusive(app.store.path/'maintenance.lock',timeout=0):
        try:
            with app.store.lock(),app.store.connect() as con:
                flag={'token':token,'pid':os.getpid(),'reason':reason,'at':app.now()}
                con.execute("INSERT OR REPLACE INTO state VALUES('maintenance',?)",(encoded(flag),))
                con.execute("INSERT OR REPLACE INTO state VALUES('context_epoch',?)",(encoded(app.store.epoch()+1),))
                if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proposals'").fetchone():invalidate_pending(con,app.now(),'Maintenance interrupted this evaluation. Retry it after maintenance.')
                entered=True
            # Transports hold a shared kernel lock until their result is committed.
            # Never infer a live notifier's termination from an expired lease.
            try:
                with exclusive(app.store.path/'delivery.lock',timeout=timeout):
                    with app.store.lock(),app.store.connect() as con:
                        con.execute("UPDATE attempts SET status='uncertain',error='No active transport owner; prior outcome unknown at maintenance' WHERE status='sending'")
                        con.execute("UPDATE notices SET status='uncertain',active_attempt_id=NULL,error='No active transport owner; prior outcome unknown at maintenance' WHERE status='sending'")
                    yield token
            except AssistantError as error:
                if error.code=='busy':raise AssistantError('maintenance_busy','An active transport has not quiesced. No backup was published; retry after its result is committed.') from error
                raise
        finally:
            if entered:
                with app.store.lock(),app.store.connect() as con:
                    current=app.store.state_value('maintenance')
                    if current and current.get('token')==token:con.execute("DELETE FROM state WHERE key='maintenance'")


def sqlite_copy(source,target):
    if not source.is_file():raise AssistantError('backup_missing_store','A required SQLite store is missing.')
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(target) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise AssistantError('backup_corrupt','SQLite integrity validation failed.')
    os.chmod(target,0o600)


@storage_errors
def snapshot(app,destination=None,category='manual',key=None,timeout=20):
    if category not in ('manual','daily','migration','reconciliation'):raise AssistantError('invalid_backup','Invalid backup category.')
    folder=directory(app,destination)
    with exclusive(app.store.path/'backup.lock',timeout=0):
        receipts=app.store.state_value('backup_receipts',{})
        if key and key in receipts:
            cached=receipts[key]
            if Path(cached['path']).parent!=folder or cached['category']!=category:raise AssistantError('idempotency_conflict','The backup key identifies another destination or category.')
            verify(cached['path']);return cached
        with tempfile.TemporaryDirectory(prefix='.day-snapshot-',dir=folder) as temporary:
            staging=Path(temporary)
            with maintenance(app,'backup:'+category,timeout):
                with app.store.lock():
                    # Snapshot observed stores without advancing any admitted
                    # operation. A recovery backup must never execute the action
                    # the human is deciding whether to abandon.
                    with app.store.connect() as con:schema=con.execute('PRAGMA user_version').fetchone()[0]
                    profile=app.store.state_value('profile') or {'id':str(uuid.uuid5(uuid.NAMESPACE_URL,str(app.store.path))),'kind':'synthetic','name':app.store.path.name,'timezone':app.store.state_value('timezone','UTC')}
                    selected=list(app.tasks.storage_files)
                    with app.store.connect() as con:
                        has_images=con.execute("SELECT 1 FROM sqlite_master WHERE name='attachments'").fetchone()
                        images=[dict(r) for r in con.execute('SELECT * FROM attachments')] if has_images else []
                    for image in images:
                        rel=image['relative_path'];path=(app.store.path/rel).resolve()
                        if not path.is_relative_to((app.store.path/'attachments').resolve()) or not path.is_file() or digest(path.read_bytes())!=image['sha256']:
                            raise AssistantError('attachment_corrupt','A selected image failed backup verification.')
                        selected.append(rel)
                    size=sum((app.store.path/rel).stat().st_size for rel in selected if (app.store.path/rel).exists())
                    if size>MAX_TOTAL or shutil.disk_usage(folder).free<size*2+10*1024*1024:raise AssistantError('backup_space','There is not enough free space for a coherent staged backup.')
                    for rel in app.tasks.storage_files:
                        if rel.endswith('.sqlite3'):sqlite_copy(app.store.path/rel,staging/rel)
                    for rel in selected:
                        if rel.endswith('.sqlite3'):continue
                        source=app.store.path/rel
                        if source.is_symlink() or not source.is_file():raise AssistantError('backup_unsafe_file','A required file is missing or is a symbolic link.')
                        target=staging/rel;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copyfile(source,target);os.chmod(target,0o600)
                    with sqlite3.connect(staging/'assistant.sqlite3') as con:
                        for name in RUNTIME_KEYS:con.execute('DELETE FROM state WHERE key=?',(name,))
                    captured=app.now();revision=app.store.revision()
                    manifest={'format':app.tasks.storage_format,'task_backend':app.tasks.backend,'application':'Day Attendant','application_version':__version__,'state_schema':schema,'profile':profile,
                        'captured_at':captured,'revision':revision,'category':category,'task_fingerprint':task_digest(app.tasks.rows()),
                        'recovery_required':bool(app.store.unresolved()),'files':{}}
                    for rel in selected:
                        data=(staging/rel).read_bytes();manifest['files'][rel]={'sha256':digest(data),'bytes':len(data)}
            # Compression uses immutable staged files; normal reads/writes can resume.
            filename='day-attendant-'+profile['id']+'-'+captured.replace(':','').replace('-','')+'-'+uuid.uuid4().hex[:8]+'-'+category+'.zip'
            final=folder/filename;partial=staging/'snapshot.zip'
            fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:
                with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('manifest.json',encoded(manifest))
                    for rel in selected:archive.write(staging/rel,rel)
                stream.flush();os.fsync(stream.fileno())
            verify(partial)
            os.replace(partial,final)
            result={'path':str(final),'sha256':digest(final.read_bytes()),'captured_at':captured,'revision':revision,'category':category,'recovery_required':manifest['recovery_required']}
            with app.store.lock(),app.store.connect() as con:
                previous=app.store.state_value('last_backup')
                if not previous or previous['captured_at']<=captured:con.execute("INSERT OR REPLACE INTO state VALUES('last_backup',?)",(encoded(result),))
                if key:
                    receipts=app.store.state_value('backup_receipts',{});receipts[key]=result
                    # Delivery receipts are bounded; archived files remain independent.
                    receipts=dict(list(receipts.items())[-100:]);con.execute("INSERT OR REPLACE INTO state VALUES('backup_receipts',?)",(encoded(receipts),))
            if category=='daily':prune(folder,profile['id'])
            return result


def verify(filename):
    try:
        with zipfile.ZipFile(filename) as archive:
            entries=archive.infolist();names=[e.filename for e in entries]
            if len(entries)>10000 or len(set(names))!=len(names) or sum(e.file_size for e in entries)>MAX_TOTAL:raise ValueError('archive limits')
            for entry in entries:
                path=PurePosixPath(entry.filename);mode=entry.external_attr>>16
                if entry.is_dir() or path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename or stat.S_ISLNK(mode) or entry.flag_bits&1:raise ValueError('unsafe member')
            if 'manifest.json' not in names:raise ValueError('manifest missing')
            if archive.getinfo('manifest.json').file_size>2*1024*1024:raise ValueError('manifest too large')
            manifest=json.loads(archive.read('manifest.json'))
            if manifest.get('format') not in (1,FORMAT) or manifest.get('application')!='Day Attendant' or manifest.get('state_schema') not in (3,4):raise ValueError('unsupported format')
            files=manifest['files']
            base=BASE_FILES if manifest['format']==FORMAT else LEGACY_FILES
            backend='python-sqlite' if manifest['format']==FORMAT else 'taskwarrior'
            if manifest.get('task_backend',backend)!=backend:raise ValueError('task backend mismatch')
            if not isinstance(files,dict) or set(names)!=set(files)|{'manifest.json'} or not set(base)<=set(files):raise ValueError('incomplete stores')
            if manifest['profile']['kind'] not in ('personal','synthetic'):raise ValueError('profile kind')
            uuid.UUID(manifest['profile']['id'])
            for name,expected in files.items():
                if name not in base and not name.startswith('attachments/'):raise ValueError('unexpected member')
                if expected['bytes']!=archive.getinfo(name).file_size:raise ValueError('size mismatch')
                checksum=hashlib.sha256()
                with archive.open(name) as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):checksum.update(chunk)
                if checksum.hexdigest()!=expected['sha256']:raise ValueError('checksum mismatch')
            return manifest
    except (OSError,ValueError,KeyError,TypeError,zipfile.BadZipFile,RuntimeError) as error:
        raise AssistantError('invalid_backup','The archive failed its format, path, size or integrity checks.') from error


def safe_taskrc(path):
    allowed={'confirmation':'off','news.version':'3.5.0','hooks':'off'}
    for line in path.read_text().splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        key,separator,value=line.partition('=')
        if not separator or key.strip() not in allowed or value.strip()!=allowed[key.strip()]:raise AssistantError('invalid_backup','The restored Taskwarrior configuration contains unsupported directives.')


def inspect_database(path):
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchall():raise AssistantError('invalid_backup','The restored SQLite store is inconsistent.')
        if con.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone():raise AssistantError('invalid_backup','Unexpected executable SQLite schema objects.')


@storage_errors
def restore(archive_path,target,legacy_task_bin=None):
    archive_path=Path(archive_path).expanduser().resolve();manifest=verify(archive_path)
    if manifest['format']==1 and not legacy_task_bin:
        raise AssistantError('legacy_backup','This Taskwarrior-era backup needs one explicit conversion on a machine with Taskwarrior 3.5.0: restore with --taskwarrior-bin PATH. New backups use only Python/SQLite.')
    target=Path(target).expanduser().resolve();checkout=Path(__file__).resolve().parent.parent
    if target==checkout or checkout in target.parents:raise AssistantError('unsafe_state_path','Restore outside the checkout.')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):raise AssistantError('restore_target_exists','Restore to a new or empty directory. Existing profiles are never overwritten.')
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.day-restore-',dir=target.parent) as temporary:
        staging=Path(temporary)/'profile';staging.mkdir(mode=0o700)
        with zipfile.ZipFile(archive_path) as archive:
            for name in manifest['files']:
                output=staging/name;output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with archive.open(name) as src,output.open('xb') as dst:shutil.copyfileobj(src,dst,1024*1024)
                output.chmod(0o600)
        inspect_database(staging/'assistant.sqlite3')
        if manifest['format']==1:
            safe_taskrc(staging/'taskwarrior/taskrc')
            inspect_database(staging/'taskwarrior/data/taskchampion.sqlite3')
            from .migration import import_legacy_rows
            import_legacy_rows(staging,legacy_task_bin)
        inspect_database(staging/'tasks.sqlite3')
        from .service import Assistant
        from .connected import Connected
        app=Assistant(staging)
        board=Connected(app)
        if task_digest(app.tasks.rows())!=manifest['task_fingerprint']:raise AssistantError('invalid_backup','Native responsibility identity or content does not match the snapshot.')
        with app.store.connect() as con:
            existing=app.store.state_value('profile')
            if existing and (existing['id']!=manifest['profile']['id'] or existing['kind']!=manifest['profile']['kind']):raise AssistantError('invalid_backup','The manifest does not identify the included profile.')
            for name in RUNTIME_KEYS:con.execute('DELETE FROM state WHERE key=?',(name,))
        board.initialize(kind=manifest['profile']['kind'],_upgrade_backup=str(archive_path))
        native={r['uuid']:r for r in app.tasks.rows()}
        for uid,row in native.items():
            uuid.UUID(uid)
            board.app.validate_dependencies(uid,row.get('depends',[]))
        with app.store.lock(),app.store.connect() as con:
            for row in con.execute('SELECT a,b FROM related'):
                if row[0] not in native or row[1] not in native:raise AssistantError('invalid_backup','A restored relation refers to missing work.')
            for row in con.execute('SELECT relative_path,sha256 FROM attachments'):
                path=(staging/row[0]).resolve()
                if not path.is_relative_to((staging/'attachments').resolve()) or not path.is_file() or digest(path.read_bytes())!=row[1]:raise AssistantError('invalid_backup','Restored image provenance is inconsistent.')
            for row in con.execute("SELECT key,plan,digest FROM operations WHERE status NOT IN ('confirmed','abandoned')"):
                plan=json.loads(row[1]);decision=con.execute('SELECT intent_digest,proposal_id FROM decisions WHERE operation_key=?',(row[0],)).fetchone()
                if not decision or decision[0]!=row[2] or decision[1]!=plan.get('proposal_id'):raise AssistantError('invalid_backup','A recoverable operation lacks matching durable authorization.')
            con.execute("UPDATE operations SET status='conflict',error='Restored incomplete operation; inspect and reconcile explicitly' WHERE status NOT IN ('confirmed','abandoned')")
            profile=app.store.state_value('profile');source_id=manifest['profile']['id'];profile['id']=str(uuid.uuid4())
            profile['restored_from']={'profile_id':source_id,'captured_at':manifest['captured_at'],'archive_sha256':digest(archive_path.read_bytes())}
            con.execute("INSERT OR REPLACE INTO state VALUES('profile',?)",(encoded(profile),))
            con.execute("INSERT OR REPLACE INTO state VALUES('context_epoch',?)",(encoded(app.store.epoch()+1),))
            invalidate_pending(con,app.now(),'This profile was restored. Review pending reasoning again.')
            policy=app.store.state_value('notification_policy',default_policy());policy['enabled']=False
            con.execute("INSERT OR REPLACE INTO state VALUES('notification_policy',?)",(encoded(policy),))
            # Current historical candidates are held, not falsely recorded as delivered.
            candidates=app._candidates(app._view(coherent=False))
            con.execute("INSERT OR REPLACE INTO state VALUES('restore_suppression',?)",(encoded({'until':stamp(app.clock()+timedelta(days=1)),'ids':[c['id'] for c in candidates]}),))
            con.execute("DELETE FROM state WHERE key IN ('backup_receipts','last_backup','backup_directory','last_tick','scheduler')")
        if target.exists():target.rmdir()  # Only the explicitly supplied empty directory.
        os.replace(staging,target)
    return {'state':str(target),'profile_id':profile['id'],'source_profile_id':source_id,'revision':app.store.revision() if staging.exists() else manifest['revision'],
            'reminders_enabled':False,'jobs_installed':False,'native_tasks':len(native),'pending_reasoning':'stale'}


def upgrade_tasks(app):
    """Let a schema-1 task store hold urgent. A verified migration backup comes first and
    every row stays byte-for-byte the same, so earlier priorities keep their level."""
    from .taskstore import SCHEMA
    if app.tasks.schema()>=SCHEMA:return None
    backup=snapshot(app,category='migration')
    expected=verify(backup['path'])
    with app.store.lock():
        if task_digest(app.tasks.rows())!=expected['task_fingerprint'] or app.store.revision()!=backup['revision']:
            raise AssistantError('stale_migration','The profile changed after its backup; retry the upgrade. No tasks were changed.')
        app.tasks.upgrade()
        receipt={'at':app.now(),'task_schema':SCHEMA,'tasks':len(app.tasks.rows()),'task_fingerprint':expected['task_fingerprint'],'backup':backup}
        with app.store.connect() as con:con.execute("INSERT OR REPLACE INTO state VALUES('priority_migration',?)",(encoded(receipt),))
    return receipt


def upgrade_profile(state):
    from .web import preflight
    from .service import Assistant
    from .connected import Connected
    app=Assistant(preflight(state))
    with exclusive(app.store.path/'server.lock',timeout=0):
        with app.store.connect() as con:version=con.execute('PRAGMA user_version').fetchone()[0]
        if version not in (3,4) or version==3 and not app.store.state_value('connected',False):raise AssistantError('unsupported_migration','Only the known connected v3 profile is upgraded automatically; legacy data is not imported.')
        backup=None
        if version==3:
            backup=snapshot(app,category='migration')
            Connected(app).initialize(_upgrade_backup=backup['path'])
        tasks=upgrade_tasks(app)
        return {'upgraded':bool(backup or tasks),'schema':4,'task_schema':app.tasks.schema(),'profile':app.store.state_value('profile'),'backup':backup,'priority_migration':tasks}


def prune(folder,profile_id):
    records=[]
    for path in Path(folder).glob('day-attendant-'+profile_id+'-*-daily.zip'):
        try:
            manifest=verify(path);at=parse(manifest['captured_at']);records.append((at,path))
        except AssistantError:continue  # Never delete a suspect archive as if it were expendable evidence.
    records.sort(reverse=True);days={};weeks={}
    for at,path in records:
        days.setdefault(at.date(),path);weeks.setdefault(at.isocalendar()[:2],path)
    keep=set(list(days.values())[:7])|set(list(weeks.values())[:4])
    for _,path in records:
        if path not in keep:path.unlink()


def list_backups(app):
    folder=directory(app);result=[]
    for path in sorted(folder.glob('day-attendant-*.zip'),reverse=True):
        try:
            m=verify(path);result.append({'path':str(path),'captured_at':m['captured_at'],'revision':m['revision'],'category':m['category'],'valid':True})
        except AssistantError:result.append({'path':str(path),'valid':False})
    return {'directory':str(folder),'backups':result,'last_backup':app.store.state_value('last_backup')}
