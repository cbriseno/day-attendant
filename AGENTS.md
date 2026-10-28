# Operating this assistant

Use `day-attendant --json --state PATH board ...` for the selected daily profile. The
legacy task/focus commands cannot write connected state. Use explicit workspace
paths and stable request keys; confirm only the current proposal generation. The local service
owns state access; do not edit SQLite or invoke Taskwarrior directly on its state.
Read `README.md` and `docs/daily-operation.md` for commands and limits. Follow the user's language;
default to short English reports with UUID references and one next action.

Extract only facts visible in the user's text or images. Save unambiguous facts
without a routine confirmation step. Ask about material ambiguity, including an
unclear date, person, requested action or repeated DST hour. Never invent a task,
deadline or source link. Never open Teams chats to obtain more context.

Check existing tasks before creating. Use a stable idempotency key for each user
request, and keep that key on retries. Reuse permanent UUIDs, never numeric task
IDs. Read the current revision before a state change and pass it as `--expect`.
Use `--progress` only when the user or a confirmed action establishes real progress.

Use `brief` to recommend a next action. `pulse` only evaluates known facts;
`notify` explicitly attempts local delivery. Never say that a notification was
read or that disconnected sources were checked. Do not install schedulers or run
unattended notification loops without the user's explicit instruction.

On recovery conflicts, preserve the state and report the operation key. Do not
force an overwrite or delete the database. Consult the saved architect conversation
before architectural changes or push: the local run metadata is under `arquitecto/`.
Keep `.lavish/`, `arquitecto/`, `.runtime/` and private state out of publication.

For implementation changes, run the real integration tests with Taskwarrior 3.5.0
and review the affected failure paths. Linux is validated locally; Mac requires
real deployment and notification tests, not simulated claims.

Use `dependency_update` for blocking prerequisites and `related_update` for
symmetric nonblocking links. Preserve unrequested links when replacing lists.
Completing a prerequisite never starts its dependents; retiring it does not fulfill
it. Task content keeps its creation language independently of the interface locale.

Only the selected subscription harness/model may perform reasoning. Reconstruct
continuity from profile facts and sourced excerpts; never import global histories,
activate credits, buy API usage or silently fall back after an auth/quota failure.
The native scheduler performs known-data reminders and backups, never LLM turns.

Use `backup`, offline `restore` to a new directory, and supervised `reconcile` for
recovery. The diagnostic read view is not permission to bypass incomplete operations.
