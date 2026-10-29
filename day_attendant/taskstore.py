"""The same standard-library SQLite task store on Linux and macOS.

The coordinator owns policy and its journal. This store atomically persists one
participant at a time so an interrupted operation can compare before/after rows.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .errors import AssistantError
from .timeutil import parse, stamp, wire, utcnow


def canonical(row):
    if row is None:
        return None
    result = {k: v for k, v in row.items() if k not in ('id', 'urgency', 'modified')}
    if result.get('depends'):
        result['depends'] = sorted(result['depends'])
    else:
        result.pop('depends', None)
    return result


class TaskStore:
    backend = 'python-sqlite'
    storage_format = 2
    storage_files = ('assistant.sqlite3', 'tasks.sqlite3')

    def __init__(self, store):
        self.database = store.path / 'tasks.sqlite3'
        with store.lock():
            if not self.database.exists() and (store.path / 'taskwarrior').exists():
                raise AssistantError('task_migration_required',
                    'This profile still uses Taskwarrior. Stop its service and run profile migrate; no tasks were replaced.')
            existed = self.database.exists()
            fd = os.open(self.database, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            with self.connect(write=True) as con:
                version = con.execute('PRAGMA user_version').fetchone()[0]
                if not existed:
                    con.execute('CREATE TABLE tasks(uuid TEXT PRIMARY KEY NOT NULL, data TEXT NOT NULL)')
                    con.execute('PRAGMA user_version=1')
                elif version != 1:
                    raise AssistantError('unsupported_task_schema', 'The task store has an unsupported schema; no tasks were replaced.')
            self.database.chmod(0o600)

    @contextmanager
    def connect(self, write=False):
        try:
            con = sqlite3.connect(self.database, timeout=10)
            try:
                con.execute('PRAGMA busy_timeout=10000')
                if write:
                    con.execute('BEGIN IMMEDIATE')
                with con:
                    yield con
            finally:
                con.close()
        except sqlite3.Error as error:
            raise AssistantError('task_storage_error', 'The SQLite task operation failed; inspect recovery before retrying.') from error

    @staticmethod
    def validate(row):
        if not isinstance(row, dict):
            raise AssistantError('invalid_task_record', 'A task record must be an object.')
        try:
            if str(uuid.UUID(row['uuid'])) != row['uuid']:
                raise ValueError('uuid')
            if not isinstance(row['description'], str) or not row['description'].strip():
                raise ValueError('description')
            if row.get('status') not in ('pending', 'completed', 'deleted', 'recurring'):
                raise ValueError('status')
            for field in ('entry', 'due', 'wait', 'start', 'end'):
                if field in row:
                    parse(row[field])
            if row.get('priority') not in (None, 'H', 'M', 'L'):
                raise ValueError('priority')
            if not isinstance(row.get('project', ''), str):
                raise ValueError('project')
            refs = row.get('depends', [])
            if not isinstance(refs, list) or len(refs) != len(set(refs)):
                raise ValueError('depends')
            for ref in refs:
                if str(uuid.UUID(ref)) != ref:
                    raise ValueError('dependency uuid')
            json.dumps(row, allow_nan=False)
        except (KeyError, TypeError, ValueError, AttributeError, AssistantError) as error:
            raise AssistantError('invalid_task_record', 'A stored task has invalid identity, fields, or dates.') from error
        return canonical(row)

    def version(self):
        return '1'

    def rows(self):
        with self.connect() as con:
            records = con.execute('SELECT uuid,data FROM tasks ORDER BY rowid').fetchall()
        return [self._decode(uid, data) for uid, data in records]

    def _decode(self, uid, data):
        try:
            row = self.validate(json.loads(data))
            if row['uuid'] != uid:
                raise ValueError('identity mismatch')
            return row
        except (ValueError, AssistantError) as error:
            raise AssistantError('invalid_task_record', 'The SQLite task record failed validation.') from error

    def get(self, uid):
        with self.connect() as con:
            row = con.execute('SELECT data FROM tasks WHERE uuid=?', (uid,)).fetchone()
        return self._decode(uid, row[0]) if row else None

    def create(self, row):
        value = self.validate(dict({'status': 'pending', 'entry': wire(stamp(utcnow()))}, **row))
        with self.connect(write=True) as con:
            old = con.execute('SELECT data FROM tasks WHERE uuid=?', (value['uuid'],)).fetchone()
            if old:
                if self._decode(value['uuid'], old[0]) == value:
                    return
                raise AssistantError('external_conflict', 'A different task already has this UUID; no task was overwritten.')
            con.execute('INSERT INTO tasks VALUES(?,?)', (value['uuid'], json.dumps(value, ensure_ascii=False)))

    def patch(self, uid, changes):
        if 'uuid' in changes or any(k in changes for k in ('id', 'urgency', 'modified')):
            raise AssistantError('invalid_task_record', 'Task identity and derived fields cannot be patched.')
        with self.connect(write=True) as con:
            old = con.execute('SELECT data FROM tasks WHERE uuid=?', (uid,)).fetchone()
            if not old:
                raise AssistantError('task_not_found', 'The task does not exist.')
            value = self._decode(uid, old[0])
            for key, item in changes.items():
                if key == 'depends' and isinstance(item, str):
                    item = item.split(',') if item else None
                if item is None or item == '':
                    value.pop(key, None)
                else:
                    value[key] = item
            value = self.validate(value)
            con.execute('UPDATE tasks SET data=? WHERE uuid=?', (json.dumps(value, ensure_ascii=False), uid))
