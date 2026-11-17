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

The unit tier uses the SQLite product store, a fake orchestration and a clock that the test
advances. It needs no Infrahub and no Prefect server. It shows:

| What | Where |
| --- | --- |
| Start times from a cron expression and a time zone, including a daylight-saving change | `tests/service/test_schedule_cadence.py` |
| Schedule routes, refusals, owner and administrator rules, idempotency | `tests/service/test_schedule_routes.py` |
| The loop: a start creates one run, a repeated pass creates no second run, `reject` skips | `tests/service/test_schedule_loop.py` |
| A run that could not be handed to the task manager is retried and never recorded as missed; an old unresolved receipt does not block a schedule | `tests/service/test_schedule_loop.py` |
| A change of the schedule or a take-over between two attempts of one start creates no second run | `tests/service/test_schedule_loop.py` |
| The owner's actor is removed: the schedule stops, and a resume succeeds when the actor exists again | `tests/service/test_schedule_owner.py` |
| Branch review is refused for a version that declares a branch; `verify`, `apply` and `resume` are refused on a run with branch review | `tests/service/test_schedule_branch_review.py` |
| The store records of a run's branch from a worker result that the test writes: `pending`, `open`, `removed`, `replaced`, `kept` | `tests/service/test_schedule_branch_review.py` |
| The approval setting and the two write targets | `tests/service/test_schedule_approval.py` |
| The left-out setting reaches the `sync` stage | `tests/service/test_schedule_left_out.py` |
| Missed starts, the late run and the `queue` policy (user story 3) | `tests/service/test_schedule_missed_starts.py` |
| Schedule records and the compare-and-set advance | `tests/product_store/test_schedules.py` |
| Client methods and CLI commands, and the command, method and route matrix | `tests/client/test_schedules.py`, `tests/cli/test_schedule_commands.py`, `tests/cli/test_parity_and_closure.py` |
| The documented event types match the code | `tests/test_run_events_docs.py` |
| The CLI and the client import without `cronsim` | `tests/client/test_public_surface.py` |

Requirements with no acceptance scenario of their own in the specification are shown here:
FR-019 and the daylight-saving edge case in `test_schedule_missed_starts.py` and
`test_schedule_cadence.py`, FR-021 (only what the schedule created) in
`test_schedule_branch_review.py`, and FR-025 in `test_schedule_routes.py` with a canary
credential.

## 2. PostgreSQL product store (opt-in)

```bash
uv run invoke tests.tests-product-store-postgresql
```

It runs the schedule store tests against PostgreSQL, including two loops that advance the same
schedule at the same time: one of them records the start.

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

**Blocked until the prerequisite of the specification exists.** When it does:

```bash
infrahub-sync schedules create --name smoke-review --config from-netbox --version 1 \
  --cron "*/5 * * * *"
```

Expected, with a source that changes between two starts:

- After the first start, Infrahub holds a branch `sync/smoke-review-<date>-<suffix>` and an open
  proposed change. `schedules show` holds both in `open_branch`.
- After the second start, the first proposed change is closed with a comment that names the
  second, and its branch no longer exists.
- Approve the second proposed change in Infrahub and wait for the third start: the second stays
  open, and the events hold `infrahub_sync.schedule.proposed_change_kept`.
- With a source that does not change, a start ends with the outcome `no-change`, and Infrahub
  holds no branch of that run when it ends.
- Stop the worker during a write, then let the next start pass: the branch of the interrupted run
  no longer exists after the next successful run.
- With `INFRAHUB_SYNC_REQUIRE_APPROVAL=true`, the same schedule still runs.

## 5. Missed starts (user story 3)

On the development stack, with a schedule that starts every 5 minutes:

1. Stop the `sync-api` service before a start time and start it again 2 minutes after.
   Expected: one run starts, and the events hold `start_missed` with `missed-started-late`.
2. Set `--start-late-by-at-most 1m`, stop the service before a start time and start it again
   3 minutes after. Expected: no run, and `start_missed` with `missed-skipped`.

## 6. Documentation

```bash
uv run invoke docs.generate
uv run invoke docs.docusaurus
```

`docs/docs/reference/cli.mdx` holds the `schedules` commands, and the pages that said there is no
built-in schedule describe the schedule.
