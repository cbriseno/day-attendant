"""Supervised recovery: observe, back up, compare again, then reconcile."""
import json
from .errors import AssistantError
from .service import fingerprint,encoded
from .taskwarrior import canonical
from .profiles import invalidate_pending


def inspect(app,key):
    with app.store.lock():return _inspect(app,key)


def _inspect(app,key):
    with app.store.connect() as con:row=con.execute('SELECT * FROM operations WHERE key=?',(key,)).fetchone()
    if not row:raise AssistantError('operation_not_found','The operation does not exist.')
    plan=json.loads(row['plan']);participants=[{'uuid':s['uuid'],'before':s['before'],'intended':s['after'],'observed':canonical(app.tasks.get(s['uuid']))} for s in plan['steps']]
    guards=app.read_observations(plan)
    matching=guards['matching'] and all(p['observed'] in (p['before'],p['intended']) for p in participants)
    basis={'key':key,'status':row['status'],'revision':app.store.revision(),'participants':participants,'read_guards':guards}
    return dict(basis,precondition=fingerprint(basis),can_complete=matching and app.store.revision()==plan['revision'],
                closed=row['status'] in ('confirmed','abandoned'),action=plan['action'])


def resolve(app,key,resolution,precondition,backup_directory=None):
    if resolution not in ('complete','keep_observed'):raise AssistantError('invalid_resolution','Choose complete or keep_observed.')
    with app.store.lock():
        app.store.assert_writable();before=_checked_observation(app,key,resolution,precondition)
        if before['closed']:return before
        if resolution=='complete' and not before['can_complete']:raise AssistantError('recovery_conflict','The operation cannot complete without overwriting an external change. Review keep_observed instead.')
    from .archives import snapshot
    backup=snapshot(app,destination=backup_directory,category='reconciliation')
    with app.store.lock():
        app.store.assert_writable();current=_checked_observation(app,key,resolution,precondition)
        if current['closed']:return current
        with app.store.connect() as con:
            if resolution=='complete':con.execute("UPDATE operations SET status='prepared',error=NULL WHERE key=?",(key,))
            else:
                con.execute("UPDATE operations SET status='abandoned',error='Human kept observed data after a verified backup' WHERE key=?",(key,))
                con.execute("UPDATE proposals SET phase='canceled',generation=generation+1,error='Reconciled by keeping observed data; no completion claimed' WHERE operation_key=?",(key,))
                con.execute("UPDATE state SET value=? WHERE key='revision'",(str(app.store.revision()+1),))
            con.execute('INSERT INTO reconciliations(operation_key,resolution,observed,backup,at) VALUES(?,?,?,?,?)',(key,resolution,encoded(current),encoded(backup),app.now()))
            con.execute("INSERT OR REPLACE INTO state VALUES('context_epoch',?)",(encoded(app.store.epoch()+1),))
            invalidate_pending(con,app.now(),'Recovery changed the context. Review pending decisions again.')
        if resolution=='complete':
            remaining=app._recover()
            if remaining:raise AssistantError('recovery_conflict','Reconciliation did not finish; the backup and audit were preserved.',{'operations':remaining})
        return dict(_inspect(app,key),backup=backup,resolution=resolution)


def _checked_observation(app,key,resolution,precondition):
    current=_inspect(app,key)
    if current['closed']:
        with app.store.connect() as con:records=con.execute('SELECT observed,backup FROM reconciliations WHERE operation_key=? AND resolution=? ORDER BY id DESC',(key,resolution)).fetchall()
        for record in records:
            if json.loads(record['observed'])['precondition']==precondition:
                return dict(current,backup=json.loads(record['backup']),resolution=resolution)
        raise AssistantError('stale_recovery','The operation closed without this reconciliation. Inspect it again.')
    if current['precondition']!=precondition:raise AssistantError('stale_recovery','Observed data changed. Inspect the operation again.')
    return current
