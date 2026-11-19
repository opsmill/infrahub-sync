# 51. A schedule's cadence is a Prefect schedule, and the worker admits each scheduled start

**Status**: Accepted
**Date**: 2026-11-18
**Source**: `dev/specs/archive/021-scheduled-runs/research.md` (R1, R2, R10, R13, and the sections
"Checked since" for R1, R2, R10 and R13)

## Context

A team wants a `sync` run to start at set times with no person present: at a given time, on given
days, or every few hours. Before this decision, every run started from a request to the Sync API,
and the worker refused a flow run that had no run record. The run record must exist before any
work of a stage.

The Sync service already runs a Prefect server with its default services, the scheduler among
them ([ADR 0019](0019-prefect-is-an-internal-component-of-sync.md)). Prefect's scheduler handles
cron expressions and time zones, pauses each schedule on its own, and shows the coming flow runs
in the Prefect interface. Infrahub defines its own system workflows with a Prefect cron schedule
in the same way.

The worker has no API token. It cannot ask the Sync API to create a run.

## Decision

- **Prefect holds the cadence.** Each Sync schedule has one Prefect schedule on the existing
  deployment `infrahub-sync-service/run`. It holds a `CronSchedule(cron, timezone)`, the
  schedule's identifier as its slug, and the flow parameters `schedule_id` and `stage = "sync"`.
  Prefect's scheduler creates the flow runs. Infrahub Sync has no scheduling loop of its own.
- **The product store is the source; one step makes Prefect match it.** The Sync API writes the
  schedule to the product store first. `match_prefect_schedules` (`service/schedules.py`) then
  creates, changes, deactivates or deletes Prefect schedules, by slug, until Prefect holds one
  schedule for each stored schedule that is not deleted, active only while the stored schedule is
  `active`. It leaves alone a Prefect schedule whose slug is not a Sync schedule identifier. The
  step runs after each schedule request, when the API process starts, and at the end of the
  bootstrap job. When a Prefect call that reads, creates, changes or deletes a schedule fails, the
  request answers `503 orchestration-unavailable` and the stored schedule keeps `prefect_synced`
  false until a later run of the step succeeds. The call that creates flow runs is the exception,
  described in the next point.
- **The step asks Prefect to create the flow runs at once.** After it creates, changes or deletes
  a Prefect schedule, the step calls `POST /deployments/{id}/schedule`
  (`PrefectOrchestration.create_scheduled_runs`). Prefect then creates the flow runs of every
  active schedule from the current time, with the same idempotency key as its scheduler. A failure
  of this call is logged and not raised.
- **The worker admits a scheduled flow run.** A flow run that carries `schedule_id` and no `run_id`
  takes a second way into `service_sync_run`. The flow refuses it unless Prefect's scheduler
  created it (`auto_scheduled`). `admit_scheduled_start` (`service/schedule_admission.py`) decides
  on the start from the stored schedule and the flow run's `expected_start_time`, and the product
  store saves the decision in one transaction:
    - for a start that does not run, the events that say why, with no run;
    - for a start that runs, the run with the schedule's owner as actor, its accepted receipt, its
      write admission and its execution, already claimed by this worker.
- **One run for each start.** A unique index on `product_runs (schedule_id, scheduled_start_at)`
  allows one run for each start of a schedule. A flow run that Prefect retries, or a second flow
  run for the same start, creates no second run.
- **Missed and late starts are decided by the worker.** When the next start is admitted, the
  admission records the starts of the cadence since the last admitted start that have no run. A
  start up to 5 minutes late (`ON_TIME_SECONDS`) is on time. The most recent start that is later
  than that but within the schedule's late-start duration runs, with a `missed-started-late`
  event. Every other late start is recorded as missed and does not run.

## Consequences

- No periodic task runs in the API process, and two API processes need no rule between them. A
  restart of the Sync API does not lose a start, because the API process takes no part in a start.
- The run record still exists before any work. The worker writes it, not the Sync API.
- The owner is a local account of the product store
  ([ADR 0049](0049-sign-in-uses-local-accounts-and-the-environment-defines-admin.md)). An
  administrator can disable or delete it while the Sync API runs, so the worker reads the owner's
  account before each admission: a start of a schedule whose owner's account is disabled or
  deleted creates no run, and the admission stops the schedule with the reason
  `owner-not-found`. The API process makes the same check when it starts and when a schedule is
  resumed.
- The bootstrap job keeps the Prefect schedules. It applies the deployment with an empty list of
  schedules, but Prefect keeps the existing schedules when each of them has a slug. The match step
  of the bootstrap job therefore repairs differences only.
- Prefect's scheduler creates flow runs only from the moment its loop runs, about once a minute,
  and a change or a deletion of one Prefect schedule deletes the future flow runs of every schedule
  of the deployment that have not run. Without the call to `POST /deployments/{id}/schedule`, a
  start within that minute gets no flow run. With it, such a start still gets no flow run when the
  call fails, and the admission records the start as missed at the next start.
- A deletion of one schedule also deletes the late flow runs of the other schedules of the
  deployment, for example while the worker is stopped. Those starts are recorded as missed at the
  next start.
- A start that passes while the Prefect server is stopped keeps its flow run, because Prefect
  creates flow runs ahead of time (at least 3 runs and at least one hour). A Prefect server stopped
  for longer than that leaves starts with no flow run; the admission records them as missed.
- Prefect's scheduler must work in the deployed image. Prefect 3.8.6 creates no flow run with
  SQLAlchemy 2.1 ([PrefectHQ/prefect#23199](https://github.com/PrefectHQ/prefect/issues/23199)),
  so `pyproject.toml` holds SQLAlchemy below 2.1 in the `prefect` and `service` extras until the
  `prefect` pin moves to 3.9.1 or later.
- The unit tier does not run Prefect's scheduler. That Prefect creates the flow runs at the right
  times is shown only on the development stack (`tests/dev_stack/smoke/test_scheduled_run.py`).

## Alternatives considered

- **A loop in the Sync API process that starts runs through `RunService`.** Rejected: it builds a
  second scheduler next to the one Prefect already runs, and needs a rule for two API processes.
- **One Prefect deployment for each Sync schedule.** Rejected: the worker admits only the
  deployment `run`, and one deployment with several schedules needs no change there.
- **The Sync API creates the run record before Prefect starts the flow run.** Rejected: the Sync
  API would need a loop, and the worker cannot call the Sync API because it has no API token.
- **Remove the Prefect schedules in the bootstrap job and create them again.** Not needed: Prefect
  keeps schedules that have a slug when the deployment is applied again.
