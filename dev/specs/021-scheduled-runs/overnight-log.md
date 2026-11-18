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
