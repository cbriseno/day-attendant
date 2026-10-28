"""Private, versioned auxiliary state. Task records belong to Taskwarrior."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3

from .errors import AssistantError
from .platforms import exclusive

DEFAULTS = {"repeat_minutes": 30, "stale_days": 3, "due_soon_minutes": 60,
            "event_soon_minutes": 15, "chat_wait_minutes": 60}


class Store:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        checkout = Path(__file__).resolve().parent.parent
        if self.path == checkout or checkout in self.path.parents:
            raise AssistantError("unsafe_state_path", "Private state must be outside the checkout.")
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path, 0o700)
        self.database = self.path / "assistant.sqlite3"
        self.lock_path = self.path / "writer.lock"
        with exclusive(self.lock_path), self.connect() as con:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise AssistantError("newer_schema", "The state requires a newer assistant version.")
            if version == 0:
                con.executescript('''
                    CREATE TABLE operations(key TEXT PRIMARY KEY, digest TEXT NOT NULL,
                        action TEXT NOT NULL, plan TEXT NOT NULL, status TEXT NOT NULL,
                        result TEXT, error TEXT, created_at TEXT NOT NULL);
                    CREATE TABLE task_meta(uuid TEXT PRIMARY KEY, source TEXT, person TEXT,
                        reference TEXT, observed_at TEXT, captured_at TEXT NOT NULL,
                        human_progress_at TEXT NOT NULL, origin_zone TEXT, estimate_minutes INTEGER);
                    CREATE TABLE frames(id INTEGER PRIMARY KEY AUTOINCREMENT,
                        operation_key TEXT UNIQUE, target TEXT NOT NULL, previous TEXT);
                    CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    INSERT INTO state VALUES('revision','0');
                    CREATE TABLE events(uuid TEXT PRIMARY KEY, title TEXT NOT NULL,
                        start_at TEXT NOT NULL, end_at TEXT NOT NULL, origin_zone TEXT,
                        original_start TEXT, canceled INTEGER NOT NULL DEFAULT 0);
                    CREATE TABLE config(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                    CREATE TABLE notices(id TEXT PRIMARY KEY, episode TEXT NOT NULL,
                        entity TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0, accepted_at TEXT,
                        retry_at TEXT, error TEXT, claimed_at TEXT);
                    CREATE TABLE attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,
                        notice_id TEXT NOT NULL, at TEXT NOT NULL, status TEXT NOT NULL, error TEXT);
                    PRAGMA user_version=1;
                ''')
                con.executemany("INSERT INTO config VALUES(?,?)", DEFAULTS.items())
        os.chmod(self.database, 0o600)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.database, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=10000")
        try:
            with con:
                yield con
        finally:
            con.close()

    def lock(self):
        return exclusive(self.lock_path)

    def revision(self):
        with self.connect() as con:
            return int(con.execute("SELECT value FROM state WHERE key='revision'").fetchone()[0])

    def config(self):
        with self.connect() as con:
            return dict(con.execute("SELECT key,value FROM config"))

    def unresolved(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT key,action,status,error FROM operations WHERE status!='confirmed'")]

    def frames(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM frames ORDER BY id")]

    def metadata(self):
        with self.connect() as con:
            return {r["uuid"]: dict(r) for r in con.execute("SELECT * FROM task_meta")}

    def events(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM events WHERE canceled=0 ORDER BY start_at")]
