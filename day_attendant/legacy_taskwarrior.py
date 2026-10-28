"""Legacy import/parity helper only. Day Attendant never uses it as its task store."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from .errors import AssistantError


from .taskstore import canonical

class Taskwarrior:
    backend = "taskwarrior"
    storage_format = 1
    storage_files = ("assistant.sqlite3", "taskwarrior/taskrc", "taskwarrior/data/taskchampion.sqlite3")
    def __init__(self, store, binary=None):
        development = Path(__file__).resolve().parent.parent / ".runtime/taskwarrior/bin/task"
        chosen = binary or os.environ.get("DAY_ATTENDANT_TASK_BIN") or shutil.which("task") or (development if development.is_file() else None)
        if not chosen:
            raise AssistantError("task_missing", "Taskwarrior 3.5.0 is required; set DAY_ATTENDANT_TASK_BIN.")
        self.binary = str(Path(chosen).expanduser().resolve())
        self.home = store.path / "taskwarrior"
        self.home.mkdir(mode=0o700, exist_ok=True)
        self.rc = self.home / "taskrc"
        if not self.rc.exists():
            self.rc.write_text("confirmation=off\nnews.version=3.5.0\n", encoding="utf8")
            os.chmod(self.rc, 0o600)
        self.detected_version = self.version()
        if self.detected_version != "3.5.0":
            raise AssistantError("unsupported_task_version", "This release requires the tested Taskwarrior version 3.5.0.", {"detected": self.detected_version})

    def command(self, *args, input_text=None):
        env = os.environ.copy()
        env.update(TASKRC=str(self.rc), TASKDATA=str(self.home / "data"))
        try:
            result = subprocess.run([self.binary, "rc.confirmation=off", "rc.verbose=nothing", "rc.hooks=off", *args],
                                    input=input_text, capture_output=True, text=True, env=env, timeout=15,
                                    **({"umask": 0o077} if os.name != "nt" else {}))
        except subprocess.TimeoutExpired as error:
            raise AssistantError("task_uncertain", "Taskwarrior timed out; recover before retrying.") from error
        except OSError as error:
            raise AssistantError("task_unavailable", str(error)) from error
        if result.returncode:
            raise AssistantError("task_failed", result.stderr.strip()[:500] or result.stdout.strip()[:500])
        return result.stdout

    def version(self):
        env = os.environ.copy()
        env.update(TASKRC=str(self.rc), TASKDATA=str(self.home / "data"))
        try:
            return subprocess.check_output([self.binary, "--version"], env=env, text=True, timeout=5,
                                           **({"umask": 0o077} if os.name != "nt" else {})).strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise AssistantError("task_unavailable", "Cannot read the Taskwarrior version.") from error

    def rows(self):
        try:
            return json.loads(self.command("export"))
        except json.JSONDecodeError as error:
            raise AssistantError("invalid_task_json", "Taskwarrior returned invalid JSON.") from error

    def get(self, uid):
        return next((r for r in self.rows() if r["uuid"] == uid), None)

    def create(self, row):
        self.command("import", "-", input_text=json.dumps([row]))

    def patch(self, uid, changes):
        args=[]
        for key,value in changes.items():
            if key=='depends':
                current=set((self.get(uid) or {}).get('depends',[]))
                wanted=set(value.split(',') if isinstance(value,str) and value else value or [])
                entries=['-'+u for u in sorted(current-wanted)]+sorted(wanted-current)
                if entries:args.append('depends:'+','.join(entries))
            else:args.append(f"{key}:{value if value is not None else ''}")
        if args:self.command(uid,'modify',*args)
