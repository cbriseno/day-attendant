#!/usr/bin/env python3
"""Start a bus with no activation services, then run system tests sequentially."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
env = dict(os.environ, ASISTENTE_ISOLATED_BUS='1')
raise SystemExit(subprocess.call(['dbus-run-session', '--config-file=' + str(root / 'tests/fixtures/session.conf'), '--', sys.executable, '-B', str(root / 'tests/test_system.py'), *sys.argv[1:]], env=env, cwd=root))
