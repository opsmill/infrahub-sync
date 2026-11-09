# Feature Specification: Read state for each side of a Plan

**Feature Branch**: `018-plan-read-progress`

**Created**: 2026-11-09

**Status**: Draft

**Input**: The idea brief "Read state for each side of a Plan", grilled with the user on 2026-11-09. Every
decision in the brief was confirmed by the user. The brief is compared with this specification in
[`alignment-check.md`](./alignment-check.md).

## Background

The frontend team asked whether a run's page could show more about what happened during a Plan. Today the
page shows one block for the Plan stage. The operator cannot see when the source and the destination were read,
whether each read succeeded, or how many nodes each side returned.

A Plan reads the source and the destination in parallel, then compares them and writes the plan. The stored run
holds the stage outcome, the counts of planned operations by action and by kind, the results and the artifact
references. Nothing is stored for each side. The only trace of a read is a log line written when the read
starts. When a read fails, the stage fails with a message that names the adapter, and no stored field says which
side failed.

The `sync` stage reads both sides in the same way before it writes. The `verify` and `apply` stages read
neither side: `verify` checks the saved plan file without building adapters, and `apply` executes the saved
operations.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Read state for each side, while the stage runs and after it ends (Priority: P1)

An operator opens a running Plan on the run's page and views the source read and the destination read. Each one
is waiting, reading, succeeded or failed. When a read ends, the page shows its start and end times, whether the
read was full or incremental, and the number of nodes for each kind. All of it stays visible after the stage
ends, whether the stage succeeded or failed.

**Why this priority**: It removes both pains the frontend team reported. During a long Plan, the operator can
tell which read is still running. After a failed Plan, the operator can tell which side failed without reading
the logs.

**Independent Test**: Run a Plan against a source and a destination whose reads can be paused or made to fail,
and read the run while each read is in progress and after the stage ends.

**Acceptance Scenarios**:

1. **Given** a Plan whose source read has ended and whose destination read is still running, **When** the
   operator reads the run, **Then** the source shows "succeeded" with its start and end times, its read mode and
   its node count for each kind, and the destination shows "reading" with its start time.
2. **Given** a Plan whose destination read failed, **When** the operator reads the run, **Then** the destination
   shows "failed" with the error class, and the source shows its own state.
3. **Given** a Plan that a lineage refusal stopped before either read started, **When** the operator reads the
   run, **Then** both sides show "not started", and the run's failure cause explains why.
4. **Given** a Plan whose worker process was killed while the destination was being read, **When** the Sync API
   has marked the execution failed and the operator reads the run, **Then** the destination shows "interrupted"
   with no end time.

---

### User Story 2 - The CLI shows the same information (Priority: P2)

An operator who works from a terminal runs `infrahub-sync runs show <run_id>` and reads the same read state for
each side of the run's latest execution that read the sides, meaning its latest `plan` or `sync` execution.

**Why this priority**: Not every site uses the web UI, and the CLI is the operator's other way to read a run.
It depends on User Story 1 for the data.

**Independent Test**: Run `runs show` on a run whose Plan has ended and check the printed lines for each side.

**Acceptance Scenarios**:

1. **Given** a run whose Plan has ended, **When** the operator runs `runs show`, **Then** the output names each
   side with its state, its start and end times, its read mode and its node counts.
2. **Given** a run recorded before this feature, **When** the operator runs `runs show`, **Then** the output has
   no read-state lines and no error.
3. **Given** a run whose Plan has ended and whose plan the operator then verified, **When** the operator runs
   `runs show`, **Then** the output still shows the Plan execution's read state.

---

### User Story 3 - The same for the `sync` stage (Priority: P3)

An operator who runs a `sync` reads the same read state for each side, because the `sync` stage reads both sides
before it writes.

**Why this priority**: The `sync` stage runs the same reads as a Plan, so the operator expects the same
information. Plans are more common than `sync` runs on the frontend team's run pages.

**Independent Test**: Run a `sync` and read the run while its reads are in progress and after the stage ends.

**Acceptance Scenarios**:

1. **Given** a `sync` run whose source read has ended, **When** the operator reads the run, **Then** the source
   shows the same state, times, read mode and node counts as it would in a Plan.

---

### Edge Cases

- **The worker process is killed during a read.** Nothing writes that side's end. When the execution has a
  terminal state, the side shows "interrupted" (FR-009).
- **The Plan stops before either read.** A lineage refusal, an HTTP 401, or a transport error while the
  destination's server version is read stops the Plan before either side is read. Both sides show "not
  started", and the run's failure cause explains why.
- **One side fails while the other is still reading.** The reads run in parallel, and the other read continues
  until it ends. The run shows "failed" next to "reading" for that time. The other read is not cancelled.
- **A run has several executions,** for example after an execution expired or was refused. Each execution keeps
  its own read state, and nothing is overwritten.
- **The worker fails to record a side's end, and the stage succeeds.** A completed `plan` or `sync` execution read
  both sides successfully, so "interrupted" would be false. The run shows no read state for that side (FR-009).
- **A run holds `verify` and `apply` executions after its Plan.** These read no side. The UI and the CLI show the
  read state of the latest `plan` or `sync` execution (FR-012).
- **An execution is still in the queue.** The worker has not started the stage, so the execution has no read state
  yet.
- **An execution recorded before this feature.** It has no read state. The run response returns no read state
  for it, and the UI and the CLI show nothing for it.
- **A read is incremental.** Most nodes come from the cache. The node count is the total compared for each kind,
  and the read mode says "incremental", so a fast read with a large count is explained.
- **Recording the read state fails.** The run continues and ends as it would without this feature (FR-007).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST record, for each side of a Plan execution and of a `sync` execution, one read
  state: waiting, reading, succeeded or failed.
- **FR-002**: The system MUST record a side's start time when its read starts, and its end time when its read
  ends.
- **FR-003**: When a side's read succeeds, the system MUST record the total number of nodes compared for each
  kind, meaning the nodes held after the read, whether they came from the cache or from the system that was read.
- **FR-004**: When a side's read fails, the system MUST record the error class without the error text, and the
  other side MUST keep its own state.
- **FR-005**: While the stage runs, the run response MUST return the current read state of each side.
- **FR-006**: Users MUST be able to view the final read state of each side after the stage ends, whether the
  stage succeeded or failed.
- **FR-007**: A failure to record the read state MUST NOT change the stage's outcome.
- **FR-008**: The system MUST record each side's read mode: full or incremental.
- **FR-009**: When a stage execution has a terminal state other than completed, the run response MUST show a side
  that is still "waiting" as "not started", and a side that is still "reading" as "interrupted". Neither gets an
  end time. When the execution completed, a side that is still "waiting" or "reading" MUST be returned with no read
  state, because its end was not recorded.
- **FR-010**: When a side's read ends, the system MUST write a log line in the run's log that names the side, its
  state, its read mode and its node counts, and no error text.
- **FR-011**: The read state MUST belong to one stage execution, so that each execution of a run keeps its own.
- **FR-012**: `infrahub-sync runs show` MUST print the read state of each side of the run's latest `plan` or `sync`
  execution, when that execution has one, even when `verify` or `apply` executions follow it.
- **FR-013**: The read state MUST hold no secret, no address, no configuration content and no exception text.

### Key Entities

- **Stage execution**: the existing entry in the run's orchestration list, one for each Prefect flow run, with its
  purpose, its state and its terminal state. It gains the read state of each side.
- **Side read**: the source or the destination of one stage execution. It holds the state, the start and end
  times, the read mode, the node count for each kind, and the error class when the read failed. It is new data
  on the stage execution, not a separate entity.
- **Run**: unchanged. A run can hold `plan`, `verify` and `apply` executions in order. The UI and the CLI show the
  read state of the latest `plan` or `sync` execution by default.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Within 5 seconds of a side's read changing state, an operator who reads the run sees the new state.
- **SC-002**: For every Plan that fails during a read, the operator can tell which side failed from the run alone,
  without opening the logs.
- **SC-003**: Every side of an execution that has ended shows a final state (succeeded, failed, not started or
  interrupted) or, for a completed execution whose end was not recorded, no read state. No side shows "waiting" or
  "reading" once its execution has ended.

## Assumptions

- The run's page in the UI reads the run again every 2 to 3 seconds while a stage runs. The frontend team has not
  confirmed the interval. SC-001 allows 5 seconds so that this interval and one stored write fit inside it.
- Counting the nodes of each kind after a read costs little compared with the read itself.
- The operator reads the run through the Sync API or the CLI. Prefect stays an internal component of Sync
  ([ADR 0019](../../adr/0019-prefect-is-an-internal-component-of-sync.md)).
- Implementation starts after pull request #402 (spec 017) and the spec 016 branch are merged into
  `feature/v3-develop`, because all three change the worker's flow, the run response and the run response
  model.
- Storing the read state needs one optional field on the stored stage execution. Executions recorded before this
  feature have none. This crosses the database change gate, and the user accepted it during the grilling.
- Adding the read state to the run response is an additive API change. Clients from before this feature ignore
  the new field.

## Out of Scope

- The comparison and plan-write steps of the stage. The frontend team can ask for them later.
- The count of changed rows read from the system during an incremental read.
- Node counts while a read is in progress, kind by kind. That would need changes to every adapter.
- Pushing changes to the UI through server-sent events or a WebSocket. The UI reads the run again instead.
- The `verify` and `apply` stages, which read neither side.
- New HTTP routes.
