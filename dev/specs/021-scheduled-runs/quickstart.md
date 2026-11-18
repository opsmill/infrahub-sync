# Validation guide: Scheduled runs that write without a person present

How to show that the feature works. The routes, commands and events are in
[contracts/](contracts/), and the records in [data-model.md](data-model.md).

## 1. Offline unit tier (every pull request)

```bash
uv sync --extra dev --extra prefect --extra service
uv run invoke format
uv run invoke lint
uv run ruff check .
uv run invoke tests.tests-unit
```

The unit tier uses the SQLite product store, a fake orchestration and times that the test
passes. It needs no Infrahub and no Prefect server, so it does not show that Prefect creates the
flow runs: sections 3 and 4 do. It shows:

| What | Where |
| --- | --- |
| A cadence is validated and its next start computed by Prefect, including a daylight-saving change | `tests/service/test_schedule_cadence.py` |
| The Prefect schedule of a schedule is created, updated, deactivated and deleted, and a failed call is repaired by the next match | `tests/service/test_schedule_prefect.py` |
| Schedule routes, refusals, owner and administrator rules, idempotency | `tests/service/test_schedule_routes.py` |
| The admission: a scheduled flow run creates one run, a second flow run of the same start creates none, `reject` skips, a late start is recorded as missed | `tests/service/test_schedule_admission.py` |
| An old unresolved receipt does not block a schedule; a flow run with `schedule_id` that Prefect's scheduler did not create is refused | `tests/service/test_schedule_admission.py` |
| The owner's actor is removed: the schedule stops, and a resume succeeds when the actor exists again | `tests/service/test_schedule_owner.py` |
| Branch review is refused for a version that declares a branch; `verify`, `apply` and `resume` are refused on a run with branch review | `tests/service/test_schedule_branch_review.py` |
| The store records of a run's branch from a worker result that the test writes: `pending`, `open`, `removed`, `replaced`, `kept` | `tests/service/test_schedule_branch_review.py` |
| The approval setting and the two write targets | `tests/service/test_schedule_approval.py` |
| The left-out setting reaches the `sync` stage for the default branch, and is refused for branch review | `tests/service/test_schedule_left_out.py` |
| The five branch and proposed change operations, with a fake Infrahub client | `tests/service/test_branch_review_operations.py` |
| Missed starts, the late run and the `queue` policy (user story 3) | `tests/service/test_schedule_missed_starts.py` |
| Schedule records and two admissions of one start | `tests/product_store/test_schedules.py` |
| Client methods and CLI commands, and the command, method and route matrix | `tests/client/test_schedules.py`, `tests/cli/test_schedule_commands.py`, `tests/cli/test_parity_and_closure.py` |
| The documented event types match the code | `tests/test_run_events_docs.py` |
| The CLI and the client import without Prefect | `tests/client/test_public_surface.py` |

Requirements with no acceptance scenario of their own in the specification are shown here:
FR-019 and the daylight-saving edge case in `test_schedule_missed_starts.py` and
`test_schedule_cadence.py`, FR-021 (only what the schedule created) in
`test_schedule_branch_review.py`, and FR-025 in `test_schedule_routes.py` with a canary
credential.

## 2. PostgreSQL product store (opt-in)

```bash
uv run invoke tests.tests-product-store-postgresql
```

It runs the schedule store tests against PostgreSQL, including two admissions of the same start
at the same time: one of them creates the run.

## 3. Development stack: a schedule that writes to the default branch (user story 2)

Needs the development stack and the NetBox example. The Sync API must not require approval.

```bash
uv run invoke dev.start
infrahub-sync schedules create --name smoke-every-minute --config from-netbox --version 1 \
  --cron "* * * * *" --write-to-default-branch
infrahub-sync schedules show smoke-every-minute
```

Expected:

- The answer of `create` holds `next_start_at`, less than one minute ahead.
- After that time, `infrahub-sync runs list --schedule smoke-every-minute` shows one run with the
  trigger type `scheduled`.
- While that run is in progress, the next start is skipped:
  `infrahub-sync events --schedule smoke-every-minute` shows
  `infrahub_sync.schedule.start_skipped` with the identifier of the run in progress.
- `infrahub-sync schedules pause smoke-every-minute` stops new runs.
  `infrahub-sync schedules delete smoke-every-minute --yes` ends the test.

Then restart the Sync API with `INFRAHUB_SYNC_REQUIRE_APPROVAL=true` and repeat the `create`
command: it is refused with `approval-required-for-schedule`.

## 4. Development stack: a schedule with branch review (user story 1)

This is the first delivery. The token of the worker needs the permissions of
[contracts/branch-review.md](contracts/branch-review.md).

```bash
infrahub-sync schedules create --name smoke-review --config from-netbox --version 1 \
  --cron "*/5 * * * *"
```

Expected, with a source that changes between two starts:

- After the first start, Infrahub holds a branch `sync/smoke-review-<date>-<suffix>` and an open
  proposed change. `schedules show` holds both in `open_branch`.
- After the second start, the first proposed change is closed with a comment that names the
  second, and its branch no longer exists.
- Approve the second proposed change in Infrahub and wait for the third start: no run starts,
  and the events hold `infrahub_sync.schedule.start_skipped` with
  `skipped-approved-change-open`. Merge it and wait for the next start: a run starts.
- With one source record that has a problem, the run writes the other records to its branch,
  and the description of the proposed change states that one record was left out.
- With a source that does not change, a start ends with the outcome `no-change`, and Infrahub
  holds no branch of that run when it ends.
- Stop the worker during a write, then let the next start pass: the branch of the interrupted run
  no longer exists after the next successful run.
- With `INFRAHUB_SYNC_REQUIRE_APPROVAL=true`, the same schedule still runs.

## 5. Missed starts (user story 3)

On the development stack:

1. Stop the `sync-worker` service before a start time and start it again 7 minutes after. A run
   up to 5 minutes late counts as on time and records no missed start, so the delay must be
   longer than that. Use a schedule that starts every 10 minutes, so that the next start does
   not pass during the stop. Expected: one run starts, and the events hold `start_missed` with
   `missed-started-late`.
2. With a schedule that starts every 5 minutes, set `--start-late-by-at-most 1m`, stop the
   worker before a start time and start it again 3 minutes after. Expected: no run, and `start_missed` with `missed-skipped`.
3. Stop the `sync-api` service across a start time. Expected: the run starts on time.

## 6. Documentation

```bash
uv run invoke docs.generate
uv run invoke docs.docusaurus
```

`docs/docs/reference/cli.mdx` holds the `schedules` commands, and the pages that said there is no
built-in schedule describe the schedule.
