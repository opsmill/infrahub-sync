# Verification of spec 029

## T022: User Story 1 gate (2026-11-23)

Python 3.11, with the dev, prefect and service extras.

| Command | Result |
| --- | --- |
| `uv run pytest tests/service tests/client -q` | 3364 passed, 32 skipped, at 2026-11-23T22:00:52Z |
| `uv run pytest tests/dev_stack -m "not smoke" -q` | 297 passed, 1 skipped, 26 deselected |
| `uv run invoke format` | No finding left |
| `uv run invoke lint` | Exit 0. ty reports 31 `unused-ignore-comment` warnings, all in files this spec does not change |
| `uv run ruff check .` | All checks passed |

The two tests that need real services ran separately:

- `tests/service/test_health_postgresql.py`: 3 passed at 2026-11-23T22:03:55Z, with
  `PRODUCT_STORE_TEST_POSTGRESQL_DSN` set to the development stack's database on
  `127.0.0.1:5440`. Without that variable, the file is skipped.
- `tests/dev_stack/smoke/test_health.py`: 1 passed at 2026-11-23T21:50:21Z. The running
  development stack was started from another worktree, and its `sync-api` has no `/api/health`
  route yet. To test this branch's route, the Sync API of this worktree ran on the host, on port
  8011, with the environment of `dev.api` (`tasks.dev.host_environment`). That run used the stack's
  PostgreSQL, object store, Prefect server and worker. A pytest plugin pointed the suite's Sync API
  address at port 8011. On a stack started from this branch, the command is
  `uv run pytest tests/dev_stack/smoke/test_health.py -m smoke`.

The offline unit tier (`uv run invoke tests.tests-unit`) then passed, 11235 passed and 12 skipped at 2026-11-23T22:18:12Z. Its first run found five failures that the new
Compose variables caused:

- `infrahub_sync/testcontainers/docker-compose.yml` must be a byte-for-byte copy of
  `docker-compose.yml`. The root file was copied over it.
- `docs/docs/compose-deployment.mdx` must name every variable that `docker-compose.yml` reads. The
  two health settings were added to its table of settings and to its list of start-up refusals.
  T044 rewrites the status section of that page.

## T028: User Story 2 gate (2026-11-23)

- `uv run pytest tests/service/test_worker_alive.py tests/compose -q -m "not compose"`: 426 passed,
  6 skipped and 64 deselected at 2026-11-23T22:24:43Z. The 20 new tests are in
  `tests/service/test_worker_alive.py` (16) and `tests/compose/test_worker_healthcheck.py` (4,
  which read the Compose files only).
- `uv run invoke lint` exited 0. ty reports 31 diagnostics; the baseline before this change was not
  measured.
  `uv run ruff check .` passed.
- The offline unit tier (`uv run invoke tests.tests-unit`) passed, 11255 passed and 12 skipped at
  2026-11-23T22:31:43Z.
- `docker compose -f development/docker-compose.yml --profile sync config --format json` renders
  the `sync-worker` healthcheck command unchanged, equal to `WORKER_HEALTHCHECK_COMMAND`: the
  command contains no `$` for Compose to interpolate.
- Follow-up: the two passing boundary cases (29 s and 59 s old) first wrote `int(time.time())`,
  which could fail when the fraction of the second plus the check's start-up time passed one
  second. They now write `math.ceil(time.time())`; 200 runs of the 29 s case all passed.
  `test_main_runs_the_worker_inside_the_sigterm_handler` now checks that `main` starts the writer,
  with `TMPDIR` in a temporary directory. The same command then gave 432 passed, 6 skipped and 64
  deselected at 2026-11-23T22:34:36Z.
- Not run: the container scenarios of quickstart scenario 2 (`docker compose ps` shows
  `sync-worker` `unhealthy` after `docker compose pause sync-worker`) and scenario 4 (the worker
  stays `healthy` while `postgres` is stopped). The running development stack belongs to another
  worktree and session, so it was not restarted with this branch's Compose file. No
  `compose`-marked test ran either; on a machine where no other stack runs, the command is
  `uv run pytest tests/compose -m compose`. Only the development Compose file was rendered with
  `docker compose config`; the root file needs its required passwords set to render.

## T042: User Story 3 gate, web interface (2026-11-23)

- `frontend/src/api/types.generated.ts` was regenerated with `openapi-typescript` (the tool that
  `pnpm codegen:openapi` runs) from the OpenAPI document of `create_app(...)` built from this
  branch's code, written to a file with `app.openapi()`. No running Sync API was used. The
  difference to the previous file is limited to `/status` becoming `/health`, the removal of
  `ServiceStatusResource` and `WorkerStatusResource`, and the addition of `HealthResource`,
  `DependencyHealthResource`, `WorkersHealthResource` and `WorkerHealthResource`. No test or CI
  job compares the generated types with the backend schema.
- `cd frontend && pnpm test`: 15 files, 419 tests passed at 2026-11-23T22:55:44Z (the baseline
  was 13 files and 410 tests; the new files are `src/domain/worker-state.test.ts` and
  `src/api/health-query.test.ts`).
- `pnpm lint` and `pnpm check` (Biome): 125 files checked, no findings.
- `pnpm build` failed before and after this change with `Can't resolve '@infrahub/ui/styles.css'`:
  the `frontend/vendor/infrahub` submodule is not initialized in this worktree. `pnpm typecheck`
  fails for the same reason; it reports no error in a line this change touched.
- `uv run pytest tests -q -m "not integration and not smoke and not docker and not compose"`:
  11264 passed, 12 skipped, 629 deselected, 2 xfailed, started at 2026-11-23T22:56:07Z.
- `uv run invoke lint` exited 0; its 31 ty warnings are unused `ty: ignore` comments in files
  this change does not touch. `uv run ruff check .` passed.
- Not run: the web interface in a browser against a Sync API built from this branch. The running
  development stack belongs to another worktree and session.
- After initializing the `frontend/vendor/infrahub` submodule in this worktree (`git submodule update --init frontend/vendor/infrahub`), `pnpm typecheck` reported no error and `pnpm build` finished (`✓ built in 415ms`) at 2026-11-23T23:04:52Z.

## T043–T050: documentation, decision record and reader grep (2026-11-24)

- Documentation timings are the design values of spec.md (SC-001 to SC-003, heartbeat interval
  10 seconds), labeled as design targets. T051 (quickstart on the development stack) is still
  open, so no timing in the documentation is measured.
- `uv run rumdl check` on every changed Markdown and MDX file: no issues.
- Vale 3.20.0 (`.vale.ini`, the same check as the `workflow-linter.yml` documentation job) on the
  changed pages: no finding in a changed line. `blackbox` and `Kuma` were added to
  `.vale/styles/spelling-exceptions.txt`; prose uses "health check" in place of "healthcheck".
  The remaining findings are `Infrahub.sentence-case` warnings on headings this change did not
  touch.
- `uv run invoke docs.generate`: no change to the generated CLI reference.
- `cd docs && pnpm install --frozen-lockfile`, then `uv run invoke docs.docusaurus`:
  `[SUCCESS] Generated static files in "build".`, no broken link or anchor reported.
- T050 reader grep (`grep -rnE --exclude-dir=__pycache__ "get_status|/status\b|api/status|statusQuery|ServiceStatusResource|WorkerStatusResource" infrahub_sync frontend/src tasks tests docs/docs development docker-compose.yml README.md`).
  Remaining matches and why each is allowed:
  - `tests/vendored_prefect_extras/test_executors.py:201`: "the run/submit/result/status
    contract" of the vendored Prefect executors; not the Sync route.
  - `tests/service/test_web_site.py:457-458`: the test that `GET /api/status` answers `404`.
  - `tests/compose/test_documentation.py:425` and `:432`: the test that no page under
    `docs/docs` outside `release-notes` names `/api/status`.
  - Fixed during T050: `tests/service/test_web_site.py:272` used `/status` as an example of a
    path outside `/api`; it now uses `/health`. The two documentation sentences that said the
    route is removed were dropped; the changelog fragment states it.
- Outside the grep scope, `dev/adr/0043-…md` names `/api/status` in its unchanged body; ADR 0055
  amends it, and its status line now says so. `.agents/skills/speckit-opsmill-qa/SKILL.md` and
  `.specify/extensions/opsmill/commands/qa.md` use `/api/status` in a generic example of another
  application, not the Sync API.

## T052: full gate (2026-11-24)

- `uv run invoke format`: exit 0 (ruff reformatted `tests/compose/test_documentation.py`).
- `uv run invoke lint`: exit 0; its 31 ty diagnostics are unused `ty: ignore` comments in files
  this change does not touch, as recorded at T042.
- `uv run ruff check .`: all checks passed. `uv run ty check .`: exit 0 (same 31 diagnostics).
- `uv run invoke tests.tests-unit`: 11271 passed, 12 skipped, 629 deselected, 2 xfailed, started
  at 2026-11-23T23:11:09Z (seven more than at T042: the new documentation tests).
- `uv run invoke docs.rumdl`: no issues in 184 files.
- `uv run infrahub-sync --help`, `configs --help`, `runs --help`: exit 0.
- `cd frontend && pnpm test`: 15 files, 419 tests passed. `pnpm lint`: 126 files, no findings.
  `pnpm typecheck`: exit 0. `pnpm build`: `✓ built in 1.14s`.
- `uv run invoke tests.check-310`: exit 0, "check-310 passed all three Python 3.10 legs"
  (Python 3.10 dev+prefect: 7978 passed, 100 skipped; Python 3.10 base install: 7714 passed,
  108 skipped), finished at 2026-11-23T23:25:33Z.
- After the review fixes to ADR 0055 and the reference page, the changed tests were re-run with
  `-v` at 2026-11-23T23:25:52Z: 14 passed, and `tests/compose/test_documentation.py` 150 passed.
- T051 (quickstart scenarios 1–9 and measured timings on the development stack) is not run.

## T051: quickstart scenarios on an isolated Compose deployment (2026-11-23)

The development stack on this machine belongs to another worktree, so the scenarios ran on the root `docker-compose.yml` under its own project (`sync029-qs`, API on port 8029), with an image built from this branch (`infrahub-sync:compose-test-029`). One PostgreSQL holds both databases, as in the development stack. The project and its volumes were removed afterwards. Results, as recorded by the run script:

Deployment `sync029-qs` up at 2026-11-23T23:27:03Z

### Quickstart run 2026-11-23T23:27:03Z (project sync029-qs, image infrahub-sync:compose-test-029)

#### Scenario 1: all up
- 200 with a live worker: 0.1 s — `200 database=up/none object_store=up/none task_manager=up/none task_manager_db=up/none workers=known live=1 items=[live]`
#### Scenario 6: headers and private values
- Cache-Control: `no-store`; private words found: none
#### Scenario 8: /status removed, /api redirect
- /api/status → 404; /api → 307 /api/health
#### Scenario 7: 600 requests, 50 at a time
- {200: 600} in 12.9 s
#### Scenario 2: worker paused, then resumed
- 503 after pause: 22.3 s — `503 database=up/none object_store=up/none task_manager=up/none task_manager_db=up/none workers=known live=0 items=[stale]`
- sync-worker container while paused (after ≥35 s more): `paused/`
- 200 after unpause: 1.1 s — `200 database=up/none object_store=up/none task_manager=up/none task_manager_db=up/none workers=known live=1 items=[live]`
#### Scenario 3: worker restart leaves a stale record
- 200 with a stale record: 29.7 s — `200 database=up/none object_store=up/none task_manager=up/none task_manager_db=up/none workers=known live=1 items=[live,stale]`
#### Scenario 4: object-store stopped, then started
- 503 with object_store down: 1.1 s — `503 database=up/none object_store=down/name_resolution_failed task_manager=up/none task_manager_db=up/none workers=known live=1 items=[live,stale]`
- 200 after object-store started: 1.1 s — `200 database=up/none object_store=up/none task_manager=up/none task_manager_db=up/none workers=known live=1 items=[live,stale]`
#### Scenario 4: prefect-server stopped, then started
- 503 with task_manager down: 1.6 s — `503 database=up/none object_store=up/none task_manager=down/name_resolution_failed task_manager_db=down/name_resolution_failed workers=unknown live=None items=[]`
- 200 after prefect-server started: 2.6 s — `200 database=up/none object_store=up/none task_manager=up/none task_manager_db=up/none workers=known live=1 items=[live,stale]`
#### Scenario 4: postgres stopped, then started
- 503 with database down: 0.1 s — `503 database=down/name_resolution_failed object_store=up/none task_manager=up/none task_manager_db=down/unavailable workers=unknown live=None items=[]`
- after 40 s more: `503 database=down/name_resolution_failed object_store=up/none task_manager=up/none task_manager_db=down/unavailable workers=unknown live=None items=[]`; sync-worker container `running/healthy`
- 200 after postgres started: 8.5 s — `200 database=up/none object_store=up/none task_manager=up/none task_manager_db=up/none workers=known live=1 items=[live,stale]`
#### Scenario 4b: postgres paused
- 503 with database down (expect timeout): 4.0 s — `503 database=down/timeout object_store=up/none task_manager=up/none task_manager_db=down/timeout workers=unknown live=None items=[]`
- 200 after postgres unpaused: 1.1 s — `200 database=up/none object_store=up/none task_manager=up/none task_manager_db=up/none workers=known live=1 items=[live,stale]`
#### Scenario 5: minimum of two workers

### Continued run 2026-11-23T23:30:41Z (sync-api already recreated with minimum 2; first `up --wait` failed once)

#### Scenario 5: minimum of two workers
- 503 with minimum 2: 0.1 s — `503 database=up/none object_store=up/none task_manager=up/none task_manager_db=up/none workers=known live=1 items=[live,stale]`
- 200 with two live workers: 0.1 s — `200 database=up/none object_store=up/none task_manager=up/none task_manager_db=up/none workers=known live=2 items=[live,live,stale]`
#### Scenario 2b: worker process stopped with SIGSTOP (US2)
- SIGSTOP exit 2 sh: 1: pgrep: not found
sh: 1: kill: Usage: kill [-s sigspec | -signum | -sigspec] [pid | job]... or
kill -l [exitstatus]
- container `running/healthy` after 151 s
- /api/health then: `200 database=up/none object_store=up/none task_manager=up/none task_manager_db=up/none workers=known live=1 items=[live,stale,stale]`
- SIGCONT exit 2
- container `running/healthy` 0 s after SIGCONT
- 200 after SIGCONT: 0.0 s — `200 database=up/none object_store=up/none task_manager=up/none task_manager_db=up/none workers=known live=1 items=[live,stale,stale]`
#### Scenario 9: timeout setting out of range
- sync-api `restarting/`; log names the variable: True

Run ended 2026-11-23T23:34:17Z

#### Scenario 2b: worker process stopped with SIGSTOP (US2), 2026-11-23T23:36:45Z
- The image has no pgrep or kill; the signal was sent with `docker exec sync-worker python -c 'os.kill(6, SIGSTOP)'` (PID 6 is `python -m infrahub_sync.service.worker` under tini).
- Container `unhealthy` after about 50 s; /api/health `unhealthy`, live_workers 0, items all `stale`.
- After SIGCONT, container `healthy` after about 10 s.
- A paused container (scenario 2) showed an empty health status in `docker compose ps` on this Docker Desktop, so pausing does not test US2 here; SIGSTOP does.

After the review fixes (commits 0a99c3e2, b97c6acd) the image was rebuilt: `workers.items[].name` showed `infrahub-sync-service-<uuid>` names, `HEAD /api/health` answered 200 with `Cache-Control: no-store` and an empty body, and `sync-worker` was `healthy`.

Differences from the spec, now in the deployment guide: a paused container shows no health status, because Docker does not run health checks in a paused container (a hung process, simulated with SIGSTOP, turns it `unhealthy` after about 50 s); with PostgreSQL stopped, `workers.state` is `unknown` at once instead of `stale` after 30 s, because Prefect cannot read the work pool.

The first `docker compose up -d --wait sync-api` with `INFRAHUB_SYNC_HEALTH_MIN_WORKERS=2` failed once and succeeded when repeated.

### The `compose` test suite

`INFRAHUB_SYNC_COMPOSE_PROJECT_PREFIX=sync029 INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test-029 uv run --extra testcontainers pytest -m compose tests/compose` (started 2026-11-23T23:06:39Z): 62 passed, 2 failed. Both failures were `configuration-name-in-use` (409): `test_a_plan_apply_and_separate_sync_run_through_the_replacement_worker` and `test_a_registered_plan_runs_through_the_deployed_worker` register `compose-suite-registered` in the session-wide deployment after another test registered it. Run alone, the two tests passed (2 passed). This branch does not change configuration registration.
