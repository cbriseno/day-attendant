# macOS handoff

The domain and UI use the same portable code. macOS adapters implement user-state
paths, kernel locks, owned subprocess cancellation, `osascript` notification delivery
and a `launchd` LaunchAgent. No MCP server is required. Actual notification permission,
company policy and resume behavior must be checked on the managed Mac. Linux results
or hosted macOS core tests do not establish those facts.

## Approved dependencies

Use Python 3.11+ with IANA timezone data, SQLite support, and the already authenticated
Codex/Claude Code installations. Taskwarrior is intentionally pinned to **3.5.0**;
other versions are rejected rather than modifying a different native schema blindly.
The Linux development binary is a conda-forge package, not a source build and not
portable to Mac. Do not copy `.runtime` as an installation strategy.

Where company approval permits source builds, use the fixed Taskwarrior source
revision `3419d5ba1e6a780fbbb0a8d68d8fd31e675c1bf8` (`v3.5.0`) and its approved
transitive Rust crates. The [release's INSTALL](https://github.com/GothenburgBitFactory/taskwarrior/blob/v3.5.0/INSTALL)
requires CMake, make, C++17 and Rust **1.88.0 or newer**; libuuid is not required on
macOS. Cargo.lock includes platform-specific entries, not a claim that all 348
packages are built on Mac. The user's agents/IT must approve the actual toolchain,
Git sources and dependency mirrors. Nothing here bypasses their controls.

```sh
git clone https://github.com/GothenburgBitFactory/taskwarrior.git taskwarrior-source
git -C taskwarrior-source checkout 3419d5ba1e6a780fbbb0a8d68d8fd31e675c1bf8
cmake -S taskwarrior-source -B taskwarrior-build \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$HOME/.local/day-attendant-taskwarrior"
cmake --build taskwarrior-build --parallel 2
cmake --install taskwarrior-build
export DAY_ATTENDANT_TASK_BIN="$HOME/.local/day-attendant-taskwarrior/bin/task"
"$DAY_ATTENDANT_TASK_BIN" --version
```

No `sudo` or package manager is assumed. If an approved native package already supplies
exactly 3.5.0, select that binary instead. Source downloads/builds are a handoff recipe,
not evidence that a build was executed on the managed Mac.

## Empty acceptance profile before personal use

```sh
python3 -B /absolute/day-attendant/day-attendant.py \
  --state "$HOME/Library/Application Support/Day Attendant/acceptance" init \
  --zone America/Edmonton --name 'Mac acceptance'
python3 -B /absolute/day-attendant/day-attendant.py \
  --state "$HOME/Library/Application Support/Day Attendant/acceptance" service open
```

Use fictional data. Verify the profile starts empty and survives restart. Capture
English/Spanish tasks and an event, link A as prerequisite of B, complete A and verify
B becomes eligible without starting. Add a nonblocking related link and verify both
sides. Select a fictional screenshot and check individual task/event results. Switch
between the two installed subscription harnesses and verify a previous human idea can
be recalled without creating it as a task. Check cancellation and visible auth/quota
errors; never activate credits or add API credentials to make a test pass.

Run `python3 -B -m unittest discover -s tests -q` with the approved binary. Linux-only
private D-Bus cases skip on Mac; this is deliberate, not notification validation.
Use the Mac's local notifier with a single fictional reminder in an allowed delivery
window, verify OS permission/visibility, then confirm the durable receipt is accepted
rather than claiming the user read it. Review quiet/pause settings.

`scheduler install` writes only the selected profile's
`~/Library/LaunchAgents/local.day-attendant.PROFILE_UUID.plist` and bootstraps it into
`gui/UID`. It uses absolute program arguments, inherited allowlisted graphical/session
variables, a private log and StartInterval=60; `tick` enforces the configured interval.
Check `doctor` for a completed actual `last_tick`, then test logout/login and a physical
sleep/resume. Do not infer wake guarantees from a plist. `scheduler uninstall` removes
only that job; repeated install/uninstall should not duplicate jobs.

Back up, restore to a fresh acceptance directory, verify UUIDs/relations/languages and
that reminders are disabled. Restore installs no LaunchAgent. Remove acceptance jobs
when done; preserve any failure evidence for the Mac agents to adapt the OS boundary.
Create the final personal profile separately, or use the default empty profile.
