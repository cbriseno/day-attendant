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
