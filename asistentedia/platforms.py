"""OS adapters: locking, capability detection and local notifications."""
from contextlib import contextmanager
import os
import shutil
import subprocess
import sys
import time

from .errors import AssistantError


@contextmanager
def exclusive(path, timeout=15):
    # Kernel locks are released when a process dies; no stale PID-file guessing.
    with path.open("a+b") as handle:
        if os.name != "nt":
            os.fchmod(handle.fileno(), 0o600)
        deadline = time.monotonic() + timeout
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            acquire = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(handle, fcntl.LOCK_UN)
        while True:
            try:
                handle.seek(0)
                acquire()
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise AssistantError("busy", "Another writer holds the state lock.") from error
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            release()


def capabilities():
    result = {name: shutil.which(name) for name in ("notify-send", "osascript", "systemctl", "crontab", "launchctl")}
    result["platform"] = sys.platform
    result["user_systemd"] = False
    if result["systemctl"] and sys.platform.startswith("linux"):
        run = subprocess.run([result["systemctl"], "--user", "is-system-running"],
                             capture_output=True, text=True, timeout=5)
        result["user_systemd"] = run.stdout.strip() in ("running", "degraded")
    return result


def notify(title, body):
    if sys.platform == "darwin" and shutil.which("osascript"):
        argv = ["osascript", "-e", "on run argv", "-e",
                "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e", "end run", title, body]
    elif sys.platform.startswith("linux") and shutil.which("notify-send"):
        argv = ["notify-send", "--app-name=asistente-dia", "--", title, body]
    else:
        return {"accepted": False, "error": "No supported local notifier is available."}
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return {"accepted": result.returncode == 0, "error": result.stderr.strip()[:500],
                "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"uncertain": True, "error": "Notifier timed out; acceptance is unknown."}
    except OSError as error:
        return {"accepted": False, "error": str(error)}
