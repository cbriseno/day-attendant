# Day Attendant

A local assistant for your responsibilities: capture tasks and commitments, keep
several fronts active, understand what blocks what, and review concrete risks with
your chosen LLM. It does not coordinate coding agents or replace their project boards.

Python 3.11+ and its standard-library SQLite support form the local core on both
Linux and macOS. Taskwarrior, Rust and CMake are not runtime or installation
requirements. Codex and Claude Code
adapters use your existing subscription login. The UI defaults to English and supports
Spanish, light/dark themes, responsive list/board views and collapsible chat. Card and
conversation content keep the language in which they were created.

## Start using it

Use an approved Python 3.11+ installation with SQLite and IANA timezone data, and
an existing authenticated Codex or Claude Code installation. No additional Python runtime packages, API keys, MCP server
or admin permissions are required by Day Attendant.

```sh
python3 -B /absolute/path/day-attendant/day-attendant.py init \
  --zone America/Edmonton --name 'My responsibilities'
python3 -B /absolute/path/day-attendant/day-attendant.py service open
```

A new personal profile is empty. No pilot cards or existing personal data are imported.
Default state lives under `~/.local/state/day-attendant/profiles/default` on Linux
(respecting `XDG_STATE_HOME`) or
`~/Library/Application Support/Day Attendant/profiles/default` on macOS.
`DAY_ATTENDANT_STATE` or `--state /absolute/private/path` selects another profile.

For a command available from every directory, install the checkout with an approved
Python package installer (`python3 -m pip install --user /absolute/path/day-attendant`),
or add a wrapper on your user PATH that invokes the absolute script. A virtual
environment is also supported. See [Mac handoff](docs/macos-handoff.md) for the
Python-only installation route and [daily operation](docs/daily-operation.md) for commands.

```sh
day-attendant --json board list
day-attendant --json board chat 'What should I return to?' --key human-message-001
day-attendant scheduler install
day-attendant service status
```

Before scheduling, review **Settings → Local reminders**. The initial delivery policy
is Monday–Friday 09:00–18:00 in the profile timezone, quiet 22:00–08:00, a check every
10 minutes, at most 12 attempts/day, and no unchanged repeat within 24 hours. These
are editable delivery defaults, not inferred human availability. Scheduler installation
is explicit; it does not change other jobs. Reminders and daily backups run without
an open UI or LLM session. Open the UI on demand with `service open`.

## Responsibilities, relationships and capture

- Track multiple active fronts. Card count alone never imposes a universal limit.
- Snooze from a card's clock: in 1 hour, in 3 hours, or tomorrow, in 2 days or in 1 week
  at 9:00 in the profile's time zone. Each choice shows the time it saves and says when
  that is after the deadline. `board request task_snooze` and the conversation take an exact time.
  Return directly to To do from an active or snoozed front. Snoozing preserves the deadline.
- Edit blocking prerequisites and symmetric, nonblocking related links in a row's details.
  Board cards show what blocks a responsibility and what it blocks. Selecting either
  indicator highlights the linked cards and, where the layout leaves room (not at phone
  widths), draws connecting lines; Escape clears it. The details show both directions.
  Completing a prerequisite unlocks dependents without starting them. Retiring it
  does not fulfill it. Cycles and missing references are rejected.
- Add and edit calendar commitments in the same interface. A snooze that resumes during
  a commitment is noted on its card; expired event reminders are not replayed after absence.
- Capture several unambiguous items from text or any number of selected PNG/JPEG/WebP
  images. Day Attendant sets no message or image size cap; a provider limit appears
  as that turn's visible error. Each item has its own durable proposal and outcome. Ambiguous dates or
  unsupported items remain questions. Source/person/reference, observed time and
  capture time remain distinct. The app never opens Teams or an inbox.
- Keep declared remaining effort, waiting state, where you left off and a next step.
  Silence does not prove inactivity, and zero effort does not complete a task.
- Every card shows its priority (Urgent, High, Medium or Low), and an optional group (a project, initiative or
  category) when it has one. **Order** changes only how cards are shown: by priority
  (the default), your own order, newest first, grouped, or the order the assistant
  suggested. No view changes a stored priority, group or state. Each compact row
  finishes, starts or pauses in one click; its priority chip and its clock (snooze)
  open a one-level menu. Everything else is in ⋯ (also a right click, or a long press on a
  phone). Editing stays in the row: click the title to rename it (Enter saves, Esc
  cancels); the group and deadline chips open one-level menus (⋯ sets a missing
  deadline); clicking the row's empty space expands its details below it — where you
  left off, next step, time left, waiting, prerequisites, related tasks and source —
  and each field saves by itself. If another device changed the same field while you
  typed, the row says so and keeps your text until you choose. Drag the handle, or focus it and press the arrow
  keys, or use Move up and Move down in ⋯, to set your own order. It is saved in the profile and also orders tasks of equal priority or the
  same group. Newest and Suggested accept no drops. A suggestion is computed only when
  you ask for it, and is reused until you ask again.

**Focus**, beside Today and Board, shows at most two tasks in progress and enough
available tasks to make five, plus up to two snoozed tasks returning within 72 hours.
Each says why it is here: priority, deadline, work it unblocks or a waiting response.
Blocked tasks never appear as the next action. Finish one and the next available task
moves up immediately. Focus hides filters, group colors, capture, chat and closed
items; See all returns to Today with your filters intact. Selection uses local rules
and makes no model request.

Every typed change applies at once, after the service checks it (fields, UUIDs, the
observed revision, cycles and references), and shows Undo. Nothing waits for the model.
Once you pause for a moment the assistant looks at what changed and, if it notices
something, leaves a discreet mark on that card with a suggestion you can apply or ignore;
it never blocks, applies or undoes anything by itself. Retiring is the one change that
asks for confirmation. The model interprets and suggests; the coordinator writes.
Missing or failed reasoning is visible and never blocks a change. Technical invalidity
is rejected at once.

## Harnesses and portable continuity

Choose Codex or Claude Code, a model from that harness's list and a reasoning level in
Settings, or use `profile configure` (`init` also accepts `--harness`, `--model` and
`--effort`). Codex lists its models through its app-server (no model turn); Claude Code
has no list command, so Settings offers its current exact IDs. The installed default
selects the harness default once and saves the resolved identifier.
Codex offers the levels its catalog lists for the model; Claude Code offers low,
medium, high, xhigh and max. An unset level uses medium on Claude Code; on Codex it
uses medium when the model offers it, otherwise the model's default level. A model turn
may take up to 2 minutes at minimal, low or medium, 5 at high, 10 at xhigh and 15 at max;
a later review that takes longer keeps only the known-data notes. Starting the service or
changing these settings sends no model request; tokens are spent only by conversation
messages, the later review of changes you made (one turn after each pause, none without
changes) and an order suggestion you request. Switching views or rearranging cards spends
none. There is no silent provider/model/account fallback or automatic
credit activation. The Claude adapter requires the CLI's restricted safe-mode print
and structured-output capabilities; the tested installation is 2.1.312.

Tasks, calendar, declared context and the current conversation live in the profile.
Every message starts a fresh native harness session (ephemeral for Codex) that receives
the complete current conversation, untruncated. **New conversation** in the chat, or
typing `/clear`, starts over: earlier messages stay stored locally but are no longer
shown or sent, while tasks, calendar, context and per-task notes stay. Switching or
restarting a harness keeps the current conversation without importing global
histories. A very long conversation can reach the provider's context window; use
`/clear` instead of relying on silent truncation. Under the message box, a status line
shows the context the harness reported for its latest reply and the selected model's
context window; without such a report the line stays hidden. Enter sends and
Shift+Enter adds a line; on touch keyboards Enter adds a line and a send button appears.

## Backups and recovery

The scheduler creates a coherent daily snapshot of the task store (`tasks.sqlite3`),
the auxiliary store (`assistant.sqlite3`) and selected images. It retains seven daily and four weekly snapshots,
plus manual/pre-migration backups. Snapshots exclude authentication, HTTP tokens,
process receipts and harness homes. They remain private outside the checkout.

```sh
day-attendant backup create
day-attendant backup list
day-attendant backup verify /private/backups/snapshot.zip
day-attendant restore /private/backups/snapshot.zip --to /private/new-profile
```

Restore never overwrites an occupied directory or installs jobs. It verifies hashes
and semantic identity, creates a new profile identity, invalidates pending reasoning
and starts with reminders disabled. Copy verified archives to an approved independent
location to protect against disk loss; there is no default cloud upload or device sync.

An interrupted operation either recovers its authorized effects or opens in a read-only
diagnostic view. Supervised reconciliation shows observed versus intended state, takes
a backup and rechecks preconditions. Never edit either managed SQLite store directly. See [daily operation](docs/daily-operation.md).

## Agent access and validation

Use `board request ACTION --payload JSON --key KEY` for structured actions, stable
UUIDs and the observed `--expect REVISION`. Reuse a key after a lost response; a new
human intent gets a new key. Inspect current proposal phase/generation before confirm,
retry, cancel or undo. An [agent operating contract](AGENTS.md) ships with the repo;
there is no global skill that encourages unrelated agents to capture their SDLC work.

```sh
python3 -B -m unittest discover -s tests -q
python3 -B scripts/test_system.py
```

Tests use real SQLite task and auxiliary stores with disposable state. Controlled protocol tests, live-model
semantics, real-browser flows and native scheduler execution are separate evidence.
See [daily validation](docs/daily-validation.md) and [architecture](docs/architecture.md).
Linux is tested locally. Previously hosted macOS core checks and the user's managed Mac deployment
are separate from Linux evidence; native Mac notifications and company policy require
that Mac's handoff checks. No assistant guarantees perfect risk detection or psychological
outcomes.

Existing v3 synthetic pilot state requires `profile upgrade` with the old service
stopped; a verified pre-migration backup is automatic. Legacy commands remain for
explicit separate legacy workspaces and cannot bypass connected proposals. Old default
state is never silently moved. Historical pilot evidence stays labeled as historical.

Urgent is stored in task store schema 2. The first service start of a profile from an
earlier release (or `profile upgrade` with its service stopped) takes a verified
migration backup and then marks the store; no task row changes, so existing High,
Medium and Low tasks keep their level. If that backup cannot be taken, the service
still starts, and only Urgent waits for the next start. Earlier releases refuse a
schema 2 store instead of misreading it.

## Upgrading an existing Taskwarrior profile

New profiles use the same Python/SQLite backend on every operating system. An old
profile is never silently replaced with an empty task store. Stop its service,
then explicitly convert it once on the machine that already has the old engine:

```sh
day-attendant service stop
day-attendant --json profile migrate --taskwarrior-bin /absolute/path/to/task
day-attendant service start
```

The one-time importer requires the old **Taskwarrior 3.5.0** executable only to read
its existing data. It takes and verifies a legacy backup, validates task identity
and the dependency graph, then atomically publishes `tasks.sqlite3`. UUIDs, journal,
calendar, notes, conversation and profile identity are preserved. Legacy files
remain untouched. Normal operation and all new backups thereafter need only
Python/SQLite. Starting a fresh Mac profile requires no legacy engine or conversion.
This does not enable synchronization between machines.

Format-1 backups remain verifiable. To restore and convert an old backup on the
legacy machine, use `restore ARCHIVE --to NEW_DIRECTORY --taskwarrior-bin PATH`.
Format-2 backups restore without that option or executable on Linux or macOS.
