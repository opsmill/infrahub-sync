# 53. A scheduled start that waits stores its Prefect state and ends its flow run with the `Pause` signal

**Status**: Accepted
**Date**: 2026-11-18
**Source**: `dev/specs/archive/021-scheduled-runs/research.md` (R7, and the sections "Checked
since" for R7 and for R7 and FR-035), `dev/specs/archive/021-scheduled-runs/spec.md` (FR-035)

## Context

Two kinds of scheduled start must wait and be tried again later, with no run created:

- Under the overlap policy `queue`, a start waits while another run of the same configuration is
  in progress (research R7).
- A start waits while its Infrahub destination cannot be reached, answers with a server error or
  does not answer in time, for example while Infrahub starts again after a maintenance (FR-035).
  Before this, the run of such a start failed, and a schedule does not run a failed start again.

The start must keep its start time, so that the admission
([ADR 0051](0051-a-schedule-is-a-prefect-schedule-and-the-worker-admits-each-start.md)) still
applies the late-start rules and creates at most one run for it.

The first design returned `prefect.states.AwaitingRetry(scheduled_time=...)` from the flow. A test
that called the flow with `return_state=True` passed. Under a process worker on the development
stack, it did not work: the worker's child process asks the engine for the flow's result, the
engine raises `UnfinishedRun` for a state that is not final, the process ends with status 1, and
the runner marks the flow run `Crashed`. The flow run never ran again.

## Decision

- **The flow stores the state, then raises `Pause`.** `_run_again_later` (`service/flow.py`) sets
  the flow run's state to `AwaitingRetry(scheduled_time=now + 60 seconds)` through the Prefect
  client, then raises `prefect.exceptions.Pause` with the state that Prefect stored. Prefect
  documents `Pause` as raised "when a flow run is PAUSED and needs to exit for resubmission". The
  engine keeps the state that the signal carries and proposes no other state, and the child
  process ends with status 0, so the runner proposes no state either. When Prefect refuses the
  state, the flow run ends as failed.
- **Both waits use it.** The `queue` policy waits `QUEUED_START_RECHECK_SECONDS` and the
  destination wait waits `DESTINATION_RECHECK_SECONDS`, both 60 seconds.
- **What makes a start wait for its destination.** Before the admission, the worker reads the
  destination schema of the schedule's version (`_destination_not_ready`). Only the read reasons
  `timeout` and `unreachable` make the start wait. A refused token, a missing permission or a
  missing branch does not.
- **A wait is bounded by the late-start duration.** A start waits only while it can still run one
  minute later within the schedule's late-start duration. After that, the run starts and records
  the destination failure.
- **A wait creates no run and is no failure.** It does not count toward the five failed runs in a
  row that stop a schedule.

## Consequences

- Prefect submits the flow run again at the stored time. Observed on the development stack: the
  flow run was submitted every minute while Infrahub was stopped, its `run_count` grew, and
  `expected_start_time` and `auto_scheduled` kept their values. Once Infrahub answered, the start
  created one run, which succeeded.
- A waiting flow run holds no worker slot, and a restart of the worker does not end the wait.
- Prefect does not delete the waiting flow run when a schedule changes: it deletes only flow runs
  that have not run (`run_count == 0`).
- The flow run of a waiting start shows the state `AwaitingRetry` in the Prefect interface.
- The decision depends on how Prefect 3.8.6 treats `Pause` in a worker's child process. A Prefect
  upgrade must keep that behavior. `test_prefect_keeps_the_state_that_runs_a_waiting_start_again`
  checks it against a Prefect test server, and calls the flow as the worker's process does.
- The destination wait adds one schema read before each scheduled start. The smoke tests on the
  development stack still started every scheduled run within one minute of its start time.
- The `queue` policy uses the same code path, but a `queue` wait was not observed on the
  development stack itself.

## Alternatives considered

- **Return `AwaitingRetry` from the flow.** Rejected: under a process worker the flow run ends as
  `Crashed` and never runs again.
- **Wait inside the flow run.** Rejected: the process would hold a worker slot for up to the
  late-start duration, at most seven days, and a restart of the worker would end the wait.
- **Make the development worker wait for Infrahub before it starts.** Rejected: it hides the case
  of a production destination that is not ready, and a schedule does not run a failed start again.
- **Let the run of the start fail.** Rejected: FR-035 asks that a destination that is not ready yet
  creates no failed run.
