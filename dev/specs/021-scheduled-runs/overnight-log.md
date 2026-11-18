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
- Checks after the SQLAlchemy pin (the lock moved only SQLAlchemy, 2.1.6 to 2.0.55): release-pin
  test `38 passed`, PostgreSQL tier `287 passed`, Python 3.10 legs passed.
- Kubernetes page note on the chart and the worker's approval check (`ea0cef2c`, corrected in
  `5999b819`).
- Investigation of missed starts (commits `0e701add`, `7374b30f`, `681bb9e3`): Prefect generates
  flow runs only from the current time, and a schedule change or delete deletes the future runs of
  every schedule of the deployment. Fixed by asking Prefect to create the flow runs at once after
  the match step changes a Prefect schedule. The late run after a stack restart does start (the
  earlier bootstrap claim was wrong), but on the development stack it failed because Infrahub was
  not ready yet. Quickstart section 5 step 1 assumed a delay under the 5-minute on-time window.

### Advisor question 2 (about 18:15)

- **Question**: (a) make `sync-worker` in the development Compose file wait for a healthy
  `infrahub-server`? Options: yes; no, and report the late-run failure as a product question.
  Recommended: yes. (b) Fix quickstart section 5 to use a delay above 5 minutes? Recommended: yes.
  (c) Review tonight's changes before the report? Recommended: yes, one read-only reviewer.
- **Answer**: (a) no: it hides the problem only when Infrahub runs on the same host, and a
  `depends_on` on a profiled service breaks the `sync` preset; put "retry a late run when the
  destination is not ready" under Blocked. (b) yes, and note under Risks that FR-018 does not state
  the 5-minute on-time window. (c) yes: one read-only reviewer on the range since the last reviewed
  commit, asked about every path that leaves a default-branch schedule active while approval is
  required, what `create_scheduled_runs` creates, and whether Prefect's delete on a schedule change
  removes only auto-scheduled runs; fix critical and high findings only; if one reaches the default
  branch and is not small, take `default-branch` out of `serve.py` and untick T041. Also search for
  the old bootstrap statement in tasks, docstrings, knowledge pages and the report.
- **Next**: following the answer.
