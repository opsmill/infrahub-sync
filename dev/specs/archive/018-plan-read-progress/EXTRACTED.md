# Extraction Record

**Extracted on**: 2026-11-14
**Extracted by**: speckit.opsmill.extract

## ADRs Created

- `dev/adr/0035-the-engine-reports-each-sides-read-to-an-optional-observer.md` (from R1, R2, R3,
  R4, R6, R8, R10)

## Knowledge Updated

- `docs/docs/development/knowledge/sync-architecture.md` (new subsection "Read state of each
  side of a plan", after "One registered run"; from R1, R2, R3, R4, R6, R7, R9, R10)
- `docs/docs/development/knowledge/repository-tour.md`, section "The Sync service and its
  worker": `read_progress.py` (from R10)
- `dev/adr/README.md` and `docs/docs/development/adr-index.mdx` (ADR index)

## Guidelines Updated

- `docs/docs/development/guidelines/secret-redaction.md` (new subsection "Store only the error's
  class name in a progress document"; from R8)

## Not extracted

- R5 (which kinds are counted), R6 (the returned states), R11 (the `runs show` lines) and R12
  (freshness), as what a person who reads a run views: already in
  `docs/docs/reference/sync-http-api.mdx` ("Read state of each side"),
  `docs/docs/reference/cli.mdx` and `docs/docs/running-a-sync.mdx`.
- `data-model.md` (the stored document of each side): already in
  `docs/docs/reference/durable-product-records.mdx` ("Record and identity contract").
- `contracts/run-response.md`: already in `docs/docs/reference/sync-http-api.mdx`.
- `contracts/cli-output.md`: already in `docs/docs/reference/cli.mdx` and
  `docs/docs/running-a-sync.mdx`.
- The rule "an observer's failure never changes the result", as a guideline: it is in ADR 0035
  and in `sync-architecture.md`. The same rule for the apply observer is already in
  `docs/docs/development/knowledge/planned-write-and-apply.md`.
- The node-count measurement (`opsmill-implement-report.md`, "Node counting"): one measurement
  with the in-memory store; no rule follows from it.
- `plan.md`, `quickstart.md`, `tasks.md`, `checklists/`, `critiques/`, `alignment-check.md` and
  `opsmill-implement-report.md`: execution records.
- `spec.md`, "Out of Scope" (the state of the comparison and of the plan write, node counts
  during a read, server-sent events): not built.

## Open at archive time

`tasks.md` has no open task: all 29 tasks are ticked.

No GitHub issue was found for any item below. The searches were `read state`, `nodes_by_kind`,
`Redis count` and `counts unavailable` on `opsmill/infrahub-sync`. An issue under another title
is possible.

- The time to count the nodes of a side was never measured with the Redis store, not tracked
  (R5, "Open measurement"). With DiffSync's in-memory store, the count took 0.026% of the time
  to add 7,659 nodes. With the Redis store, `count(model)` scans the keys of the store for each
  kind. `opsmill-implement-report.md` (second review, finding 4) says the Sync service refuses a
  configuration that sets a store, so the Redis store is not used on this path today.
- Review findings that `opsmill-implement-report.md` marks as deferred, not tracked:
    - A failed node count is returned as a side with no nodes (`_counted_nodes` in
      `infrahub_sync/potenda/__init__.py`). To tell "not known" from "zero", the run response
      needs a new field.
    - The read types do not enforce the fields of each state (`SideRead` in
      `infrahub_sync/client/models.py`), and the finding says the stored document is not
      validated on write. On 2026-11-14 the store checks the fields and the values of a document
      before it writes it (`_check_side_read_document` in
      `infrahub_sync/product_store/store.py`). Whether that check covers what the finding asked
      for is not known.
    - The two warnings "still reading after the execution completed" and "does not parse" repeat
      on every read of the run (`infrahub_sync/service/read_progress.py` and `store.py`).
    - Each client applies the rule "latest `plan` or `sync` entry" itself. A run-level field
      would change the API.
    - The read-state `UPDATE` does not require the ID of the worker that claimed the execution
      (`record_side_reads` in `store.py`).
    - A lock of the SQLite store can drop one read-state write; the side then shows no state.
    - `infrahub_sync/service/read_progress.py` uses the standard `logging` module, like the
      code around it.
    - Simplification suggestions: use `SideRead.model_fields` for the stored fields, and call
      `_store` directly. Not checked again.
- No live input was found that makes a read fail after it started, so the `failed` state had no
  live check, not tracked (`opsmill-implement-report.md`, "Suggested next steps").

Statements of `research.md` that the code no longer matches:

- R3 names a store method `record_side_read(run_id, flow_run_id, side, document)`. The code has
  `record_side_reads(run_id, flow_run_id, documents)`, which stores one or both sides in one
  write.
- R4 says the worker records `waiting` before it calls `execute_run`. The worker records both
  sides as `waiting` as the first step of the stage, before the destination schema read and
  before a `sync` waits for its write guard (`_execute_stage` in
  `infrahub_sync/service/flow.py`).
- R6 derives the returned state from the stored verdict of the execution only. The code also
  uses the flow run state the Sync API last observed: `failed`, `crashed` or `cancelled` counts
  as an execution that ended without completing (`stage_reads` in
  `infrahub_sync/service/read_progress.py`).
- R5 does not say what happens when the node count fails. The engine logs one warning with the
  error class and reports no counts; the read still succeeds.

## Archive

The spec directory moved to `dev/specs/archive/018-plan-read-progress/` as a historical record.
