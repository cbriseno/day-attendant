#!/usr/bin/env python3
"""Exercise the public CLI with disposable fictitious data.

No scheduler is installed. A real desktop popup is attempted only with --notify.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = {"fictitious": True, "scheduler_installed": False, "steps": []}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda minutes: (now + timedelta(minutes=minutes)).isoformat()
    with tempfile.TemporaryDirectory(prefix="asistente-daily-demo-") as state:
        def run(*argv):
            result = subprocess.run([sys.executable, "-B", str(ROOT / "day-attendant.py"),
                                     "--state", state, "--json", *argv], cwd=ROOT,
                                    capture_output=True, text=True, timeout=20)
            data = json.loads(result.stdout)
            if result.returncode or not data["ok"]:
                raise RuntimeError(data)
            report["steps"].append({"command": list(argv), "data": data["data"]})
            return data["data"]
        a = run("task", "add", "[FICTICIO] revisar PR", "--priority", "high",
                "--due", iso(30), "--source", "pr", "--key", "demo-capture-a")
        b = run("task", "add", "[FICTICIO] investigar ticket", "--priority", "medium",
                "--estimate-minutes", "60", "--key", "demo-capture-b")
        run("task", "snooze", a["uuid"], iso(15), "--key", "demo-snooze")
        revision = run("brief")["revision"]
        run("focus", "push", b["uuid"], "--expect", str(revision), "--key", "demo-focus-b")
        c = run("task", "add", "[FICTICIO] resolver incidencia", "--priority", "high",
                "--key", "demo-capture-c")
        run("focus", "push", c["uuid"], "--expect", str(c["revision"]), "--key", "demo-focus-c")
        run("task", "done", c["uuid"], "--key", "demo-complete-c")
        revision = run("brief")["revision"]
        run("focus", "resume", "--expect", str(revision), "--key", "demo-resume-b")
        run("event", "add", "[FICTICIO] junta de ejemplo", "--start", iso(5),
            "--end", iso(35), "--key", "demo-event")
        brief = run("brief")
        assert brief["next"]["uuid"] == b["uuid"]
        assert len(brief["conflicts"]) == 1
        first = run("pulse")
        second = run("pulse")
        assert first == second
        if args.notify:
            run("notify")
            assert run("notify")["attempts"] == []
        run("schedule", "--dry-run")
        run("doctor")
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"steps": len(report["steps"]), "notification_attempted": args.notify,
                      "fictitious": True, "temporary_state_removed": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
