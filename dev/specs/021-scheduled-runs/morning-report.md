# Morning report: spec 021, scheduled runs, night of 2026-11-18

Everything is on branch `021-scheduled-runs` in the worktree `.worktrees/021-scheduled-runs`. Nothing
is pushed and no pull request is open. The detailed log is [overnight-log.md](overnight-log.md); the
implementation report is [opsmill-implement-report.md](opsmill-implement-report.md).

## 1. Done

- **Rebase onto `feature/v3-develop`.** 49 commits replayed with their original committer and dates.
  Conflicts in the product store schema, the bootstrap job, `serve.py`, two test fakes and the
  export lists; both sides kept, and the bootstrap record stays the last bootstrap step. Fix-up
  commit `75f6d4af`.
- **The three next steps of the implementation report**, on an isolated development stack of this
  branch (Compose project `infrahub-sync-021`, Infrahub 1.11.9):
    - The integration test of the five branch and proposed change operations passed against
      Infrahub (`tests/integration/test_branch_review_operations_integration.py`).
    - Both smoke tests passed (`tests/dev_stack/smoke/test_scheduled_run.py`): branch review opens
      and replaces proposed changes; the default branch starts on time and stops when approval is
      required.
    - Both write targets are turned on (T058, T041), with the CLI options `--write-to-default-branch`
      and `--write-to-branch-review`. All 63 tasks are ticked.
- **Two defects found by running the feature, and fixed:**
    - With SQLAlchemy 2.1.6, Prefect 3.8.6's scheduler created no flow run for any schedule.
      SQLAlchemy is held below 2.1 (`60ae14d4`). The lock moved only SQLAlchemy (2.1.6 to 2.0.55).
    - The first start after a schedule was created, changed or resumed could get no flow run,
      because Prefect creates flow runs only from the current time and deletes future ones on a
      change. The Sync API now asks Prefect to create the flow runs right after it changes a Prefect
      schedule (`0e701add`, `681bb9e3`). Prefect still creates every flow run.
- **Frontend types** regenerated (T054, `aa430587`).
- **Documentation** updated for both write targets, the starts that get no flow run, and the
  Kubernetes gap (`b6197ba4`, `7374b30f`, `5999b819`, `0f91ed11`).
- **A second review** of everything not yet reviewed (the fix pass, the rebase and tonight's
  commits) found no defect on Compose. Its one critical finding is about the Helm chart (Blocked,
  item 2).
- **Final checks on the final tree:**
    - lint and `ruff`: pass
    - unit tier: `9631 passed, 4 skipped`
    - PostgreSQL tier: `287 passed`
    - Python 3.10 legs: pass
    - release-pin test: pass
    - integration test and both smoke tests: pass, at 18:34 UTC
    - documentation site: builds

## 2. Blocked: questions that need your answer

1. **A late run after a full restart fails when the destination is not ready yet.** After the stack
   was stopped and started again, the late run started but failed, because Infrahub was not
   answering yet, and a schedule does not run a failed start again. Recommended: let a scheduled
   run that is late, and fails because the destination cannot be reached, be retried a few times
   within its late-start duration. This is a product change; it is not implemented.
2. **The Helm chart does not pass `INFRAHUB_SYNC_REQUIRE_APPROVAL` to the worker** (repository
   `infrahub-helm-sync-chart`, branch `feat/infrahub-sync-chart`, `templates/api.yaml` only). On
   Kubernetes, the worker's second approval check does not run, so a default-branch schedule that
   the Sync API could not stop at startup would keep writing. Recommended: add the variable to the
   worker template in the chart before the chart is released. Not edited: it is another repository.
   The Kubernetes page states the gap.
3. **Where the SQLAlchemy pin lands.** The pin is on this branch. Other branches in flight will
   conflict on `uv.lock`. Recommended: land the pin on `feature/v3-develop` as its own small change
   first, then rebase this branch on it.
4. **Approval of the constitution amendment** (principle I, version 1.1.0, on this branch). The
   constitution asks a maintainer to approve an amendment. Recommended: approve it in the pull
   request of this branch.
5. **Push and pull request.** Not done, as instructed. Recommended: open the pull request into
   `feature/v3-develop` after you review this report.

## 3. Advisor decisions

From [overnight-log.md](overnight-log.md), section "Advisor questions":

1. **SQLAlchemy pin and the missed first start.**
    - Keep the pin, after checking that the lock moved only SQLAlchemy and running the Python 3.10
      legs, the PostgreSQL tier and the release-pin test (all passed).
    - Investigate the missed first start that night instead of reporting it. Done: the cause was
      found and fixed.
    - The Helm chart gap does not block T041: document it, and put the chart change under Blocked.
2. **Development stack, quickstart and review.**
    - Do not make the development worker wait for Infrahub: it would hide a product question
      (Blocked, item 1).
    - Fix quickstart section 5 to use a delay longer than the 5-minute on-time window (done).
    - Run one read-only review of everything not yet reviewed before the report (done; no
      defect on Compose).

## 4. Not done

- The four low findings of the second review:
    - The worker's approval stop does not check the write target again under the row lock.
    - The worker does not validate the approval setting when it starts.
    - After a failed store write, the immediate flow-run creation is skipped until Prefect's next loop.
    - A delete of one Prefect schedule also deletes the late flow runs of the others.
- The medium and low findings of the first review that the implementation report lists as
  deferred. The main ones:
    - A person's `sync` can start between the "run in progress" check and the admission.
    - The test fakes accept requests that Infrahub refuses.
- The Compose test tier (`pytest -m compose tests/compose`) was not run.
- Not observed on the stack:
    - a worker account with only the "Infrahub Users" permissions (everything ran as admin)
    - an approval or a merge by a second Infrahub account
    - the `queue` policy re-run by a real worker
    - a worker stopped during a write
    - a Prefect server stopped for longer than the flow runs it had created ahead

## 5. Risks to check before you accept the work

- **Nothing was reviewed by a person.** The code was reviewed only by automated reviewers.
- **The work exists only in this worktree.** It is not pushed.
- **Two running Docker resources belong to this work. Remove them when you no longer need them:**
    - the stack:
      `COMPOSE_PROJECT_NAME=infrahub-sync-021 docker compose -f development/docker-compose.yml --profile sync --profile infrahub down -v`
      (run in the worktree)
    - the test database: `docker rm -f sync021-pg`
- **The stack holds three test configurations** that the Sync API cannot remove. They go with the
  stack's volumes.
- **The SQLAlchemy pin changes the dependencies of the `prefect` and `service` extras.** The defect
  it fixes was seen on this stack only; no deployment of `feature/v3-develop` has a schedule.
- **A run up to 5 minutes late counts as on time** and records no missed start. FR-018 does not
  state that window; the user guide does.
- **The documentation says the bootstrap job keeps the Prefect schedules.** It does, because each
  has a slug. The earlier statement that it removes them was wrong and is corrected; the match step
  stays as a repair.
- **Commit trailers changed with the model.** Commits made before the model change carry
  `Co-Authored-By: Claude Fable 5.2`, later ones `Claude Opus 5.5`. None carries a `Claude-Session`
  line.
