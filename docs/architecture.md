# Current daily architecture

This section supersedes the historical pilot notes below. A portable Python domain
owns all writes through a shared coordinator and native journal. Taskwarrior 3.5.0
is the task authority; SQLite stores calendar events, declarations, provenance,
relations, messages, proposals, authorization, facts and delivery receipts.

Codex app-server and Claude Code CLI implement the same bounded inference interface.
The model receives selected profile facts and images, has no external task-writing
tools, and returns typed interpretations/reviews. The selected harness/model is
validated for every conversation, proposal and informational-guidance turn. Epochs,
generations, fingerprints and native read/write guards prevent stale application.

Pending interpretations are not executable operations. Admission records a decision,
operation and admitted transition together. An incomplete executable operation prevents
admission of another until recovery/reconciliation. Native recovery compares its
write participants against before/after states and its read set against recorded
preconditions; it does not overwrite changed prerequisites or external native edits.

Blocking graph edges are native Taskwarrior depends values; related pairs are sorted
symmetric SQLite links. Active, snoozed, awaiting and blocked are independent facts.

Kernel locks separate the coordinator, server ownership, maintenance ownership and
notification transport. Concurrent deliveries hold a shared transport lock through
receipt commit; snapshot maintenance acquires it exclusively outside any SQL write
transaction. Dead maintenance is recovered through kernel ownership rather than PID
expiry. Backups capture immutable copies of both SQLite stores and selected files,
then compress/publish atomically. They never advance a reconciliation target.

Linux cron/systemd and macOS launchd adapters execute the same bounded known-data tick.
Delivery policy, budgets, coalescing, suppression and backups are independent of LLM
availability. OS/harness interfaces remain adapters; there is no extra scheduler
daemon, global skill, SDLC coordinator, cloud sync or inbox integration.

## Historical pilot design

# Architecture

The loopback browser interface and CLI share a typed local HTTP service. Taskwarrior
3.5.0 owns responsibility fields; private SQLite owns continuity, declarations,
proposals, authorization decisions, operation receipts, conversation and reminders.

A proposal is not a recoverable operation. Only admission writes authorization,
prepared operation and admitted proposal in one transaction. Applying a native
operation confirms its result, fact and revision together. Recovery reconciles
uncertain effects before replay and blocks incompatible external changes.

Model calls run outside the writer lock. Inputs bind target state, active work,
context, confirmed history, temporal feasibility and a generation. Reviews expire
after two minutes or a relevant material change. Capacity windows use the current
clock; their consequence identity distinguishes five-minute remaining-window bands.
This band is a review freshness detail, not inferred human progress.

Acknowledgment identifies a specific consequence and evidence, never an entire
category. Cancellation and supersession invalidate a generation. Chat attempts
also carry generations; interrupted interpretation cannot create a late operation.

The Codex app-server bridge uses stdio, existing ChatGPT authentication, a fixed
workspace/model and restricted effective configuration. The UI exposes no generic
Codex RPC, shell or terminal. A failed bridge startup leaves HTTP available in a
visible degraded state. No silent provider/model substitution occurs.

Browser writes use stable uncertain-delivery keys and observed revisions. Locales
translate authored interface copy only. Workspace timezone is explicit and shared
with chat; Intl formats that selected zone independently of language.
