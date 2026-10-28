# Day Attendant

A local personal assistant for human responsibilities: keep several fronts active,
return to unfinished work, and discuss concrete consequences before changing a
commitment. It does not coordinate coding agents or replace their project boards.

The approved interface now connects to Codex app-server, Taskwarrior 3.5.0 and
SQLite. This release is a **synthetic Linux pilot**, isolated from personal task
data. macOS deployment and unattended scheduling remain unvalidated.

## Run

Requires Python 3.11+, the existing authenticated Codex CLI, and Taskwarrior 3.5.0.
No additional Python runtime packages are needed. On this development machine,
Taskwarrior is available under `.runtime/taskwarrior/bin/task`, excluded from Git.
Elsewhere, set `DAY_ATTENDANT_TASK_BIN` to the approved binary.

```sh
python3 -B day-attendant.py web --synthetic --state /absolute/private/pilot-state \
  --zone America/Edmonton --port 34913
```

Use a new empty directory. The program seeds clearly labeled synthetic cards and
prints the local session link. Existing unmarked state is rejected before migration.
The server is loopback-only and explicitly started; no scheduler is installed.
If Codex cannot start, the interface and stored state remain available with the
assistant marked unavailable. Reasoning-dependent changes remain incomplete until
retried or explicitly continued. The selected model persists for the workspace.

The interface defaults to English and offers Spanish. Interface language, timezone,
conversation language and card language are independent. Changing locale never
translates cards, notes or conversation, and never calls the model.

## Agent access

Use the same running service from any directory via the installed `day-attendant`
command, or use the absolute path to `day-attendant.py`.

```sh
day-attendant --json --state /absolute/private/pilot-state board list
day-attendant --json --state /absolute/private/pilot-state board request front_activate \
  --payload '{"uuid":"TASK_UUID"}' --key stable-human-request-1
day-attendant --json --state /absolute/private/pilot-state board chat \
  'What is the smallest useful next step?' --key stable-message-1
```

A request may be applied, under evaluation, awaiting a decision, stale, or incomplete.
Inspect the current proposal before `board confirm ID --generation N`. An explicit
`--without-review` continuation records that contextual review was incomplete.
Use `board cancel`, `board retry` and `board undo FACT_ID --key KEY` as appropriate.
Keep the same key on a transport retry; a genuinely new intent gets a new key.
Never edit SQLite or invoke Taskwarrior directly on managed state.

## Behavior and boundaries

- Each active front uses its own native Taskwarrior `start`; no universal card limit.
- Snoozing clears only that front's activation and preserves its deadline.
- Waiting for a reply is distinct from snoozing. Remaining effort is declared, not
  inferred from silence or the number of cards. Zero effort does not complete work.
- Typed declarations are saved without asking a model to approve their truth.
  Chat interpretations receive a separate scope check. A new capacity consequence
  after a correction produces deduplicated informational guidance, without task edits.
- Consequences are acknowledged by their specific evidence. Sharing a category is
  not permission to bypass a new concern. Relevant changes and temporal expiry
  invalidate reviews; delayed results cannot revive canceled or superseded requests.
- Admission and durable authorization share a SQLite transaction. Recovery verifies
  native effects before replay. Undo restores only the original changed fields and
  rejects conflicting state or newly blocking dependencies.
- The browser retains uncertain request identities and sends form revisions. A stale
  form preserves its draft and reports conflict instead of overwriting a correction.

The restricted Codex process uses existing ChatGPT authentication and remote inference.
Shell, MCP, apps, plugins, hooks, browser/computer tools and memory are disabled and
checked against effective configuration before a conversation starts. No API fallback
or provider/model substitution is performed silently.

Native dependencies are recognized when checking activation. The UI does **not** yet
create relationships, edit dependencies, or render a dependency graph.

## Legacy commands and notifications

The existing `task`, `focus`, `event`, `brief`, `pulse`, `notify`, `doctor` and `recover`
commands remain available for separate legacy workspaces. They cannot bypass the
connected proposal boundary. All connected use requires explicit `--state`.
Legacy state is never moved automatically; if an old default or `ASISTENTE_STATE`
exists, select the intended path explicitly. New configuration uses
`DAY_ATTENDANT_STATE` and `DAY_ATTENDANT_TASK_BIN`.

`pulse` reads known reminder facts; `notify` attempts local delivery. Acceptance by
Linux/macOS notification software does not prove human reading. The durable reminder
queue handles suppression, budgets, uncertain outcomes and process recovery. There
are no installed background jobs. Agree working and quiet hours before scheduling.

## Validation

```sh
python3 -B -m unittest discover -s tests -q
python3 -B scripts/test_system.py
python3 -B scripts/probe_codex.py --out /tmp/codex-probe.json
python3 -B scripts/evaluate_connected.py --live --out /tmp/model-evaluation.json
```

Integration tests use real Taskwarrior and temporary state. Controlled reviewer tests
verify the coordinator, not LLM reasoning. Live evaluations and browser traces are
reported separately; historical failed attempts are retained, not rewritten.
System tests use a private D-Bus notification receiver and accelerated logical time,
not the user's desktop or days of manual beta testing.

See [architecture](docs/architecture.md), [connected pilot](docs/connected-pilot.md)
and [validation scope](docs/e2e-linux.md). This pilot does not establish production
backup/restore, macOS deployment, synchronization, psychological outcomes or perfect
risk detection.
