# macOS handoff

Day Attendant uses the **same Python/SQLite task backend on Linux and macOS**.
Taskwarrior, TaskChampion, Rust, CMake and a C++ toolchain are not needed to install
or run it. There is no separate Mac domain implementation.

Only the OS boundary differs: user-state paths, kernel locks, owned subprocess
cancellation, `osascript` notification delivery and the `launchd` LaunchAgent.
Actual notification permission, company policy and sleep/resume behavior must be
checked on the managed Mac. Linux tests do not establish those facts.

## Approved dependencies and installation

Use an approved Python 3.11+ installation with its standard-library `sqlite3` and
IANA timezone data, plus an existing authenticated, company-approved Codex or
Claude Code installation. Use the harness allowed on that machine; the app does
not install either harness, copy credentials or activate API credits. No additional
Python runtime packages, MCP server, native task binary or administrator access is
required by Day Attendant.

Copy or clone the source to an approved directory and run its absolute script:

```sh
python3 -c 'import sqlite3, zoneinfo; print(sqlite3.sqlite_version)'
python3 -B /absolute/day-attendant/day-attendant.py init \
  --zone America/Edmonton --name 'My responsibilities'
python3 -B /absolute/day-attendant/day-attendant.py service open
```

Select the approved installed harness with `init --harness claude` or
`init --harness codex`, or change it in Settings. Startup and settings changes do
not send inference requests. An optional package installation or shell wrapper can
make `day-attendant` available from any directory. Do not copy the Linux `.runtime`
directory; no part of it is required for a fresh Mac profile.

A new profile is empty. Default state is under
`~/Library/Application Support/Day Attendant/profiles/default`; `--state` selects
another private directory. Both task records and auxiliary state use portable
SQLite databases managed by the coordinator. Do not edit them directly.

## Empty acceptance profile before personal use

```sh
python3 -B /absolute/day-attendant/day-attendant.py \
  --state "$HOME/Library/Application Support/Day Attendant/acceptance" init \
  --zone America/Edmonton --name 'Mac acceptance'
python3 -B /absolute/day-attendant/day-attendant.py \
  --state "$HOME/Library/Application Support/Day Attendant/acceptance" service open
```

Use fictional data. Verify the profile starts empty and survives restart. Capture
English/Spanish tasks and an event, link A as prerequisite of B, complete A and
verify B becomes eligible without starting it. Check both directions of related
links, snooze/return, literal card language, selected-image receipts and `/clear`.
Choose a model and reasoning level in Settings; confirm that a new conversation
preserves tasks, calendar and declared context.

Run the portable core checks without any task executable:

```sh
python3 -B -m unittest discover -s tests -q
```

Linux-only D-Bus scenarios intentionally skip on Mac. Optional legacy-engine
comparison/import tests skip unless `DAY_ATTENDANT_LEGACY_TASK_BIN` is explicitly
set; they are not required to run the app or its SQLite integration tests.

Use a single fictional reminder to check the Mac's local notifier, allowed delivery
window and OS permission. An accepted receipt does not prove that a person read it.
Review quiet/pause settings before explicitly installing the scheduler.

`scheduler install` writes only the selected profile's
`~/Library/LaunchAgents/local.day-attendant.PROFILE_UUID.plist` and bootstraps it into
`gui/UID`. The job runs the same known-data `tick` as Linux: reminders and backups,
never model turns. Check `doctor` for a completed actual `last_tick`, then test
logout/login and physical sleep/resume. Do not infer wake guarantees from a plist.
Repeated install/uninstall must not duplicate jobs; uninstall removes only that job.

Back up and restore to a fresh acceptance directory. Verify UUIDs, relationships,
languages and conversation, and confirm reminders are disabled after restore. New
format-2 backups require no Taskwarrior executable on either OS and install no jobs.
Remove acceptance jobs when done and preserve failure evidence for the Mac agents
to adapt the OS boundary. Create the final personal profile separately.

## Existing data from the old engine

Fresh profiles need no migration. An existing Taskwarrior-era profile requires one
explicit offline conversion on a machine that already has Taskwarrior 3.5.0:

```sh
day-attendant service stop
day-attendant --json profile migrate --taskwarrior-bin /absolute/path/to/task
day-attendant service start
```

The converter takes a verified legacy backup, preserves UUIDs and auxiliary state,
and leaves the old files untouched. It is an import tool, not a selectable runtime
backend. New backups after conversion are portable SQLite backups. A format-1
archive can be explicitly restored/converted on the legacy machine using
`restore ARCHIVE --to NEW_DIRECTORY --taskwarrior-bin PATH`, then backed up again
in format 2 before moving it to a Mac without the old engine.

This does not add device synchronization. Physical installation, permissions and
company approval on the managed Mac remain separate from Linux evidence.
