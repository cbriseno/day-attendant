# Day Attendant: connected Linux pilot (historical)

These notes describe the synthetic-only pilot at `8c9701d`. For current setup and
supported capabilities, see the [README](../README.md); for operating contracts,
see [daily operation](daily-operation.md).

## Language and access

The repository is `day-attendant`, the Python package is `day_attendant`, and the
command is `day-attendant`. Code, prompts, documentation and default interface are
English. The UI supports Spanish through authored localization catalogs. Existing
cards, continuity notes and conversation retain their creation language. The
conversation follows the human language independently of the locale selector.

The global command works from any directory with an explicit `--state`. The same
service handles browser, CLI and chat. Legacy direct writes cannot bypass proposals
in a connected workspace. Existing default state is never moved automatically.

## Review and recovery

Pending proposals remain outside the executable journal. Admission binds durable
authorization and prepared operation atomically; recovery verifies native effects
before completing the journal. Multiple fronts use native `start` independently.
Snooze preserves the deadline. Undo applies a limited inverse and checks conflicts
and dependencies. Model review runs outside the writer lock.

The final architect audit requested corrections to consequence acknowledgment,
temporal freshness, interpreted declaration scope, uncertain UI delivery, stale
forms, independent context corrections and effective bridge configuration. These
were corrected locally and checked with targeted regressions. The architect has
not re-audited the resulting fixes; its final response is not an approval claim.

Typed declarations are saved directly as facts, with review status `not_required`.
Interpreted chat declarations receive a separate scope review. New capacity risks
after a correction queue deduplicated informational guidance; they do not veto the
fact, edit tasks or claim that unavailable reasoning approved the decision.

A consequence acknowledgment binds its evidence, not its category. Time windows
use the current clock and reviews expire. The UI retains uncertain request keys
and sends observed form revisions. The bridge verifies effective restrictions,
honors the effective Codex home and persists the model. Failed agent startup leaves
stored state accessible in a visible degraded service.

## Evidence

Real-model scenarios and native browser checks are distinct from controlled-reviewer
regressions. Initial failures remain in the private architect run alongside the
corrections and reruns. The complete real traversal covers capture, an idea without
a task, persistent conversation, four compatible fronts, an 80/45 review before
mutation, estimate correction, reevaluation, cancellation, explicit continuation,
snooze preserving due dates, undo and legitimate rest.

The model is probabilistic and these are bounded synthetic observations. They do
not validate psychological outcomes or perfect risk detection. Current coverage
and remaining platform checks are documented in [daily validation](daily-validation.md).
