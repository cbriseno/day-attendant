"""Test-only entry point: public CLI with a shared logical clock/fault barrier."""
from datetime import datetime
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from asistentedia import cli
from asistentedia.service import Assistant


class HarnessAssistant(Assistant):
    def __init__(self, *args, **kwargs):
        def clock():
            return datetime.fromisoformat(Path(os.environ['E2E_CLOCK']).read_text().strip())

        def fault(point):
            if point == os.environ.get('E2E_CRASH'):
                os._exit(75)
            if point == os.environ.get('E2E_PAUSE'):
                marker = Path(os.environ['E2E_BARRIER'])
                marker.write_text(point)
                deadline = time.monotonic() + 20
                while not marker.with_suffix('.release').exists():
                    if time.monotonic() > deadline:
                        raise RuntimeError('Harness barrier expired')
                    time.sleep(.02)
        super().__init__(*args, **kwargs, clock=clock, fault=fault)


cli.Assistant = HarnessAssistant
raise SystemExit(cli.main())
