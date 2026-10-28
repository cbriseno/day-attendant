# Linux validation commands

Run from the checkout with Python 3.11+ and Taskwarrior 3.5.0:

```sh
export DAY_ATTENDANT_TASK_BIN=/absolute/path/to/approved/task
python3 -B -m unittest discover -s tests -q
/usr/bin/python3 -B scripts/test_system.py
```

System checks require `dbus-run-session`, `notify-send` and GI (Gio/GLib) in the
selected system Python. They use temporary state and a private notification bus.
Controlled tests require no model access. For the separate subscription-model and
browser evidence, see [daily validation](daily-validation.md).

The connected browser driver uses an explicitly owned isolated Chrome CDP session.
It must receive a synthetic state directory and an output path. Do not aim it at
personal data. See `scripts/test_connected_browser.mjs` for its declared fixtures.

The development Taskwarrior binary is excluded from publication. Its feasibility
records under `experiments/taskwarrior-feasibility` describe Linux observations,
not a corporate dependency approval or a validated macOS build.
