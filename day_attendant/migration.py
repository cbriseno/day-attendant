"""Explicit, offline import of old Taskwarrior profiles; never a runtime backend."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

from .errors import AssistantError
from .platforms import exclusive
from .store import Store
from .taskstore import TaskStore, canonical
from .timeutil import stamp, utcnow


def validate_graph(rows):
    graph = {}
    for row in rows:
        value = TaskStore.validate(row)
        if value['uuid'] in graph:
            raise AssistantError('invalid_task_record', 'Duplicate task UUID in the legacy export.')
        graph[value['uuid']] = value.get('depends', [])
    done, active = set(), set()
    for root in graph:
        stack = [(root, False)]
        while stack:
            uid, leaving = stack.pop()
            if leaving:
                active.remove(uid)
                done.add(uid)
                continue
            if uid in active:
                raise AssistantError('dependency_cycle', 'The legacy dependency graph contains a cycle.')
            if uid in done:
                continue
            if uid not in graph:
                raise AssistantError('invalid_dependencies', 'A legacy dependency refers to a missing task.')
            active.add(uid)
            stack.append((uid, True))
            stack.extend((other, False) for other in graph[uid])


def legacy_app(state, binary):
    from .archives import safe_taskrc
    from .legacy_taskwarrior import Taskwarrior
    store = Store(state)
    if not (store.path / 'taskwarrior/data/taskchampion.sqlite3').is_file():
        raise AssistantError('legacy_store_missing', 'The legacy task store is missing; no empty replacement was created.')
    safe_taskrc(store.path / 'taskwarrior/taskrc')
    return SimpleNamespace(store=store, tasks=Taskwarrior(store, binary), clock=utcnow,
                           now=lambda: stamp(utcnow()))


def publish_rows(store, rows):
    """Validate and close the staged database before atomically publishing it.

    A crash leaves either the original profile, or a complete task store with the
    same participant rows. The journal still recognizes those before/after rows.
    No existing task database is overwritten, including on retries.
    """
    from .archives import task_digest
    validate_graph(rows)
    with tempfile.TemporaryDirectory(prefix='.task-migration-', dir=store.path) as temporary:
        staged = TaskStore(SimpleNamespace(path=Path(temporary), lock=nullcontext))
        for row in rows:
            staged.create(canonical(row))
        if task_digest(staged.rows()) != task_digest(rows):
            raise AssistantError('migration_mismatch', 'The converted task store differs from the source; nothing was published.')
        with staged.database.open('rb') as stream:
            os.fsync(stream.fileno())
        # Hard-link creation is atomic and fails if another store already exists.
        os.link(staged.database, store.path / 'tasks.sqlite3')


def import_legacy_rows(state, binary):
    """Only for an explicitly requested legacy archive conversion during restore."""
    app = legacy_app(state, binary)
    with app.store.lock():
        publish_rows(app.store, app.tasks.rows())


def migrate_profile(state, binary=None):
    from .archives import maintenance, snapshot, storage_errors, task_digest, verify

    @storage_errors
    def migrate():
        store = Store(state)
        with exclusive(store.path / 'server.lock', timeout=0), exclusive(store.path / 'tick.lock', timeout=0):
            if (store.path / 'tasks.sqlite3').exists():
                tasks = TaskStore(store)
                tasks.rows()  # A retry does not present a corrupt store as success.
                return {'migrated': False, 'backend': tasks.backend, 'state': str(store.path),
                        'migration': store.state_value('task_store_migration')}
            app = legacy_app(state, binary)
            backup = snapshot(app, category='migration')
            expected = verify(backup['path'])
            with maintenance(app, 'task-store-migration'), store.lock():
                rows = app.tasks.rows()
                if task_digest(rows) != expected['task_fingerprint'] or store.revision() != backup['revision']:
                    raise AssistantError('stale_migration', 'The profile changed after its backup; retry migration. No tasks were replaced.')
                publish_rows(store, rows)
                receipt = {'at': app.now(), 'backend': 'python-sqlite', 'tasks': len(rows),
                           'task_fingerprint': task_digest(rows), 'backup': backup,
                           'legacy_files_preserved': True}
                with store.connect() as con:
                    con.execute("INSERT OR REPLACE INTO state VALUES('task_store_migration',?)",
                                (json.dumps(receipt),))
                return dict(receipt, migrated=True, state=str(store.path))
    return migrate()
