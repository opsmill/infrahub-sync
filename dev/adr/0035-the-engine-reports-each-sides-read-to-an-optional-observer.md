# 35. The engine reports each side's read to an optional observer, and the Sync API derives the read state it returns

**Status**: Accepted
**Date**: 2026-11-14
**Source**: `dev/specs/archive/013-plan-read-progress/research.md` (R1, R2, R3, R4, R6, R8, R10)

## Context

A `plan` or `sync` stage reads the source and the destination before it compares them. The stored
run held nothing for each side. An operator who viewed the run could not tell when each side was
read, whether each read succeeded, how many nodes each side returned, or which side failed.

Three facts of the code shaped the decision:

- Every read of every adapter starts and ends in one engine method, `Potenda._load_side`. By
  default the two sides are read on two threads at the same time.
- The engine (`infrahub_sync/potenda/`) is also used by direct Python callers and by the direct
  Prefect flow, which have no product store. The engine imports nothing from the service.
- A worker can be killed during a read, so the last state it stored can stay `reading`.

## Decision

- **Where the read is reported.** `Potenda._load_side` reports that a side's read started, and
  how it ended. A read succeeded only when the load and the side's snapshot write both returned.
  No adapter reports anything.
- **Optional observer.** The engine reports to a `SideReadObserver`
  (`infrahub_sync/potenda/read_progress.py`), a protocol with `read_started(side)` and
  `read_ended(side, report)`. `Potenda.read_observer` is `None` unless a caller sets it.
  `execute_run` takes a private keyword, `_read_observer`, and sets the attribute after it built
  the engine; the engine factory's arguments do not change. Only the Sync worker passes an
  observer.
- **A report never fails the read.** When the observer raises, the engine logs one warning with
  the error's class name and continues. When the node count fails, the engine logs one warning
  and reports no counts. The read's result is the same in both cases.
- **What a report holds.** A read that succeeded reports its mode (`full` or `incremental`) and
  the number of nodes of each kind. A read that failed reports the class name of the error and
  never its message, because a message can hold an address or a secret.
- **Where the state is stored.** The worker's observer, `StoreSideReadObserver`
  (`infrahub_sync/service/read_progress.py`), writes one JSON document for each side to the
  execution's row in `prefect_executions`, in two nullable columns, `source_read` and
  `destination_read`. The write changes the row only while the execution has no terminal verdict.
  When the write fails, the worker logs a warning and the stage continues.
- **The state before the reads.** At the start of a `plan` or `sync` stage, before the
  destination schema read and before a `sync` waits for its write guard, the worker stores both
  sides as `waiting` in one write. A `verify` or `apply` execution stores no document.
- **What the run response returns.** The Sync API builds the `reads` field of each orchestration
  entry when the run is read, and writes nothing:
    - When the execution ended without completing, a side stored as `waiting` is returned as
      `not_started`, and a side stored as `reading` as `interrupted`, with no end time. The
      execution ended without completing when its stored verdict is not `completed`, or when it
      has no verdict yet and the flow run state the Sync API last observed is `failed`, `crashed`
      or `cancelled`.
    - When the execution completed and a side is still stored as `waiting` or `reading`, that
      side is returned as empty and the service logs a warning: the write that ended the read
      was lost.
    - A stored document that does not parse is returned as empty for that side, with a warning.
    - An execution with neither document returns `reads` as `null`.

## Consequences

- An operator views the state of each side through `GET /runs/{run_id}` and `runs show` while
  the stage runs and after it ends, including after a worker was killed, because the returned
  state is derived and needs no last write.
- Direct Python callers and the direct Prefect flow pass no observer and behave as before. They
  read the same information in one log line for each side that the engine writes when a read
  ends.
- A later report from the engine to the service follows the same shape: an optional protocol in
  `infrahub_sync/potenda/`, set by the worker, whose failure is logged by error class and never
  changes the result of the work it reports on. The apply progress marker
  (`ApplyProgressObserver`) is the second use.
- Each side has its own column, so the two read threads never write the same value and the store
  needs no read-then-write merge.
- A lost or failed write leaves a side with no state or with an older state. The run response
  then returns less information; it never returns a wrong final state for a completed execution.
- A failed node count is returned as a read with no counts. The response does not tell "no
  nodes" from "counts not known".
- An execution recorded before this decision has no document, and its `reads` is `null`.
- Each client that shows one read state for a run picks the latest `plan` or `sync` entry itself;
  the run response has no run-level read state.
- The read-state write does not check which worker claimed the execution.
- The two columns are added at start-up by the store's existing nullable-column migration.

## Alternatives considered

- **Report from each adapter's `load`.** Rejected: nine adapters would change, and a new adapter
  could leave the report out.
- **A new argument of the engine factory.** Rejected: the factory's call shape is shared with the
  CLI's factory wrapper and pinned by tests.
- **Let the engine write to the product store.** Rejected: the engine would depend on the
  service.
- **One JSON column for both sides.** Rejected: the two read threads would each read the column,
  change it and write it back, and one write could remove the other.
- **A separate table.** Rejected: two fields on a row that already exists are enough.
