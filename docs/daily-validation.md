# Daily validation — 0.4.0

## Shared Python/SQLite backend

On 2026-10-28, local Linux discovery ran **177 cases** on the final SQLite backend:
163 executed and passed; the 14 private-D-Bus scenarios skipped in discovery, then
those same 14 executed and passed on an isolated bus. This is **177 distinct
executed/passing cases**, not 191. No live LLM inference was performed for this change.

The suite includes real SQLite participant/journal recovery, concurrent clients,
HTTP/CLI authority, real headless-Chrome UI regressions, backup/restore, process death
inside a task transaction, invalid graph restoration, and service ownership without
a task engine. No Taskwarrior executable is required by those runtime scenarios.

Six additional cases within that total use the explicitly selected legacy
Taskwarrior 3.5.0 binary solely to compare native row semantics and verify conversion:
field/dependency/status parity, preserved identity/metadata/notes, portable backup
roundtrip, explicit old-archive conversion, offline/invalid-graph rejection,
preservation/recovery of an interrupted operation, and interruption after atomic
publication. Some scenarios share a case; they are not counted twice. To run the
optional legacy checks, set `DAY_ATTENDANT_LEGACY_TASK_BIN` to an existing binary.
They deliberately skip on a new machine without the old engine.

A separate real-CLI smoke test ran from `/tmp` with an empty `PATH` and a nonexistent
legacy binary setting. Init, doctor, format-2 backup/verify and restore all succeeded
using only Python/SQLite, with application version 0.4.0. The package/service regression
also launches a copied distributable tree from another directory with no tools on PATH.

GitHub Actions remained disabled; no hosted CI or native macOS execution is claimed
for this change. The managed-Mac installation, native notification permissions and
sleep/resume checks remain the user's physical handoff. The same Python/SQLite task
store is used on both operating systems.

## Historical 0.3.0 evidence

Validation is split by what each check can establish. Controlled reasoning is used
for exact process/transaction races; actual models are used for semantic and image
interpretation. All acceptance data is fictional and separate from the empty personal
profile. Historical pilot evidence is retained without relabeling it as this release.

## Local Linux evidence

On 2026-10-28, the pre-gate integration discovery ran **124 cases**: 110 executed and
passed, while the 14 private-D-Bus scenarios intentionally skipped in ordinary discovery.
Those same 14 then executed and passed on the isolated notification bus: **124 distinct
executed/passing cases in total**, not 138. New source changes require relevant reruns.

The suite uses real Taskwarrior 3.5.0 and SQLite. The Claude transport contract also
executes a bounded fake native CLI to inject incomplete/malformed results, unexpected
capabilities, cancellation and model mismatch. These are protocol checks, not claims
about a live model's semantic quality.

Twelve directed regressions reproduce the architect's Round-20 findings with real
native stores: competing admission after a partial effect, dependency read-set changes
and cycle creation during recovery, provenance repair after an abandoned partial
capture, real maintenance-process death, protection of a live maintenance owner,
orphaned ready proposals, explicit reopening, claim deferral before transport,
maintenance waiting for a live transport despite an expired lease, batch invalidation
and wrong-provider informational guidance. Additional daily cases cover quiet/budget
fairness, memory bounds, backup corruption, failed space checks and offline restoration.

An actual running cron daemon executed a disposable profile's native scheduled tick.
The tick called no LLM, delivered one reminder through real notify-send to a private
D-Bus receiver, and created a verified daily backup. Repeated installation was
idempotent; repeated removal restored the unrelated crontab byte-for-byte. This was
repeated after the maintenance/transport changes. No desktop alert was sent by these
tests. systemd-user is unavailable on this machine, so its adapter was not presented
as a locally executed scheduler.

## Actual model and browser evidence

Both installed subscription transports read the same selected fictional screenshot:
Codex resolved `gpt-6.1-sol`; Claude Code 2.1.312 resolved `claude-haiku-5-5`. Both
returned the exact Spanish/English task titles and observed event times. No API key,
credit activation, provider fallback or external tools were used.

Actual browser clicks created tasks, edited a blocking dependency and symmetric
related links, created a calendar commitment, changed interface locale without
translating content, previewed custom calendar-day snooze, dismissed with Escape,
opened localized settings and collapsed chat. A selected screenshot then flowed
through web upload, real Codex interpretation and individual reviews into two native
tasks plus a calendar event. Task deadlines remained empty as requested and source
image references were retained.

Claude's connected app flow captured an English-titled low-priority task without a
deadline and kept a separate photography idea out of the task list. After restarting
Claude, it recalled that idea without changing tasks. Switching to Codex reconstructed
the same human context and again created no task. The resolved models persisted.

The real browser also checked settings/modal geometry at 1440×1000, 1080×800,
768×1024, 390×844 and 320×640: no document overflow, the dialog stayed in the viewport,
the footer remained visible, and its body retained a usable scroll area. These are
layout checks, not a claim that a human reviewed every new screen.

Private detailed traces and prior failed attempts remain under the ignored architect
run folder. Public CI runs the portable contracts against native stores on Linux/macOS;
its actual check results belong to the specific published commit, not this prose.

## Limits that remain explicit

Physical managed-Mac installation, native notification permission and sleep/resume
are the user's assigned handoff. Linux tests and a hosted macOS core runner do not
prove that environment. No live device synchronization, mailbox/Teams integration,
clinical intervention, perfect risk detection, popup delivery while suspended or
proof of human reading is claimed. The app uses declared facts and bounded context;
it does not infer inactivity simply because no progress was recorded.

## Daily readiness regression coverage

`tests/test_review_regressions.py` exercises the package's module launcher from an
arbitrary directory, service start/stop and the generated cron command without a
checkout entrypoint. It stages the distributable package tree; it does not replace
the separate package installation gate. Scheduler adapter tests cover switching
between cron and systemd, preserving unrelated jobs, failed replacement, repeated
uninstall and the doctor's actual installed flag. These use controlled scheduler
commands and do not install personal jobs or establish physical Mac evidence.

Native-store cases cover independent delivery/backup failures, dependency Undo in
both directions, eight capture outcomes plus a ninth unresolved item, public CLI
recovery during live maintenance, keep-observed waiting on transport, stale closing
actors before and after backup, successful recovery and both reconciliation retries.

The same focused test invokes `scripts/test_daily_regressions.mjs` in an isolated
headless Chrome with controlled HTTP responses and no model. It executes the actual
UI scripts to check editor snapshots and stale submissions, persistent capture
questions and changing receipts, every date-field family in a profile zone different
from the browser, calendar expiry/zone changes and access past twelve commitments,
and localized completed/retired details without translating card content.

Additional focused cases cover reviews whose proposed prerequisite reopens while
the model is responding, both sides of a prerequisite replacement, admission after
an external prerequisite change, and explicit fold 0/1 through native temporal
effects and calendar horizons. Ambiguous requests without a fold remain rejected.
Browser cases check blocker reasons and disabled controls after pending-badge and
locale renders, unchanged-poll DOM stability, and localized provenance labels with
unchanged stored values. Dependency indicators are checked as view-only: blocked and
blocking chips, highlighted direct links across re-renders and locales, lines in
board and list layouts, none at phone widths without horizontal overflow, and clearing.

Browser and native-store cases also cover every Details action against an externally
changed opening revision, with no replacement action or native effect. Unrelated
edits preserve stored date seconds and resolved repeated-hour instants across task,
calendar, reminder-pause and availability fields; changed ambiguous inputs retain
their drafts and fail before sending a request. These coverage additions do not
extend the pre-gate execution totals above.
