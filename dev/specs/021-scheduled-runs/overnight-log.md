# Overnight log: spec 021, 2026-11-18 to 2026-11-19

Unattended run. The work stays on branch `021-scheduled-runs` in the worktree
`.worktrees/021-scheduled-runs`. Nothing is pushed, no pull request is opened, no branch or data
that this work did not create is deleted, and no shared configuration is changed.

## Limits applied

The instructions left the list of limits as a template. These are applied:

- No push to any remote, and no pull request.
- No deletion of branches, volumes, containers or data that this work did not create.
- No change to the containers, volumes or images of other sessions: the projects
  `infrahub-sync-dev`, `infrahub-sync-preview*`, `infrahub-sync-netbox`, `infrahub-sync-022*`, the
  `hivemind*` containers, and the image tag `infrahub-sync:dev`.
- No edit of another repository, such as the Helm chart repository.
- No message to anyone outside this session, except short replies to other sessions that ask
  which Docker resources this work uses.

Resources this work created and owns: container `sync021-pg` (127.0.0.1:55432), Compose project
`infrahub-sync-021` (ports 18000, 18001, 14230, 15440, 19010, volumes `infrahub-sync-021_*`), image
tag `infrahub-sync:021`.

## Advisor questions

Each entry: the question, the answer, and what was done next. The advisor is the `advisor` tool of
this session.

## Events

- Rebase onto `origin/feature/v3-develop` done before this log started: 49 commits, conflicts in
  `store.py` (schema), `bootstrap.py` and `serve.py` (start check and schedules), two test fakes, and
  the export lists. Both sides kept; the bootstrap record stays the last bootstrap step.
- After the rebase: Pylint `too-many-statements` in `bootstrap._converge` (schedule match moved into
  `_restore_schedules`), and two test fakes updated for the new `start_check` and `schedules`
  arguments.
- Found: the Helm chart (repository `infrahub-helm-sync-chart`, branch `feat/infrahub-sync-chart`)
  passes `INFRAHUB_SYNC_REQUIRE_APPROVAL` only to the Sync API, not to the worker. The worker's
  approval check does not work on Kubernetes until the chart passes it. Not edited (another
  repository).
- Rebase fixes committed as `75f6d4af`. On that tree: `invoke lint` passes, unit tier
  `9618 passed, 4 skipped`, PostgreSQL tier `287 passed` (run before the last two test-fake fixes,
  which touch no store code).
- Started the stack work (integration test, smoke tests, T032, T058, T055, T041, T054) in one
  agent on the isolated stack `infrahub-sync-021`.
- Stack work done (commits `60ae14d4` to `1e9e1b9b`): integration test passed against Infrahub
  1.11.9; both smoke tests passed; T032, T041, T054, T055, T058 ticked. The smoke test found that the
  Prefect scheduler created no flow run with SQLAlchemy 2.1.6; fixed by holding SQLAlchemy below 2.1.
  Open: a schedule created at 16:28:30 got no run for 16:29.

### Advisor question 1 (about 17:00)

- **Question**: two decisions after the stack work. (a) Keep the SQLAlchemy pin `<2.1` in the
  `prefect` and `service` extras, or revert it? Options: keep it; revert it and report the scheduler
  defect. Recommended: keep it, because without it no schedule starts a run. (b) The first start
  after a schedule is created can be missed. Options: report it as a finding; investigate tonight.
  Recommended: report it.
- **Answer**: keep the pin, but check that the lock moved only SQLAlchemy, and run the Python 3.10
  legs, the PostgreSQL tier and `tests/test_release_pin.py`; report the base-branch claim only as
  observed; list the pin under Risks and recommend it lands on `feature/v3-develop` on its own.
  Investigate the missed start tonight: it may come from Prefect generating runs only after the
  last create, change, resume or bootstrap of a schedule, which would also lose the late run that
  the user chose (FR-017). Run quickstart section 5 and the maintenance case (stack down without
  `-v` before a start, up a few minutes after). Fix if small, with a test; if the fix changes who
  creates the flow run, stop, put it under Blocked with a recommendation, and document the limit.
  Also: the Helm chart gap does not block T041; state it on the Kubernetes operator page and put
  the chart change under Blocked.
- **Next**: following the answer, in that order.
