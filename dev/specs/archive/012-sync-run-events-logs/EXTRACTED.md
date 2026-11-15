# Extraction Record

**Extracted on**: 2026-11-14
**Extracted by**: speckit.opsmill.extract

## ADRs Created

- `dev/adr/0033-an-event-is-saved-in-the-transaction-of-its-decision.md` (from R2, R6, R10 and
  `data-model.md`, "Event")
- `dev/adr/0034-a-run-log-is-read-from-prefect-through-the-sync-api.md` (from R1, R3, R4, R9 and
  `spec.md`, "Out of Scope")

## Knowledge Updated

- `docs/docs/development/knowledge/orchestration-prefect.md`, section "What a stage leaves in
  Prefect":
    - the bullet "Refused claim" corrected: the flow returns a `Failed` state and does not raise
      the error again (from R1);
    - new bullet "Failure exit" (from R1);
    - new subsection "How the Sync API reads a run's log" (from R3, R4, R9).
- `docs/docs/development/knowledge/repository-tour.md`, section "The Sync service and its
  worker": `events.py` and `run_log.py` (from R2, R3)
- `docs/docs/reference/durable-product-records.mdx`: the sentence on `AuditEvent` (from R2)
- `dev/adr/README.md` and `docs/docs/development/adr-index.mdx` (ADR index)

## Guidelines Updated

- `docs/docs/development/guidelines/secret-redaction.md`, boundary table:
    - row "Sync HTTP API": a route that returns text read from Prefect redacts before it cuts
      (from R3, R9);
    - row "Registered service worker": a recognized failure and a refused execution claim end
      with a returned `Failed` state, and every line of the service flow goes through
      `SyncFlowLines` (from R1, R3).

## Not extracted

- R5 (the fixed cause and next action of a destination schema read failure): already in ADR 0021
  and in `docs/docs/development/knowledge/repository-tour.md` (`failure_cause.py`).
- R7 (Prefect retention settings): already in `docs/docs/compose-deployment.mdx` ("How long run
  logs are kept").
- R8 (the 2-second poll of `--follow`): already in `docs/docs/reference/cli.mdx`.
- R10 as API behaviour (`limit` and `offset` on `GET /events`, and the limit of paging by
  offset): already in `docs/docs/reference/sync-http-api.mdx` ("List events").
- `data-model.md`, the event type table: already in `docs/docs/reference/sync-http-api.mdx`. It
  was not copied, because the table of the spec is no longer complete (see below).
- `data-model.md`, "Run log line": already in `docs/docs/reference/sync-http-api.mdx` ("Read a
  run's log").
- `data-model.md`, "Run failure": replaced by spec 007; already in ADR 0021.
- `contracts/events-route.md` and `contracts/logs-route.md`: already in
  `docs/docs/reference/sync-http-api.mdx`.
- `contracts/cli-output.md`: already in `docs/docs/reference/cli.mdx`,
  `docs/docs/running-a-sync.mdx` and `docs/docs/orchestration.mdx`.
- `plan.md`, `quickstart.md`, `tasks.md`, `checklists/`, `critiques/`, `alignment-check.md` and
  `opsmill-implement-report.md`: execution records.

## Open at archive time

`tasks.md` has no open task: all 52 tasks are ticked.

No GitHub issue was found for any item below. The searches were `events offset`, `AuditEvent`,
`runs logs follow`, `event paging` and `missing branch` on `opsmill/infrahub-sync`, in every
state. An issue under another title is possible.

- Review items that `opsmill-implement-report.md` marks as deferred, read in the code on
  2026-11-14 unless the item says otherwise:
    - `GET /events` and `events --follow` page by `offset`, not tracked. When an event with an
      earlier time is saved late, a reader can skip one event or read one event twice.
      `docs/docs/reference/sync-http-api.mdx` states this limit. Whether to page by
      `(created_at, event_id)` is not decided.
    - `AuditEvent` (`infrahub_sync/product_store/models.py`) stores the configuration, the
      version and the branch both as fields and in `related`, and nothing checks that they
      agree, not tracked.
    - `EventFilters` (`infrahub_sync/product_store/models.py`) accepts a `since` that is not
      before `until`, not tracked.
    - The next action for a missing destination branch is "Create it, or start the run with
      `--branch <existing branch>`" (`infrahub_sync/runtime_schema/errors.py`), not tracked. It
      does not help when the branch is set in the destination settings.
    - Stage events and cancellation events repeat the same receipt lookup in
      `infrahub_sync/service/flow.py` and `infrahub_sync/service/liveness.py`, not tracked. Not
      checked again.
    - In `infrahub_sync/cli.py`, one variable holds both "run ended" and "empty reads" in the
      follow loop, not tracked. Not checked again.
    - Low-severity items, not tracked, not checked again: `actor or "unknown"` gives no warning;
      `runs logs --follow` stops on one 503; the client models and the offset helpers are
      duplicated; the tests share one fake for `read_logs`; a docstring of `worker.py` is out
      of date; no test checks that a token of another account reads a log.
- A verify run leaves `run_phase` at `accepted` when it ends, not tracked. Whether it should
  change is not decided (`opsmill-implement-report.md`, "Suggested next steps").
- The Infrahub branch `test-017-timeline` existed on the development stack when the report was
  written, not tracked. Whether it still exists is not known.
- Three kinds of record are saved in a transaction other than the one of their event, not
  tracked: the run record of a new request, a new configuration version, and a refusal that the
  store decides inside a call. ADR 0033 lists them under Consequences. Whether to change them is
  not decided.
- The liveness check ends two kinds of execution with no event, not tracked: an execution that
  no worker claimed in time (`abandon_execution`), and a cancellation whose recovery deadline
  passed (`expire_execution_cancellation`). ADR 0033 lists them under Consequences. Whether they
  should have an event is not decided.
- Whether Prefect stores an engine traceback for a flow run that ends with a returned `Failed`
  state was not checked, not tracked. It depends on the entry point the worker uses. Research R1
  did not check it either.
- No figure for the size of a run's log is known, not tracked. The log route reads every line of
  the run from Prefect for each request.

Statements of the spec documents that the code no longer matches:

- The event type table of `data-model.md` is not complete. Spec 014 added the outcome
  `accepted-withdrawn`, recorded as `infrahub_sync.run.accepted`. Spec 016 added
  `infrahub_sync.run.resumed`. The current list is in `docs/docs/reference/sync-http-api.mdx`.
- `data-model.md`, "Run failure": spec 007 replaced the failure cause with the structured record
  of ADR 0021.

## Archive

The spec directory moved to `dev/specs/archive/012-sync-run-events-logs/` as a historical
record.
