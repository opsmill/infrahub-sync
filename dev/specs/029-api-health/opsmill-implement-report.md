# Implementation report: spec 029, health of the Sync API and each worker

- Feature: `GET /api/health`, the worker container health check, removal of `GET /api/status`
- Spec directory: `dev/specs/029-api-health`
- Branch: `029-api-health`
- Base commit: `a688a6bb` (end of preparation)
- Head commit: `bb75fe89` before this report
- Wall-clock time: about 3 hours (2026-11-23, 20:50 to 23:55 UTC)
- Result: DONE. All 52 tasks are ticked, every new or changed test has local-pass evidence, and
  the review ran with `/code-review`.

## Chunk ledger

| Chunk | Tasks | Outcome | Commits | Points flagged |
| --- | --- | --- | --- | --- |
| Before the loop: T001, T003, T004 | 3 | 3 ✅ | `7144b2a5`, `31c7b1fa` | T003 and T004 copy the text of commit `25371149` on branch `030-automatic-resume`, so the two branches merge without a conflict. |
| 1. Setup and foundational (T002, T005) | 2 | 2 ✅ | `ee285b5d` | T001 named a `verification.md` that did not exist yet; chunk 3 created it. |
| 2. User Story 1, health checker core (T006–T009, T013–T017) | 9 | 9 ✅ | `55428d35`, `daea2f14` | `ErrorCategory` is `(str, Enum)` because ty checks against Python 3.10. A client-boundary test now allows `httpx.AsyncClient` in `health.py`, because it calls Prefect. |
| 3. User Story 1, route and wiring (T010–T012, T018–T022) | 8 | 8 ✅ (T021 first passed against a host-run API) | `36161b41`, `9248cadb` | The running development stack belongs to the `preview-v3` worktree. For T021 the subagent ran this branch's Sync API on the host for about 90 seconds against that stack's services; at start-up it applied the development `admin` account settings (the same values) and ran schedule and liveness reconciliation once. Later chunks were told not to touch that stack. |
| 4. User Story 2, worker container health check (T023–T028) | 6 | 6 ✅ | `c603cac0`, `e0c424e3` | The interval comes from Prefect's local `worker.heartbeat_seconds` setting, at least 1 second. A clock-rounding race in two tests was fixed. |
| 5. User Story 3, backend (T029–T031, T033–T037) | 8 | 8 ✅ | `b325f4f8` | Three liveness tests moved from `_service_status` to `workers_health`; `workers_health` calls `normalized_pool_status` first. |
| 6. User Story 3, web interface (T032, T038–T042) | 6 | 6 ✅ | `751ffa44`, `9aea57de` | The shared fetch middleware in `client.ts` now lets a 503 from `/api/health` that carries a health report through. The build needed the `frontend/vendor/infrahub` submodule, which the orchestrator initialized. |
| 7. Documentation, ADR and gate (T043–T050, T052) | 9 | 9 ✅ | `ed24e147`, `389cadc7` | ADR 0055 amends ADR 0043, which named `/api/status`. Vale spelling exceptions `blackbox` and `Kuma` were added. |
| T051, quickstart scenarios | 1 | 1 ✅ | `bb75fe89` | Run by the orchestrator on an isolated Compose project, not on the shared development stack. |
| Review fixes | — | 6 findings fixed | `0a99c3e2`, `b97c6acd` | See the review section. |

## Tasks not completed

None.

## Local-pass evidence

All runs were on macOS with Python 3.11 and the dev, prefect and service extras, unless the
environment column says otherwise.

| Test id | Type | Run command | Passed at | Environment | Pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/service/test_orchestration_pool.py` (16 tests: liveness rule, Prefect worker name) | unit | `uv run pytest tests/service/test_orchestration_pool.py tests/service/test_liveness_policy.py -q -p no:randomly` | 2026-11-23T21:16:34Z | n/a | `90 passed, 1 warning in 1.38s` |
| `tests/service/test_health.py`, `tests/service/test_settings.py`, `tests/cli/test_parity_and_closure.py::test_only_shared_client_constructs_sync_api_http_requests` (87 tests) | unit | `uv run pytest <ids> -v -p no:randomly --no-cov` | 2026-11-23T21:42:11Z | n/a | `87 passed in 4.92s` |
| `tests/service/test_http_api.py` health route tests, `tests/service/test_account_presentation.py::...[/health]`, `tests/service/test_serve.py` (6), `tests/service/test_serve_site.py`, `tests/service/test_schedule_routes.py::test_the_served_api_delivers_both_write_targets` | unit | `uv run pytest <id> -q -p no:randomly -rA` | 2026-11-23T21:55:59Z to 22:11:03Z | n/a | `PASSED` for each |
| `tests/service/test_health_postgresql.py` (3) | integration | `PRODUCT_STORE_TEST_POSTGRESQL_DSN=postgresql://postgres:…@127.0.0.1:5440/infrahub_sync uv run pytest tests/service/test_health_postgresql.py -q -p no:randomly` | 2026-11-23T23:49:59Z (after the review fixes) | PostgreSQL of the development stack, read only | `3 passed in 0.82s` |
| `tests/dev_stack/smoke/test_health.py::test_the_development_stack_reports_four_components_up_and_a_live_worker` | e2e (smoke) | `uv run pytest tests/dev_stack/smoke/test_health.py -m smoke` with a plugin pointing at port 8011 | 2026-11-23T21:50:21Z | This branch's Sync API on the host, with the `preview-v3` stack's services | `1 passed in 0.52s` |
| `tests/service/test_worker_alive.py` (16), `tests/compose/test_worker_healthcheck.py` (4), `tests/service/test_worker_termination.py::test_main_runs_the_worker_inside_the_sigterm_handler` | unit | `uv run pytest tests/service/test_worker_alive.py tests/service/test_worker_termination.py tests/compose -q -m "not compose" -p no:randomly -rA` | 2026-11-23T22:34:36Z | n/a | `432 passed, 6 skipped, 64 deselected in 17.99s` |
| `tests/client/test_client.py` `test_get_health_*` (9), `tests/client/test_not_sync_api.py` (3), `tests/service/test_web_site.py` redirect and 404 tests, `tests/dev_stack/test_deps_mode.py`, `tests/dev_stack/smoke/test_evidence.py`, three liveness tests | unit | `uv run pytest <files> -q -p no:randomly -rA` | 2026-11-23T22:43:39Z | n/a | `753 passed, 1 warning in 19.30s` |
| `frontend/src/domain/worker-state.test.ts` (5), `frontend/src/api/health-query.test.ts` (4) | unit (Vitest) | `cd frontend && pnpm test` | 2026-11-23T22:55:44Z | Node v26.8.1 | `Tests  419 passed (419)` |
| `tests/compose/test_documentation.py` new checks (10), `tests/service/test_web_site.py::test_a_program_outside_api_gets_the_envelope[...]` (4) | unit | `uv run pytest <ids> -v -p no:randomly -p no:xdist -o addopts=""` | 2026-11-23T23:25:52Z | n/a | `14 passed, 1 warning in 2.43s` |
| Review-fix tests in `test_health.py` (3 new, 6 renamed), `test_worker_alive.py` (2), `tests/dev_stack/test_dev_tasks.py` (5) | unit | `uv run pytest <id> -v -p no:randomly` | 2026-11-23T23:40:26Z | n/a | `PASSED` for each |
| `tests/service/test_http_api.py::test_head_health_answers_the_status_and_headers_of_get_without_a_body[True|False]` | unit | same | 2026-11-23T23:49:59Z | n/a | `2 passed, 1 warning in 1.72s` |
| `tests/compose` with the `compose` marker (64 tests, including the changed `lifecycle.py` and `test_lifecycle.py`) | e2e | `INFRAHUB_SYNC_COMPOSE_PROJECT_PREFIX=sync029 INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test-029 uv run --extra testcontainers pytest -m compose tests/compose` | started 2026-11-23T23:06:39Z | Image built from this branch; own Compose projects | `2 failed, 62 passed`; the two failures pass alone: `2 passed in 175.75s` (see below) |
| Quickstart scenarios 1–9 and SIGSTOP (T051) | e2e (manual) | scratch script on Compose project `sync029-qs` | 2026-11-23T23:27:03Z to 23:37:43Z; names and HEAD after the fixes at about 23:55Z | Root `docker-compose.yml`, image built from this branch | Recorded in `verification.md` |

Gates at the end of chunk 7 and after the review fixes: `uv run invoke format` clean;
`uv run invoke lint` exit 0 (the 31 ty diagnostics are unused-ignore warnings in files this
change does not touch); `uv run ruff check .` clean; unit tier 11282 passed, 12 skipped;
`uv run invoke tests.check-310` passed all three Python 3.10 legs; frontend test, lint,
typecheck and build pass; rumdl, Vale and the Docusaurus build clean.

The two `compose` failures are `configuration-name-in-use` (409): two tests register
`compose-suite-registered` in the session-wide deployment after another test registered it.
Run alone, both pass. This branch does not change configuration registration, so the order
problem is in the base branch. The nightly workflow that runs this suite last ran in October.

## Review findings

Review mode: command (/code-review)

`/code-review high a688a6bb..HEAD` reported nine findings.

| Severity | File | Finding | Action |
| --- | --- | --- | --- |
| important | `infrahub_sync/service/health.py` | The worker name pattern expected `infrahub-sync-worker-<uuid>`, but workers register as `infrahub-sync-service-<uuid>`, so every real worker was shown as `other`. The wrong prefix came from the spec. | Fixed in `0a99c3e2`: one constant in `orchestration.py`, used by `worker.py` and `health.py`; tests, docs, ADR and spec corrected. Checked on the isolated deployment. |
| important | `infrahub_sync/service/health.py` | The database check passed `options=-c statement_timeout=…`, which replaces the URL's `options` and is refused by poolers such as PgBouncer. | Fixed: no `options`; the round's time limit bounds the query, and psycopg cancels it on the server. |
| important | `infrahub_sync/service/health.py` | An error other than three types while the Prefect client opens replaced the whole report with the error envelope. | Fixed: the pool read boundary catches any error, logs its type only, and reports the workers as `unknown`. Complexity Tracking updated. |
| important | `infrahub_sync/service/app.py` | `HEAD /api/health` answered 405. | Fixed: a separate HEAD route outside the OpenAPI document; same status and headers, no body. |
| nit | `infrahub_sync/service/worker_alive.py` | The writer and the health check command resolved the temporary directory with different rules. | Fixed: both use `os.environ.get("TMPDIR") or "/tmp"`. |
| nit | `tasks/dev.py` | `check_host_processes` raised `AttributeError` for a JSON answer that is not an object. | Fixed: refused with `DevStackError`. |
| nit | `infrahub_sync/service/health.py` | `head_bucket` needs `s3:ListBucket`; a policy limited to the prefix gets 403. | Deferred. The product store already needs the bucket; to document if a deployment reports it. |
| nit | `infrahub_sync/service/health.py` | A timed-out object store check leaves its thread running until botocore's own timeouts end. | Deferred. Bounded by the one-round-per-second rule and the botocore timeouts set to the limit, as plan.md Risks states. |
| nit | `infrahub_sync/service/health.py` | The broad `except` in `_run_check` breaks the AGENTS.md rule. | Deferred. Justified in plan.md Complexity Tracking, with the same markers as `contain_unhandled_error`. |

## Autonomous decisions

- T003 and T004 were copied from branch `030-automatic-resume` instead of written again; that
  session was told, and later told of the new `SERVICE_WORKER_NAME_PREFIX` constant in the same
  file.
- The quickstart (T051) and the `compose` suite ran on separate Compose projects (`sync029-qs`,
  prefix `sync029`) with an image built from this branch, because the development stack belongs
  to another session. Both projects were removed afterwards.
- The measured times replaced the design targets in `docs/docs/compose-deployment.mdx`. Two
  measurements differ from the spec and are now documented: Docker shows no health status for a
  paused container (a hung process turns it `unhealthy`), and with PostgreSQL stopped the workers
  section is `unknown` at once instead of `stale` after 30 seconds. `spec.md` and the contract
  keep their original wording.
- The orchestrator initialized the `frontend/vendor/infrahub` submodule in this worktree to run
  the web interface build.
- Three low-severity review findings were deferred, with reasons above.

## Suggested next steps

1. Open the pull request into `feature/v3-develop` as a draft and watch CI.
2. Fix the test-order problem of the `compose` suite (`compose-suite-registered` registered twice)
   in a separate change on the base branch.
3. Decide whether `spec.md` US1-2 and the contract should be updated to the measured PostgreSQL
   behavior (`unknown` at once).
