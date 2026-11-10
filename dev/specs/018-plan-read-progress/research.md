# Research: Read state for each side of a Plan

All findings were read from branch `018-plan-read-progress`, created from `origin/017-sync-run-events-logs` at
`15e2262a`. That branch holds the code of specs 016 and 017.

## R1. Where a side's read starts, succeeds and fails

**Finding**: `Potenda.load_both_sides` (`infrahub_sync/potenda/__init__.py:516`) resolves the destination's
lineage and metadata read, then runs `source_load` and `destination_load`, in parallel on two threads when
`concurrent_load` is true (the default). Both call `_load_side(side=..., adapter=...)` (`potenda/__init__.py:493`)
with `side="A"` for the source and `side="B"` for the destination. `_load_side` logs
`Load: Importing data from <adapter>`, calls `load_one_side`, writes the side's snapshot, and wraps any failure in
`ValueError("An error occurred while loading <adapter>: ...")`.

**Decision**: `_load_side` reports three events to an optional observer: the read started, the read succeeded,
and the read failed. A read succeeds only when `load_one_side` and `_write_side_snapshot` both return, which is the
point where `_load_side` already sets `completed = True`. Side `A` is reported as `source` and side `B` as
`destination`.

**Rationale**: `_load_side` is the single place where both reads of a Plan and of a `sync` start and end, for every
adapter. Reporting there needs no adapter change (constitution principle III).

**Alternatives considered**: Report from each adapter's `load`. Rejected, because nine adapters would change and a
new adapter could forget it.

## R2. How Potenda receives the observer

**Finding**: `execute_run` (`infrahub_sync/execution.py:1116`) builds the engine through a `PotendaFactory` with
eight pinned keyword arguments, then sets `ptd.force_full_extract` on the result (`execution.py:1229`). It already
has private keyword-only seams that `run_remote_request` never sets, such as `_plan_applier_factory` and
`_run_file_mode`.

**Decision**:

- Add a `SideReadObserver` protocol in `infrahub_sync/potenda/read_progress.py` with two methods:
  `read_started(side)` and `read_ended(side, report)`. `report` is a frozen `SideReadReport` (data-model.md).
- Add a `read_observer: SideReadObserver | None = None` attribute on `Potenda`.
- Add a private `_read_observer` seam to `execute_run`, which sets `ptd.read_observer` after the factory call, as
  `force_full_extract` is set today. The factory's eight-keyword shape does not change.
- `_load_side` calls the observer inside `try`/`except Exception`. A failing observer logs one WARNING with the
  error class and never changes the read's result (FR-007).

**Rationale**: The CLI and direct Python callers pass no observer and keep their behavior. Potenda imports nothing
from the service.

**Alternatives considered**:

- Add a ninth keyword to `PotendaFactory`. Rejected, because the pinned call shape is tested and shared with the
  CLI's factory wrapper.
- Let Potenda write to the product store. Rejected, because it would tie the engine to the service.

The protocol has one implementation, in the worker. Constitution principle VII asks for two real callers before
adding an abstraction. The exception is recorded in the plan's Complexity Tracking.

## R3. Where the read state is stored

**Finding**: Each stage execution is a row of `prefect_executions`, keyed by `(run_id, flow_run_id)`
(`infrahub_sync/product_store/store.py:86`). New columns are added on start-up by
`_migrate_prefect_execution_columns` (`store.py:851`), which runs `ALTER TABLE prefect_executions ADD COLUMN` for
each missing column of `_PREFECT_EXECUTION_LIVENESS_COLUMNS` (`store.py:199`). Rows are inserted once
(`_insert_prefect_execution`) and then changed by targeted `UPDATE` statements, such as
`observe_prefect_execution` (`store.py:1375`). Every store method opens its own connection.

**Decision**:

- Add two nullable `TEXT` columns, `source_read` and `destination_read`, each holding one side's JSON document.
  Add them to a new tuple `_PREFECT_EXECUTION_READ_COLUMNS`, migrated by the same method.
- Add `record_side_read(run_id, flow_run_id, side, document)` to the store and the projection. It runs
  `UPDATE prefect_executions SET <side>_read = ? WHERE run_id = ? AND flow_run_id = ? AND terminal_at IS NULL`.
  `side` is checked against the two names before it selects a column.
- Add the two columns to `_SELECT_PREFECT_EXECUTIONS` and `_INSERT_PREFECT_EXECUTION`, and two fields to
  `PrefectExecutionLink`.

**Rationale**:

- One column for each side means the two read threads never write the same column, so no read-modify-write merge
  is needed.
- The `terminal_at IS NULL` guard drops a write that arrives after the Sync API ended the execution, for example
  from a worker that kept running after it was marked interrupted.
- The migration pattern already exists and works for SQLite and PostgreSQL.

**Alternatives considered**:

- One JSON column for both sides. Rejected, because the two threads would race on a read-modify-write.
- A separate table. Rejected, because two fields on an existing row are enough (principle VII).

## R4. The initial "waiting" state

**Finding**: A Plan checks the destination's lineage and reads its server version before either side is read
(`load_both_sides`). A failure there stops the stage with both sides unread.

**Decision**: Before the worker calls `execute_run` for a `plan` or `sync` stage, it records both sides as
`waiting`. The run response then shows the block for every execution recorded after this feature. When the stage
stops before the reads, both sides stay `waiting`, and R6 shows them as `not_started`.

**Rationale**: Without a stored `waiting`, a Plan stopped before the reads would look like an execution recorded
before this feature, and the UI would hide the block.

## R5. Node counts and read mode

**Finding**: Each adapter is a DiffSync `Adapter`. `Adapter.count(model)` (diffsync `__init__.py:890`) and
`Adapter.get_all_model_names()` (`__init__.py:706`) read the adapter's store, local or Redis.
`Potenda._side_full_extract[side]` (`potenda/__init__.py:298`) is true after a full extract (`:439`) and, on the
incremental path, true only when every top-level kind was read in full (`:489`). The plan summary's `by_kind`
uses the same model names (`infrahub_sync/plan/review.py:121`).

**Decision**:

- `nodes_by_kind` holds `adapter.count(name)` for every name in `adapter.top_level` and in
  `adapter.get_all_model_names()`, sorted by name. A top-level kind with no node shows `0`.
- `read_mode` is `full` when `_side_full_extract[side]` is true, and `incremental` otherwise.
- Both are computed in `_load_side` after the snapshot is written, only for a read that succeeded.

**Rationale**: The count is the number of nodes compared, whether they came from the cache or from the system
(FR-003). A top-level kind with zero nodes is useful to an operator who expects some.

**Open measurement (critique R3)**: with DiffSync's Redis store, `count(model)` scans the store's keys for each
kind (`diffsync/store/redis.py:210`). The implementation measures the counting time on the largest example
configuration and records it in the implementation report. If it exceeds 5% of the side's read time, it counts
once over `get_all_model_names()` with a single pass instead.

## R6. What the run response shows

**Finding**: `RunService._resource_with_observations` (`infrahub_sync/service/service.py:1160`) builds one
`OrchestrationSummary` (`infrahub_sync/client/models.py:268`) for each `PrefectExecutionLink`, and reads
`link.terminal_state` to set `state` (016 R7).

**Decision**:

- `OrchestrationSummary` gains `reads: StageReads | None = None`. `StageReads` holds `source` and `destination`,
  each a `SideRead | None`.
- When the link has neither document, `reads` is `None` (an execution recorded before this feature, or a `verify`
  or `apply` execution).
- When `link.terminal_state` is set to anything other than `completed`, a side stored as `waiting` is returned as
  `not_started`, and a side stored as `reading` as `interrupted`. Neither gets `ended_at`. The stored document is
  not changed.
- When `link.terminal_state` is `completed`, a side still stored as `waiting` or `reading` is returned as `None`,
  and the service logs one WARNING with the run ID and the side. A completed `plan` or `sync` execution read both
  sides, so its end write was lost (critique M2).
- A stored document that does not parse is returned as `None` for that side, and the service logs one WARNING
  with the run ID and the side.

**Rationale**: The derivation needs no write, so it also covers a worker that was killed (FR-009).

## R7. The log line when a read ends

**Finding**: Potenda logs through `logging.getLogger("infrahub_sync.potenda")` (`potenda/__init__.py:46`). The
service worker forwards the `infrahub_sync` logger hierarchy to the Prefect run log through the log bridge when a
Prefect run context exists, and `GET /runs/{run_id}/logs` (spec 017) returns those lines.

**Decision**: `_load_side` logs at INFO, after the observer call:

- `Load: source read succeeded (full): 5 nodes; LocationSite 2, DcimDevice 3`
- `Load: destination read failed (ConnectionError)`

The line holds the side, the state, the read mode, the counts and the error class, and no exception text
(FR-010, FR-013).

**Rationale**: The CLI's direct runs, which have no UI, see the same information in their log.

## R8. The error class of a failed read

**Decision**: `SideReadReport.error_class` is `type(exc).__name__` of the exception that `_load_side` catches,
before it wraps it in `ValueError`. The message is never stored.

**Rationale**: A class name is a fixed identifier of the code, so it holds no secret and no address (FR-013). It
is enough to tell a connection failure from an authentication failure or a validation failure.

## R9. The `sync` stage

**Finding**: The service's `sync` stage calls `_plan(..., composed_sync=True)` (`infrahub_sync/service/flow.py`),
which calls `execute_run(operation="plan", ...)`, the same path as a `plan` stage. `verify` runs
`_read_verified_plan` (`execution.py:860`), which builds no adapter, and `apply` runs `apply_plan`, which loads no
side.

**Decision**: `_plan` receives the observer and passes it to `execute_run`. `_execute_stage` builds it for `plan`
and `sync` only. `verify` and `apply` executions keep `reads` empty.

## R10. The worker's observer

**Decision**: `infrahub_sync/service/read_progress.py` holds `StoreSideReadObserver(projection, run_id,
flow_run_id)`. Each method builds the side's document (data-model.md) with the current UTC time and calls
`projection.record_side_read`. It keeps each side's `started_at` in memory so that the end document repeats it.
`_execute_stage` receives `flow_run_id`, which `service_sync_run` already holds after the claim.

**Rationale**: Each call opens its own store connection, so the two read threads need no lock.

## R11. The CLI

**Finding**: `_echo_run` (`infrahub_sync/cli.py:612`) prints `key: value` fields for the latest orchestration
entry (`resource.orchestration[-1]`).

A registered run records its `verify` and `apply` stages as later executions of the same run
(`RunService._owned_run(run_id, ..., "verify")`, `infrahub_sync/service/service.py:341,370`), so the latest entry
is often a `verify` or `apply` execution with no read state (critique M1).

**Decision**: `_echo_run` reads the read state from the latest entry whose `purpose` is `plan` or `sync`, not from
`orchestration[-1]`. The existing fields keep reading `orchestration[-1]`. When that entry's `reads` is set,
`_echo_run` adds two fields after `prefect_flow_run_url`,
`source_read` and `destination_read`, formatted as
`state=succeeded mode=full started_at=<ISO time> ended_at=<ISO time> nodes=DcimDevice:3,LocationSite:2`, with
`error=<class>` for a failed read. Keys with no value are left out. When `reads` is `None`, neither field is
printed.

## R12. Freshness (SC-001)

**Finding**: The worker writes each state when it changes, and `GET /runs/{run_id}` reads the store on every
request, with no cache.

**Decision**: No extra mechanism. The delay an operator sees is the UI's refresh interval plus one store write.
