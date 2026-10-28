# Linux validation — initial implementation

The architect authorized provisional Linux development in round 04 of the saved
conversation. This record describes checks actually run locally, not a macOS
deployment claim.

- Taskwarrior 3.5.0, conda-forge build `hef4216c_1`, private development prefix
  `.runtime/taskwarrior`; exact package URLs/hashes are recorded by the feasibility
  experiment. The first explicit manifest export was not a valid install file;
  it was corrected to `@EXPLICIT` plus the exact package URLs/hashes, and a fresh
  persistent prefix was successfully created from it.
- Final product suite: **30 tests, zero failures/errors/skips, 25.134 seconds**.
  Command: `ASISTENTE_TASK_BIN="$PWD/.runtime/taskwarrior/bin/task" python3 -B -m unittest discover -s tests -v`.
- An earlier 21-test suite passed. A subsequent version-check change failed because
  `task --version` must be invoked without the command's `rc.*` argument prefix;
  it was corrected and the full suite rerun before this final record.
- A CLI smoke session created a fictitious task in a temporary directory, returned
  the JSON envelope, produced a Spanish brief and passed SQLite integrity in doctor.
- Core tests include real child-process death at intent, Taskwarrior effects,
  auxiliary transaction and post-commit boundaries; idempotent creation; refusal
  to overwrite external changes; nested focus and resume recovery; stale revisions;
  snoozed/completed paused frames; two clients and a pulse; simultaneous notifier
  claims; notifier execution outside the writer lock and SQLite transaction;
  suppression, backoff, uncertain result and obsolete notices; DST ambiguity and
  nonexistent times; private state path checks; and scheduling dry-run.
- No product scheduler was installed. No real work data was used. The feasibility
  experiment, separately, observed `notify-send` acceptance and an offline user
  systemd manager. Product delivery tests use an observable test notifier rather
  than emitting dozens of desktop popups.

Known limitations for review: no safe operator conflict-resolution command, no
production backup/retention command, no recurrent tasks, and no Mac validation.
Conflict detection is deliberately conservative and may block writes pending
review. External native Taskwarrior writes remain outside the coordinator's normal
contract; two independent stores cannot share an atomic commit. Notifier acceptance
does not establish user reading or exactly-once delivery.


## Round 06 corrections

The architect read the full plain-text source in round 05 and requested three
blocking corrections. Global recovery validation now classifies every participant
before attempting any pending step. A notice invalidated after its durable claim
is canceled by a final revalidation before external delivery. Doctor reports an
expired sending claim as `expired_unknown`, with an action, without changing its
persisted status. Regression tests cover each case.

Additional tests verify real pending/completed/deleted dependencies, administrative
changes that do not register human progress and restrictive effective permissions
for private files. Taskwarrior child processes use a private umask.

Round 06 static source review approved the manual fictitious Linux pilot and closed
all three requested P0s. The architect did not execute the suite. It recorded a
residual P1 for claim ownership before periodic automation and did not request a
further change round before the manual pilot. The public CLI demo completed 18
steps, the notifier accepted one fictitious event notice and a second invocation
was suppressed. Its temporary state was removed.
