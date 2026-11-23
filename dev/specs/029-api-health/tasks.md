# Tasks: Health of the Sync API and each worker

**Input**: Design documents from `dev/specs/029-api-health/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/health-api.md, quickstart.md

**Tests**: required. The constitution (principle V) requires tests written alongside each change.
Each story lists its tests first; write them, see them fail, then implement.

**Organization**: by user story. US1 adds `/api/health` next to `/api/status`; US2 adds the worker
container healthcheck; US3 removes `/api/status` and moves its readers. US3 depends on US1. US2
is independent of both.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1, US2, US3 from spec.md

Run everything from `/Users/bkohler/automation/opsmill/infrahub-sync/.worktrees/029-api-health`.

---

## Phase 1: Setup

- [ ] T001 Install the full development profile in the worktree with `uv sync --extra dev --extra prefect --extra service`, then run `uv run invoke tests.tests-unit` and record the baseline pass count in `dev/specs/029-api-health/verification.md` (create it with a `## Baseline` heading)
- [ ] T002 [P] Install the web interface dependencies with `cd frontend && pnpm install --frozen-lockfile` and record `pnpm test` and `pnpm lint` (or the scripts `frontend/package.json` defines) baseline results in `dev/specs/029-api-health/verification.md`

---

## Phase 2: Foundational (blocks US1 and US3)

- [ ] T003 Add `name: str | None = None` as the last field of `PoolWorker` in `infrahub_sync/service/orchestration.py:155`, fill it in `_pool_worker` (`orchestration.py:714`) from the Prefect record's `name` (a non-string name becomes `None`, it does not make the record invalid), and add `worker_is_live(worker: PoolWorker, now: datetime) -> bool` in the same module: true exactly when `worker.status == "online"`, `last_heartbeat` and `heartbeat_interval_seconds` are not None, the interval is > 0, and `max(0, (now - last_heartbeat).total_seconds()) <= max(3 * interval, 30)`
- [ ] T004 Make `_owner_is_fresh` in `infrahub_sync/service/liveness.py:270` call `worker_is_live` for the matching worker record, keeping its current behavior: the exact claiming worker id must match, and a missing id returns False. `tests/service/test_liveness_policy.py` MUST pass without edits (research R-5)
- [ ] T005 [P] Add unit tests for `worker_is_live` and `PoolWorker.name` in `tests/service/test_orchestration_pool.py` (new file): online and fresh; offline; no heartbeat; interval 0; exactly at `max(3 × interval, 30)`; one second past it; a 5-second interval uses the 30-second floor; a non-string Prefect name gives `name=None`

**Checkpoint**: `uv run pytest tests/service/test_liveness_policy.py tests/service/test_orchestration_pool.py` passes.

---

## Phase 3: User Story 1 — A monitoring tool detects a failed component or worker (P1) 🎯 MVP

**Goal**: `GET /api/health` answers 200 or 503 with the four components and the workers, as in
`contracts/health-api.md`.

**Independent Test**: quickstart scenarios 1, 2 (the `/api/health` part), 3, 4, 5, 6, 7 and 9 on the
development stack.

### Tests for User Story 1

- [ ] T006 [P] [US1] Write unit tests for `classify_error` in `tests/service/test_health.py`, parametrized over every row of research R-3, using the exact recorded psycopg 3.3.6 texts: `failed to resolve host 'no-such-host.invalid': [Errno 8] nodename nor servname provided, or not known` → `name_resolution_failed`; `connection failed: connection to server at "127.0.0.1", port 1 failed: could not receive data from server: Connection refused` → `connection_refused`; a `psycopg.errors.ConnectionTimeout` → `timeout`; `server closed the connection unexpectedly` → `connection_closed`; another `psycopg.OperationalError` text → `unavailable`; `socket.gaierror`, `ConnectionRefusedError`, `ConnectionResetError`, `TimeoutError`, `httpx.ConnectTimeout`, `httpx.RemoteProtocolError`, botocore `ConnectTimeoutError`, `ReadTimeoutError`, `EndpointConnectionError` wrapping a `ConnectionRefusedError`, `ConnectionClosedError`, `ClientError` (404 and 403) → the categories of R-3; a `RuntimeError` → `unknown_error`; an error found only in `__cause__` or inside an `ExceptionGroup`
- [ ] T007 [P] [US1] Write unit tests for `HealthChecker` in `tests/service/test_health.py` with fake checks and a fake monotonic clock: all up gives `healthy`; one check raising gives `down` with its category and `unhealthy`; a check that sleeps past the limit gives `timeout` and the round ends within limit + 0.5 s; ten concurrent `report()` calls run exactly one round; a call 0.9 s after a round **ended** reuses it and a call 1.1 s after runs a new one; `dependencies` are always the four names in the order `database`, `object_store`, `task_manager`, `task_manager_db`; a failed check logs one warning naming the component and category and never the error text
- [ ] T008 [P] [US1] Write unit tests for the worker section in `tests/service/test_health.py`: `known` with `live_workers` counted by `worker_is_live`; a worker with a heartbeat 24 h + 1 s old is not listed, one 23 h old is listed `stale`; a worker with no heartbeat is not listed; items sorted by `last_heartbeat` newest first, then name; a name that is not `infrahub-sync-worker-<uuid>` (for example `acct_x` or `laptop-01`) is shown as `other` (FR-013); an unavailable `PoolStatus` or a pool read past the limit gives `state="unknown"`, `live_workers=None`, `queue_depth=None`, `items=[]`; `unhealthy` when `live_workers < minimum` or state `unknown` with every component up; `healthy` with one live and three stale workers and minimum 1
- [ ] T009 [P] [US1] Write unit tests for the settings in `tests/service/test_settings.py` (extend the existing file if present, else create it): `INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT` accepts `"1"` and `"60"`, defaults to 3, refuses `"0"`, `"61"`, `"1.5"`, `"abc"` and `""` with a `HealthSettingsError` whose message names the variable and not the value; `INFRAHUB_SYNC_HEALTH_MIN_WORKERS` defaults to 1, accepts `"1"` and `"5"`, refuses `"0"`, `"-1"`, `"x"`; `validate_settings()` raises for each refused value
- [ ] T010 [P] [US1] Write route tests in `tests/service/test_http_api.py`: `GET /health` without credentials answers 200 with a healthy fake checker and 503 with an unhealthy one; both bodies validate against `HealthResource`; `Cache-Control: no-store` on both; the body contains no `acct_`, no host name, no IP address and no version string; a checker that raises answers the existing 503 `service-unavailable` envelope; the OpenAPI document declares the `HealthReport` schema for 200 and 503
- [ ] T011 [P] [US1] Add `/health` to the parametrized paths of `test_an_answer_with_no_identifier_makes_no_account_read` in `tests/service/test_account_presentation.py:643`, with a fake checker whose answer lists workers, so the account middleware is proven not to read the store for a health answer
- [ ] T012 [P] [US1] Write a test in `tests/service/test_serve.py` (extend the existing serve tests if present) that `build_app` with `INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT=0` raises `HealthSettingsError` before `start_check` and before the projection factory is called (both fakes record calls), and that `main()` turns `HealthSettingsError` into `SystemExit` with the variable name

### Implementation for User Story 1

- [ ] T013 [US1] Add `HEALTH_CHECK_TIMEOUT_ENV = "INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT"` ("whole seconds, 1–60", default 3), `HEALTH_MIN_WORKERS_ENV = "INFRAHUB_SYNC_HEALTH_MIN_WORKERS"` ("whole number ≥ 1", default 1), `class HealthSettingsError(ValueError)`, `health_check_timeout_seconds(environ=None) -> int` and `health_min_workers(environ=None) -> int` to `infrahub_sync/service/settings.py`, following `session_idle_seconds()` (`settings.py:177`); call both from `validate_settings()` (`settings.py:200`)
- [ ] T014 [US1] Add the response models to `infrahub_sync/client/models.py` using `Literal` types (the client supports Python 3.10, research R-9): `DependencyHealthResource(name: Literal["database","object_store","task_manager","task_manager_db"], status: Literal["up","down"], error: Literal["none","timeout","connection_refused","connection_closed","name_resolution_failed","unavailable","unknown_error"])`; `WorkerHealthResource(name: str, state: Literal["live","stale"], last_heartbeat: datetime)`; `WorkersHealthResource(state: Literal["known","unknown"], minimum: int (ge=1), live_workers: int | None (ge=0), queue_depth: int | None (ge=0), items: list[WorkerHealthResource])`; `HealthResource(status: Literal["healthy","unhealthy"], dependencies: list[DependencyHealthResource], workers: WorkersHealthResource, timestamp: datetime)`. Keep `ServiceStatusResource` and `WorkerStatusResource` for now (removed in US3). Export the four new models from `infrahub_sync/client/__init__.py` and `infrahub_sync/service/models.py`
- [ ] T015 [US1] Create `infrahub_sync/service/health.py`: `ErrorCategory` (`StrEnum`, the seven values), `classify_error(exc)` walking cause, context and exception groups (research R-3, including the psycopg text markers and botocore types), `DependencyCheck(name, check)`, and `HealthChecker(checks, pool_reader, minimum, check_timeout, max_report_age=1.0, clock=time.monotonic, wall_clock=...)` with `async report() -> HealthResource`: runs the four checks and the pool read with `asyncio.gather`, each under `asyncio.wait_for(check_timeout)`; caches the report with the monotonic time its round **ended**; re-checks the age after acquiring the lock; one `except Exception` per check boundary with `# pylint: disable=broad-exception-caught  # noqa: BLE001` and the reason from plan.md Complexity Tracking, logging only the component and category with structlog
- [ ] T016 [US1] Add the four check factories to `infrahub_sync/service/health.py`: `database_check(database_url, limit)` with `psycopg.AsyncConnection.connect(database_url, connect_timeout=max(limit, 2), options=f"-c statement_timeout={limit * 1000}")`, `SELECT 1`, close; `object_store_check(values, bucket, limit)` building a boto3 client from `s3_client_arguments(values)` (`infrahub_sync/service/storage.py:251`) with `connect_timeout=limit`, `read_timeout=limit`, `retries={"max_attempts": 0}`, calling `head_bucket(Bucket=bucket)` in `asyncio.to_thread`; `task_manager_check(api_url, limit)` and `task_manager_db_check(api_url, limit)` doing `GET {PREFECT_API_URL}/health` and `/ready` with a plain `httpx.AsyncClient(timeout=limit)`, raising a `DependencyUnavailableError` for a non-2xx answer (→ `unavailable`)
- [ ] T017 [US1] Add the worker projection to `infrahub_sync/service/health.py`: from a `PoolStatus` and the round's wall-clock time, build `WorkersHealthResource` per data-model.md (24-hour window, `worker_is_live`, name filter `^infrahub-sync-worker-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` else `other`, sort newest first then name, `unknown` when `detail_available` is False or the read timed out); and the status rule of research R-6
- [ ] T018 [US1] Add the route to `infrahub_sync/service/app.py` next to `/status` (`app.py:484`): `GET /health`, no authentication, `response_model=HealthResource`, `responses={503: {"model": HealthResource}}`, answering 200 or 503 with `Cache-Control: no-store`; `create_app` takes a `health_checker` argument. Keep `/status` for now
- [ ] T019 [US1] In `infrahub_sync/service/serve.py` `build_app` (`serve.py:100`), read `health_check_timeout_seconds()` and `health_min_workers()` right after `LivenessPolicy.from_environment` and before `start_check` (`serve.py:139`); build the four checks from `_database_url`, `s3_client_arguments`, the bucket setting and `PREFECT_API_URL`, and the pool reader from `_ClientPerCallOrchestration().pool_status` with the work pool; pass the `HealthChecker` to `app_factory`. Add `HealthSettingsError` to the errors `main()` turns into `SystemExit` (`serve.py:221`). Update every test fake of `app_factory`/`create_app` that the new argument breaks
- [ ] T020 [US1] Pass `INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT` and `INFRAHUB_SYNC_HEALTH_MIN_WORKERS` (`"${…:-3}"`, `"${…:-1}"`) to `sync-api` in `docker-compose.yml` and `development/docker-compose.yml`; leave the `sync-api` healthcheck on `/api/version` (FR-010)
- [ ] T021 [P] [US1] Write the real-service tests in `tests/dev_stack/smoke/test_health.py`, marked `smoke` like the other files of that directory: against the running development stack, `GET /api/health` is 200 with four `up` dependencies and at least one `live` worker; and in `tests/service/test_health_postgresql.py`, skipped unless `PRODUCT_STORE_TEST_POSTGRESQL_DSN` is set (as `tests/product_store/postgresql_isolation.py` does): `database_check` passes on that DSN, gives `name_resolution_failed` for host `no-such-host.invalid`, and `connection_refused` for port 1 on 127.0.0.1
- [ ] T022 [US1] Run `uv run pytest tests/service tests/client -q`, `uv run invoke format` and `uv run invoke lint` and `uv run ruff check .`; fix every finding; record results in `dev/specs/029-api-health/verification.md`

**Checkpoint**: US1 works on its own: `/api/health` answers; `/api/status` still answers.

---

## Phase 4: User Story 2 — A hung worker container shows as unhealthy (P2)

**Goal**: `docker compose ps` shows `sync-worker` `unhealthy` when its process does not run, and
`healthy` during an outage of another service.

**Independent Test**: quickstart scenario 2 (the `dc ps` part) and scenario 4 (the worker stays
`healthy` while `postgres` is stopped).

### Tests for User Story 2

- [ ] T023 [P] [US2] Write unit tests in `tests/service/test_worker_alive.py`: the writer task writes `<epoch> <interval>` to the path built from `TMPDIR` every interval (fake sleep), through a temporary file and `os.replace`; a write failure logs exactly one warning across several failures and the task keeps running; `WORKER_HEALTHCHECK_COMMAND` run with `subprocess.run([sys.executable, "-I", "-c", WORKER_HEALTHCHECK_COMMAND], env={"TMPDIR": tmp})` exits 0 for a file written now with interval 10, exits 0 at 29 s old, exits 1 at 31 s old, exits 0 at 59 s old with interval 20 (`max(3 × 20, 30) = 60`), exits 1 for a missing file, an empty file, `"abc 10"` and `"123"`, and prints one line on failure with no path outside `TMPDIR`
- [ ] T024 [P] [US2] Write a test in `tests/compose/test_documentation.py` (or a new `tests/compose/test_worker_healthcheck.py`, unmarked, reading the files only) that both `docker-compose.yml` and `development/docker-compose.yml` give `sync-worker` a healthcheck whose `test` is `["CMD", "python", "-I", "-c", WORKER_HEALTHCHECK_COMMAND]` exactly, with `interval: 10s`, `timeout: 5s`, `retries: 3`, `start_period: 60s`, and that `sync-api` keeps `/api/version`

### Implementation for User Story 2

- [ ] T025 [US2] Create `infrahub_sync/service/worker_alive.py`: `ALIVE_FILE_NAME = "infrahub-sync-worker-alive"`, `alive_file_path(environ) -> Path` from `TMPDIR` (default `tempfile.gettempdir()`), `async run_alive_writer(interval_seconds: int, path: Path, *, sleep=asyncio.sleep, clock=time.time)` writing `f"{int(clock())} {interval_seconds}"` each interval and warning once on `OSError`, and `WORKER_HEALTHCHECK_COMMAND`, a standard-library-only one-statement-per-line script that reads the file from `os.environ.get("TMPDIR", "/tmp")`, parses two integers and exits 0 when `time.time() - written <= max(3 * interval, 30)`
- [ ] T026 [US2] Start `run_alive_writer` with the worker in `infrahub_sync/service/worker.py`, next to the worker's run loop (`run`, `worker.py:486`, or the entrypoint that calls `run_until_terminated`, `worker.py:563`), with the interval from `PREFECT_WORKER_HEARTBEAT_SECONDS` (default 30, Prefect's default) and cancel it when the worker stops; the task must not depend on Prefect, PostgreSQL or the Sync API
- [ ] T027 [US2] Add the healthcheck of T024 to `sync-worker` in `docker-compose.yml` and `development/docker-compose.yml` (YAML block scalar or a single-line string equal to `WORKER_HEALTHCHECK_COMMAND`), with a comment that the check looks only at the worker's own process
- [ ] T028 [US2] Run `uv run pytest tests/service/test_worker_alive.py tests/compose -q -m "not compose"`, `uv run invoke lint` and `uv run ruff check .`; record results in `dev/specs/029-api-health/verification.md`

**Checkpoint**: US2 works on its own.

---

## Phase 5: User Story 3 — Existing readers of the worker state keep working (P3)

**Goal**: `/api/status` is gone; every reader uses `/api/health` and shows the same information.

**Independent Test**: quickstart scenario 8, and the web interface start dialog with and without a
live worker.

### Tests for User Story 3

- [ ] T029 [P] [US3] Update `tests/client/test_client.py` and `tests/client/test_not_sync_api.py`: replace the `get_status` cases with `get_health` cases: 200 report → `HealthResource`; 503 report → `HealthResource` with `status="unhealthy"`; 503 error envelope → the client's existing error-envelope exception; 404, 500, non-JSON and a body that does not validate → `ProtocolError`
- [ ] T030 [P] [US3] Update `tests/service/test_web_site.py`: `GET /api` and `GET /api/` redirect with 307 to `/api/health`; `GET /api/status` answers 404 with the not-found envelope
- [ ] T031 [P] [US3] Update `tests/service/test_http_api.py` and `tests/service/test_account_presentation.py`: remove the `/status` cases and the `/status` entry of the parametrized paths
- [ ] T032 [P] [US3] Add `frontend/src/domain/worker-state.test.ts` (Vitest) for `workerState(report)`: workers `unknown` → `unavailable`; `live_workers` 0 → `no-live-worker`; `queue_depth` 3 with live workers → `busy`; otherwise `ready`; an `unhealthy` report with a component down and one live worker and queue 0 → `ready` (the frame shows only the worker information, spec Assumptions)
- [ ] T033 [P] [US3] Update `tests/dev_stack/test_deps_mode.py`, `tests/dev_stack/smoke/test_evidence.py`, `tests/dev_stack/smoke/test_config_lifecycle.py`, `tests/compose/test_lifecycle.py` and `tests/compose/lifecycle.py` to read `/api/health` and its `workers` section instead of `/api/status`; check `tests/vendored_prefect_extras/test_executors.py` and change it only if it names the Sync API's `/status`

### Implementation for User Story 3

- [ ] T034 [US3] Remove the `GET /status` route from `infrahub_sync/service/app.py:484`, `RunService.status()` and `_service_status()` from `infrahub_sync/service/service.py` (`service.py:178-208`, `:1454`), and `ServiceStatusResource` and `WorkerStatusResource` from `infrahub_sync/client/models.py`, `infrahub_sync/client/__init__.py` and `infrahub_sync/service/models.py`
- [ ] T035 [US3] Replace `SyncClient.get_status()` (`infrahub_sync/client/client.py:265`) with `get_health() -> HealthResource` per contracts/health-api.md "Python client": unauthenticated; 200 or 503 with a report → the model; 503 with an error envelope → the existing error-envelope exception; anything else → `ProtocolError(operation, status)`
- [ ] T036 [US3] Set `API_ROOT_TARGET = f"{API_PREFIX}/health"` in `infrahub_sync/service/web.py:47` and update the module docstring (`web.py:4`) and the redirect docstring (`web.py:223`)
- [ ] T037 [US3] Change `check_host_processes` in `tasks/dev.py:1007-1031` to read `/api/health`: accept 200 and 503 bodies that are health reports (do not `raise_for_status()` on 503), report "start it with `uv run invoke dev.api`" only when there is no answer or the body is not a report, and "start it with `uv run invoke dev.worker`" when `workers.live_workers` is 0 or `workers.state` is `unknown`; remove `AVAILABLE_WORKER_STATES` if nothing else uses it
- [ ] T038 [US3] Regenerate `frontend/src/api/types.generated.ts` with `pnpm codegen:openapi` against a Sync API built from this branch (`uv run invoke dev.start`, then `cd frontend && INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001 pnpm codegen:openapi`)
- [ ] T039 [US3] In `frontend/src/api/queries.ts:46`, replace `statusQuery` with `healthQuery()`, polling `/health` every 15 s with the same retry delay, whose query function returns the parsed `HealthReport` for 200 and for a 503 whose body is a report, and throws for anything else (including the 503 error envelope), so a failure looks the same as a failed `/status` today
- [ ] T040 [US3] Add `workerState(report)` to `frontend/src/domain/run-state.ts` returning `"unavailable" | "no-live-worker" | "busy" | "ready"` from `report.workers` only (research R-8), and update the comment at `run-state.ts:621` to name `GET /health`
- [ ] T041 [US3] Move `frontend/src/routes/__root.tsx` (lines 83, 107, 151-161, 198, 234-238, 355-363) and `frontend/src/features/runs/StartRunDialog.tsx` (lines 54-121, 147) to `healthQuery()` and `workerState()`; read `report.timestamp` where `worker.observed_at` was read; keep every message and label the same
- [ ] T042 [US3] Run `cd frontend && pnpm test && pnpm lint && pnpm build` (or the scripts `frontend/package.json` defines), `uv run pytest tests -q -m "not integration and not smoke and not docker and not compose"`, `uv run invoke lint`, `uv run ruff check .`; record results in `dev/specs/029-api-health/verification.md`

**Checkpoint**: all three stories work; `/api/status` answers 404.

---

## Phase 6: Documentation, decision record and polish

- [ ] T043 [P] Document `GET /api/health` in `docs/docs/reference/sync-http-api.mdx` per contracts/health-api.md: request, 200/503, every field, the seven error categories, both settings, an example monitoring check (`curl -fsS` and an Uptime Kuma / Prometheus blackbox HTTP check on status code), the statement that it is a readiness signal and not a liveness probe, and that a probe's timeout must exceed `INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT` (FR-012); remove the `/status` section
- [ ] T044 [P] Replace `/api/status` with `/api/health` in `docs/docs/compose-deployment.mdx` (the `## Status` section at line 1177: new table of components and worker states, the in-network command at line 1198, and the "paused or hung worker" paragraph now pointing to the worker healthcheck), `docs/docs/operations/day-2-operations.mdx` (lines 10-32), `docs/docs/operations/compose-troubleshooting.mdx`, `docs/docs/quickstart-compose.mdx`, `docs/docs/development-stack.mdx`, `docs/docs/tutorials/netbox-demo-to-infrahub.mdx`, `docs/docs/tutorials/netbox-to-existing-infrahub.mdx`, `docs/docs/tutorials/nautobot-to-existing-infrahub.mdx`, `docs/docs/use-with-an-ai-agent.mdx` and `README.md:101`; state that one PostgreSQL holds both databases in Compose and what an operator then sees
- [ ] T045 [P] Replace `get_status()` with `get_health()` in `docs/docs/reference/python-api.mdx`, then run `uv run invoke docs.generate` and commit any generated change
- [ ] T046 [P] Update `tests/compose/test_documentation.py:397` (`test_the_status_table_documents_every_worker_state`) to check that the Compose page documents every error category and the worker states `live` and `stale` of the new response, and that no page under `docs/docs` (outside `release-notes`) names `/api/status`
- [ ] T047 [P] Add the decision record `dev/adr/00NN-the-health-endpoint-replaces-status.md` (NN = next free number on `feature/v3-develop` at the time; 0054 is the last today) following `dev/adr/README.md`: `/api/health` replaces the unreleased `/api/status`; the shape follows Infrahub; workers count through a minimum and stale records never fail it; the database check reads only; the worker container check reads a file its own timer writes, because one PostgreSQL holds both databases in Compose. No internal links (Slack, Jira, artifacts)
- [ ] T048 [P] Add `docs/docs/development/knowledge/` coverage: one section in `docs/docs/development/knowledge/repository-tour.md` for `health.py` and `worker_alive.py`, and update `docs/docs/development/knowledge/orchestration-prefect.md` if it describes the worker liveness rule, to name `worker_is_live`
- [ ] T049 [P] Add changelog fragments with `uv run --extra dev towncrier create -c "…" +api-health.added.md` (the endpoint and the worker container healthcheck) and `+api-status-removed.changed.md` (`/api/status` and `SyncClient.get_status()` replaced by `/api/health` and `get_health()`)
- [ ] T050 Run the reader grep of plan.md (`grep -rnE "get_status|/status\b|api/status|statusQuery|ServiceStatusResource|WorkerStatusResource" infrahub_sync frontend/src tasks tests docs/docs development docker-compose.yml README.md`) and confirm every remaining match is on this allow-list: Prefect's own paths, `docs/docs/release-notes/`, `dev/specs/archive/`, HTTP `status_code` attributes; fix anything else
- [ ] T051 Run quickstart.md scenarios 1–9 on the development stack (`uv run invoke dev.start`), record each result and the measured timings for SC-001, SC-002 and SC-003 in `dev/specs/029-api-health/verification.md`, and put the measured recovery times into the documentation of T043/T044
- [ ] T052 Run the full gate: `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run ty check .`, `uv run invoke tests.tests-unit`, `uv run invoke docs.rumdl`, `uv run infrahub-sync --help`, `uv run infrahub-sync configs --help`, `uv run infrahub-sync runs --help`, and the frontend checks; record the results in `dev/specs/029-api-health/verification.md`

---

## Dependencies and execution order

- **Phase 1** → **Phase 2** → US1 (Phase 3) → US3 (Phase 5) → Phase 6.
- **US2** (Phase 4) depends only on Phase 1 and can run in parallel with US1 and US3.
- Within US1: T006–T012 (tests, parallel) → T013, T014 → T015 → T016, T017 → T018 → T019 → T020 → T021 → T022.
- Within US3: T029–T033 (tests, parallel) → T034 → T035, T036, T037 → T038 → T039 → T040 → T041 → T042.
- Phase 6: T043–T049 in parallel; then T050 → T051 → T052.

## Parallel examples

- Phase 2: T005 while T003/T004 are written.
- US1: T006, T007, T008, T009, T010, T011, T012 together (all tests, mostly one new file split by section; T009–T012 different files).
- US2 next to US1: T023, T024 while US1's tests are written.
- US3: T029, T030, T031, T032, T033 together.
- Phase 6: T043–T049 together.

## Implementation strategy

1. **MVP = US1**: `/api/health` answers next to `/api/status`. A monitoring tool can use it from
   this point.
2. **US2**: the worker container healthcheck, independent.
3. **US3**: remove `/api/status` and move the readers, once `/api/health` is in place.
4. **Phase 6**: documentation, decision record, the measured timings and the full gate.
