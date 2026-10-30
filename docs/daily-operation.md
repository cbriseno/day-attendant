# Daily operation

## First run and lifecycle

Initialize once with `day-attendant init --zone IANA_ZONE --name NAME`. Restarting
never reseeds a personal profile or changes its timezone. Select another state with
`--state`; the CLI, API and UI share the same coordinator. Do not edit either managed SQLite store directly. Linux and macOS use the same
Python/SQLite task backend.

`service start`, `stop`, `restart`, `status` and `open` operate only the selected
profile. Start is idempotent and the kernel lifetime lock permits one server/profile.
Open starts if needed and obtains a private authenticated loopback URL. Session
credentials rotate on restart; use `service open` again. Supervised logs contain the
origin, never the authentication fragment. A missing harness leaves every typed change
working; only the conversation, the model's follow-up suggestions and order suggestions
wait for it. Known-data notes still appear on the affected cards.

Run `scheduler install` after reviewing Settings. Linux chooses a usable systemd user
manager, otherwise cron. macOS installs a user LaunchAgent. Jobs execute `tick`, which
never calls a model, reevaluates current facts, attempts permitted reminders and makes
a daily backup. Opening the UI is independent and on demand. `scheduler status` shows
the app's installation receipt; `doctor` shows the last actual tick and transport
failures. A receipt does not prove execution: confirm `last_tick` after installation.
`status` does not claim to validate physical suspend or OS notification permissions.

`service stop` stops the web assistant; reminders continue until disabled in Settings
or removed with `scheduler uninstall`. Uninstall is idempotent and removes only this
profile's identified native job. It preserves tasks, backups and unrelated jobs.
No scheduling adapter promises a popup while the machine is physically suspended.
The next runnable tick reevaluates current conditions instead of replaying missed ticks.

## Editing and local dates

Task fields are edited in their row and save against the latest state read. If the
same field changed elsewhere since you started typing, nothing is sent: the row shows
the current value and keeps your draft, with Save mine and Use the current one. Dialog
editors (capture, calendar, settings, context) use their opening revision; a stale
submission is rejected and the dialog stays open.

Unchanged task deadlines, calendar bounds, reminder pauses and availability times
retain their exact stored instants, including seconds and the resolved occurrence
of a repeated hour. Editing other fields or the profile timezone does not
reinterpret them. Changed local dates use the profile timezone; the browser rejects
missing or repeated local times and retains the draft for correction. Date fields also
show a readable preview in the interface language beside the browser's native picker.

## Agent contract

```sh
day-attendant --json board list
day-attendant --json board request dependency_update \
  --payload '{"uuid":"DEPENDENT_UUID","dependsOn":["PREREQUISITE_UUID"]}' \
  --key human-link-001 --expect 12
day-attendant --json board request related_update \
  --payload '{"uuid":"FIRST_UUID","relatedTo":["SECOND_UUID"]}' --key human-related-001
day-attendant --json board chat 'Capture these selected responsibilities' \
  --image /absolute/selected-notes.png --capture-only --key capture-001
day-attendant --json board request task_update \
  --payload '{"uuid":"TASK_UUID","group":"Office move"}' --key human-group-001 --expect 12
day-attendant --json board request order_update \
  --payload '{"order":["FIRST_UUID","SECOND_UUID"]}' --key human-order-001 --expect 13
day-attendant --json board request group_color_update \
  --payload '{"group":"Office move","color":"teal"}' --key human-color-001 --expect 14
day-attendant --json board suggest --key human-suggest-001
```

Dependency and related updates replace that entity's corresponding list. Preserve
links the human did not ask to remove. Related links are symmetric and nonblocking.
Use permanent UUIDs, never positional or numeric task IDs. New items in a capture receive UUIDs
independently; requested links between new items need a follow-up using assigned IDs.

`group` is an optional short label (project, initiative or category) on `task_add` and
`task_update`; an empty string removes it. `order_update` replaces the human's display
order (`taskOrder` in `board list`) and is saved without a model review. Use it only when
the human arranges their list; it never changes priority, group or state, and tasks
missing from it follow by priority. `board suggest` asks the selected model once for a
suggested order (`orderSuggestion`); `stale` means tasks or context changed since.
`group_color_update` sets the color a group shows with (`groupColors` in `board list`, keyed
by the lowercase label): `purple`, `teal`, `brown`, `sky`, `pink`, `red`, `orange`, `indigo`,
`green` or `gray`, or `null` for the automatic color its label gives. Like the order it is
the human's presentation, saved without review and with Undo; it changes no task.
Requesting it again with the same key reuses the stored result.

In the UI, group chips above the tasks filter in one click and count open tasks.
Today with Order → Group shows foldable sections with completed/total progress
(retired tasks excluded) and the next open deadline. Dragging into a section,
including a folded one, changes that task's single group and offers Undo. The
group menu on a task changes its color for every task in that group.

Closed responsibilities show the latest ten, grouped by the closing day in the profile's
timezone. Show 10 more reveals the next page; nothing is removed. Reopen and Undo preserve
the recorded closing instant. Older records without a closing date say so instead of
using their creation date. The conversation formats emphasis, lists, code and web/mail
links; supplied HTML remains text.

`board request` applies a valid change in the same call (phase `applied`) or rejects it
with a stable code (`dependency_cycle`, `invalid_uuid`, `task_not_found`,
`stale_revision`…); no model reviews it first. `board list` returns current proposal
phases and individual outcomes. A batch is not one all-or-nothing business transaction:
its interpretations are validated/persisted together, then each item applies in order
against the state the previous one left. Some may save while others no longer apply
(phase `invalid` with its code). Retrying transport keeps the same request identity and
does not create a new batch. An incomplete message with durable proposals reuses them.

Only a retire interpreted from the conversation waits (phase `review`). Use
`board confirm ID --generation N` only after the human confirms that retire;
`--without-review` is accepted for compatibility and changes nothing. Use
`board retry ID` to apply a change an earlier version left waiting, or to ask again
about a stale retire, and `board cancel ID` to discard it.

`board undo FACT_ID --key KEY` restores exactly what a fact replaced and refuses with
`undo_conflict` when a later change touched the same data. It covers task changes,
captures (the new task is retired, never erased), order, available time and rest,
effort and continuity, related links, and calendar additions, edits and cancellations
(the same commitment returns with its UUID); it does not cover provenance or recorded
progress. `board undo-message
MESSAGE_KEY --key KEY` undoes every change one conversation message applied, newest
first.

After a pause of a few seconds the service looks at new facts and records suggestions,
listed under `suggestions` in `board list` (`current` while their facts still hold) with
the review state under `review`. `board accept ID` applies a suggested change as a typed
request, with Undo; `board dismiss ID` hides it for good. Neither the review nor a
suggestion ever changes a task by itself.

## Settings and notification policy

```sh
day-attendant --json profile show
day-attendant --json profile configure --key select-claude-001 \
  --values '{"harness":"claude","model":null,"effort":"high"}'
day-attendant --json board clear --key new-conversation-001
day-attendant --json profile configure --key backup-location-001 \
  --values '{"backup_directory":"/absolute/approved/backup-directory"}'
```

`effort` selects the reasoning level the harness offers for the model; an unavailable
level is rejected instead of replaced. Changing `harness` without `effort` resets the level
to unset, as it resets `model`. `board clear` (or a chat message that is exactly
`/clear`) starts a new conversation and is idempotent per key. It is refused while a
reply or contextual guidance is pending. Earlier messages remain in the profile and its backups.

Exact model IDs are pinned after resolution. A model alias that resolves to another
identifier is rejected; use the resolved exact ID. No automatic change of harness,
provider or billing method occurs after authentication, quota or model errors.
Use the UI Settings for working days, delivery/quiet times, pause, repeat period and
budgets. `notification_policy` is also a typed JSON object in `profile configure`.
The initial Mon–Fri 09:00–18:00 default is not an assertion about your working hours.
Equal quiet start/end disables that quiet window; delivery start/end must differ.

Delivery failures consume the configured attempt budgets; silence consumes none.
Uncertain outcomes require inspection rather than automatic resend. A notifier
accepting an alert never proves that a person read it. Unchanged repeats are slow,
and never-notified work is favored before repeats to avoid starvation. A rolling
three-attempt/five-minute ceiling also covers concurrent callers and clock changes.

## Backups, maintenance and recovery

Use `backup create [--destination DIR] [--key KEY]`, `backup list`, and `backup verify
ARCHIVE`. The default private backup directory is outside the profile and checkout.
The scheduler retains seven daily and four weekly snapshots; manual and migration
copies are not pruned by that policy. Keep a verified independent copy for disk loss.
No cloud transfer or cross-device synchronization is enabled.

Snapshot maintenance prevents new admissions, image writes and delivery claims,
invalidates in-flight reasoning, waits boundedly for transport and captures both
SQLite stores with their backup APIs. Already observed partial operations are
preserved for diagnosis; a backup does not execute an operation on your behalf.
Compression happens from immutable staged files and publication is atomic. A failed
backup preserves existing archives/profile and surfaces a diagnostic error.

`restore ARCHIVE --to NEW_DIRECTORY` is offline and never overwrites an occupied
profile. It verifies allowed members, hashes, database integrity, native UUIDs,
dependency graphs, relation references and authorization for incomplete operations.
It creates a distinct profile identity, discards runtime credentials/jobs, invalidates
reasoning and disables reminders. Historical current candidates also have a 24-hour
hold to reduce replay. Review Settings before reenabling. This is not exactly-once
external notification delivery or live multi-machine synchronization.

Conflicts open a read-only UI with **Diagnostics → Review recovery**. Compare expected,
intended and observed native state. Completing is permitted only if every participant
matches its recorded before/after state and the auxiliary revision matches. Keeping
observed data abandons the interrupted operation without undoing partial/external
changes or claiming completion. Both routes take a backup, compare preconditions
again and record the decision. CLI equivalent:

```sh
day-attendant --json reconcile 'proposal:OPERATION_UUID'
day-attendant --json reconcile 'proposal:OPERATION_UUID' \
  --resolution keep_observed --precondition OBSERVED_DIGEST
```

Never use manual SQLite edits, blanket force or deletion of private state as recovery.
If maintenance was interrupted, retry a backup: a dead maintenance owner is reconciled
under the writer lock. An active owner remains protected; check `doctor` before retry.
