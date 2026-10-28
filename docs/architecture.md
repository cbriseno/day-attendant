# Linux V1 contract

Architect consultation: saved rounds 04–06, same conversation
`6ae11817-25b8-83e8-9345-b8f4cbf63b9e`. Round 06 approved the manual fictitious
Linux pilot after reading the source; local tests were reported, not executed by
the architect. This does not validate macOS deployment.

- Taskwarrior 3.5.0 is the sole authority for task fields. SQLite stores immutable
  operation intents, focus frames, capture metadata, events, settings and attempts.
- All ordinary writes go through a kernel lock and short SQLite transactions.
  Slow Taskwarrior/notifier invocations never occur inside a SQLite write transaction.
- Mutation intent records full before/after snapshots for recovery, not a second
  mutable table of task truth. Administrative `modified` and derived fields are
  excluded from semantic comparison; a change with no content difference is not
  treated as a conflicting write.
- A prepared operation can be replayed only from its before state or recognized
  from its complete after state. Other content becomes a conflict. An incomplete
  focus cannot lose its previous frame: frame changes and operation confirmation
  commit together. External writers are unsupported during coordinated operation;
  there remains an unavoidable race between a last external read and local commit.
- Focus supports one active target and nested frames. Unavailable previous targets
  require explicit skip. Revisions prevent stale focus requests from quietly winning.
- `pulse` is pure. `notify` prepares a stable episode identity, revalidates it,
  claims a durable attempt and invokes the notifier outside locks/transactions.
  An interrupted attempt becomes uncertain after a two-minute lease. It requires
  explicit retry. Ordinary failures use bounded backoff and accepted attempts use
  configurable suppression. Exactly-once delivery and human reading are not claimed.
- UTC instants and optional IANA origin zones are distinct. Naive inputs require a
  zone; repeated/nonexistent local hours are rejected unless resolved explicitly.
- JSON envelope: `schema_version`, `ok`, `operation_id`, `data`, `error`. Read data
  and errors use stable machine identifiers. Human output defaults to Spanish.
- State resides outside the checkout with a private root directory. The assistant
  uses dedicated TASKRC/TASKDATA and does not touch the user's normal Taskwarrior.

Deferred: scheduler installation, production backup/retention commands, a safe
operator conflict-resolution workflow, sync between machines, recurrence, UI,
HTTP API, MCP and image/NL interpretation. Linux notifier acceptance was tested in
the feasibility experiment. No real scheduler is installed; the current environment
has an offline user systemd manager. Mac build and real deployment remain pending.

Round 06 residual P1: external delivery results need a claim generation/ownership
check before periodic automation. A late original notifier must not overwrite a
claim already reclassified by another process. This remains outside the approved
manual-pilot scope, alongside production backups and conflict resolution.
