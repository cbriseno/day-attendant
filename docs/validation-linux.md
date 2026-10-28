# Validation commands

Run from the checkout with Python 3.11+ and Taskwarrior 3.5.0:

```sh
DAY_ATTENDANT_TASK_BIN="$PWD/.runtime/taskwarrior/bin/task" \
  python3 -B -m unittest discover -s tests -q
python3 -B scripts/test_system.py
```

System checks require `dbus-run-session`, `notify-send` and Python GI (Gio/GLib).
They use temporary state and a private notification bus. Real-model checks require
the existing authenticated Codex CLI; controlled tests require no model access.

The connected browser driver uses an explicitly owned isolated Chrome CDP session.
It must receive a synthetic state directory and an output path. Do not aim it at
personal data. See `scripts/test_connected_browser.mjs` for its declared fixtures.

The development Taskwarrior binary is excluded from publication. Its feasibility
records under `experiments/taskwarrior-feasibility` describe Linux observations,
not a corporate dependency approval or a validated macOS build.
