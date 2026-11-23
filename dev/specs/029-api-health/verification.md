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
