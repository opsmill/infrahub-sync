# Tasks: Discoverable runs, configurations and versions, and consistent verbs

**Input**: Design documents from `dev/specs/015-run-discovery-api/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/http-api.md](contracts/http-api.md),
[contracts/cli.md](contracts/cli.md), [quickstart.md](quickstart.md)

**Tests**: included. `AGENTS.md` and the constitution require targeted tests for new
features. Write each story's tests first and confirm they fail before implementing.

**Format**: `[ID] [P?] [Story] Description`. `[P]` means the task touches different files from
other open tasks in the phase and has no dependency on them.

Run after every phase: `uv run invoke format`, `uv run invoke lint`,
`uv run invoke tests.tests-unit`.

## Phase 1: Setup

- [X] T001 Confirm the development profile installs and the unit tier passes on the branch base before any change: `uv sync --extra dev --extra prefect --extra service`, then `uv run invoke tests.tests-unit` (record the baseline result in the task note)
  - Baseline (commit 6b6d1886, Python 3.11.8): `uv sync` installed without errors. Unit tier: 5714 passed, 4 skipped, 203 deselected, 0 failed, 4 warnings, in 152 s. No failures existed before this feature's changes.

## Phase 2: Foundational (blocks all user stories)

**Purpose**: storage changes and the shared cursor module that every list route uses.

- [X] T002 Add idempotent migration steps in `infrahub_sync/product_store/store.py` `_initialize`: indexes `product_runs_started (started_at, run_id)` and `product_runs_binding_started (config_id, registry_version, started_at, run_id)`; nullable columns `configuration_versions.registered_by`, `configuration_versions.registration_reason` and `configurations.name`, added through the existing catalog-checked pattern (`_migrate_product_runs_columns` is the model) on SQLite and PostgreSQL; and fix the stale comment at `store.py:142-149` that says no `ProductRun` field reads the binding columns
- [X] T003 Convert `started_at` to UTC in the run insert path of `_RelationalRunStore` in `infrahub_sync/product_store/store.py` (critique E1); add the nullable `product_runs.destination_branch` column through the same catalog-checked migration as T002 and include it in `_INSERT_PRODUCT_RUN` / `_SELECT_PRODUCT_RUN`; add `destination_branch: str | None = None` to `ProductRun` in `infrahub_sync/product_store/models.py` and to `PublicRunResource` in `infrahub_sync/client/models.py`
- [X] T004 [P] Add migration tests in `tests/product_store/test_contract.py`: the new indexes and columns exist after start-up, a second start-up is a no-op, and an existing database without them is upgraded; add the PostgreSQL case in `tests/product_store/test_postgresql_isolation.py`
- [X] T005 [P] Add a test in `tests/product_store/test_contract.py` that a run inserted with a non-UTC `started_at` is stored and ordered as its UTC instant
- [X] T006 [P] Create `infrahub_sync/service/paging.py`: `encode_cursor(sort_key, filters)`, `decode_cursor(value, filters)` (URL-safe base64 JSON with keys `k` and `f`, where `f` is the first 16 hex characters of SHA-256 of the canonical filter JSON), a `InvalidCursorError`, and `validate_limit(value)` (default 50, range 1–200); typed, with docstrings
- [X] T007 [P] Add `tests/service/test_paging.py`: round trip, cursor reused with different filters is refused, malformed base64 and malformed JSON are refused, limit default and bounds (parametrized)
- [X] T008 Map `InvalidCursorError` to `422 invalid-cursor` in the error handlers of `infrahub_sync/service/app.py` and `infrahub_sync/service/config_routes.py`, using the existing error envelope

**Checkpoint**: storage and paging are ready; user stories can start.

## Phase 3: User Story 1 — List the runs of a configuration version (P1, MVP)

**Goal**: `GET /runs` with configuration and version filters, newest first, keyset paged.

**Independent test**: create three runs across two versions, apply one, list with and without
filters, and page through 120 runs while inserting 10 more (quickstart "Paging check").

### Tests for User Story 1

- [ ] T009 [P] [US1] Store tests in `tests/product_store/test_contract.py` for `list_runs`: newest-first order with `run_id` tie-break for equal `started_at`, filter by `config_id`, filter by `config_id` and `registry_version`, legacy runs included only without filters, `after` key excludes newer rows, limit respected
- [ ] T010 [P] [US1] Route tests in `tests/service/test_http_api.py` for `GET /runs`: 401 without token, three runs for configuration C and two for version 1 (spec US1 scenarios 1–2), list items have empty `artifact_refs` and `prefect_executions`, 404 `config-not-found` and `config-version-not-found`, 422 when `registry_version` is given without `config_id`, 422 for limit 0 and 201, `next_cursor` null on the last page
- [ ] T011 [P] [US1] Paging stability test in `tests/service/test_http_api.py`: 120 runs, read page 1 with limit 50, insert 10 newer runs, follow cursors; every original run appears exactly once and none of the 10 new runs appears (SC-005, FR-004)

### Implementation for User Story 1

- [ ] T012 [US1] Add `RunFilters`, `RunPage` and the `list_runs(filters, after, limit)` protocol method to `_RunStore` in `infrahub_sync/product_store/store.py` (store-side models in `infrahub_sync/product_store/models.py` if that is where `ProductRun` lives)
- [ ] T013 [US1] Implement `list_runs` in `_RelationalRunStore` in `infrahub_sync/product_store/store.py`: one query ordered by `started_at DESC, run_id DESC`, keyset predicate `(started_at, run_id) < (?, ?)` written as an OR expression portable to SQLite and PostgreSQL, `limit + 1` rows to detect a next page, rows mapped to `ProductRun` without loading artifacts or executions; add the pass-through on `ProductProjection`
- [ ] T014 [US1] Add `RunPage` to `infrahub_sync/client/models.py` and its bounded emitted variant to `infrahub_sync/service/models.py`, and include it in `EMITTED_RESOURCES`
- [ ] T015 [US1] Add `RunService.list_runs(config_id, registry_version, cursor, limit)` in `infrahub_sync/service/service.py`: validate that the configuration and version exist (raise the existing not-found `ServiceAPIError` codes `config-not-found` / `config-version-not-found`), decode the cursor with the filters, call the projection, project each run with `public_run_resource`, encode `next_cursor`
- [ ] T015a [US1] In `RunService.create_run` (`infrahub_sync/service/service.py:176-213`), compute the effective destination branch (package `configuration.destination.settings.branch` if declared, else the request `branch`, else `main`) and store it on the new `ProductRun`; test in `tests/service/test_http_api.py` that `GET /runs/{id}` and `GET /runs` show it for a declared and an undeclared package branch, that it equals the saved plan's `destination_branch`, and that a run inserted without it shows `null`
- [ ] T016 [US1] Add `GET /runs` to `infrahub_sync/service/app.py` with the `authenticate` dependency and query parameters `config_id`, `registry_version`, `limit`, `cursor`; declare it before `GET /runs/{run_id}`

**Checkpoint**: US1 delivers the run list through the API.

## Phase 4: User Story 2 — List configurations and their versions (P1)

**Goal**: named, paged configuration list with a name filter; light, paged version list with
latest run and latest applied run; actor and reason recorded at registration.

**Independent test**: spec US2 scenarios 1–4.

### Tests for User Story 2

- [ ] T017 [P] [US2] Store tests in `tests/product_store/test_configs_service.py`: registration stores `registered_by` and `registration_reason`; `configurations.name` follows the latest version's `configuration.name`; start-up fills a `NULL` name from the latest version; two configurations may share a name
- [ ] T018 [P] [US2] Route tests in `tests/service/test_config_routes.py`: `GET /configs` returns `{items, next_cursor}` newest first with `name` and `latest_version`; `?name=` returns both configurations that share a name; `GET /configs/{id}` includes `name` and `latest_version`; `GET /configs/{id}/versions` returns summaries without `declared_content`, newest version first, with `latest_run` and `latest_applied_run` (version 1 applied, version 2 planned only, version 3 never run → both null); `limit` actually limits the page and `cursor` returns the next page (the bug where paging parameters were ignored); `offset` is no longer a declared parameter; 404 for an unknown configuration

### Implementation for User Story 2

- [ ] T019 [US2] Write `registered_by`, `registration_reason` and `configurations.name` in the version insert transaction in `infrahub_sync/product_store/store.py`, and pass actor and reason from `ConfigurationRoutes.mutate` through `infrahub_sync/product_store/configs.py` `register` and version creation
- [ ] T020 [US2] Add the start-up step in `infrahub_sync/product_store/store.py` that fills `configurations.name` where it is `NULL` from the latest version's `declared_content["configuration"]["name"]`; idempotent and tolerant of a package without a name (leaves `NULL`)
- [ ] T021 [US2] Add paged store queries in `infrahub_sync/product_store/store.py`: configurations ordered by `created_at DESC, config_id DESC` with an optional exact `name` filter and `latest_version`; versions ordered by `registry_version DESC`; and one query per page that returns, for each `(config_id, registry_version)` on the page, the newest run and the newest run with `phase = 'applied'` (window function or grouped subqueries, both portable)
- [ ] T022 [US2] Add `ConfigurationVersionSummaryResource`, `RunReference`, `ConfigurationPage`, `ConfigurationVersionPage`, and `name` / `latest_version` on `ConfigurationSummaryResource` in `infrahub_sync/client/models.py`
- [ ] T023 [US2] Rewrite `list_configs` and `list_versions` in `infrahub_sync/service/config_routes.py` (both the `ConfigurationRoutes` methods and the router functions at `config_routes.py:331-356`): replace `offset` with `cursor`, use `validate_limit` and the paging module, add the `name` filter, return page envelopes; update `infrahub_sync/product_store/configs.py` `list_configs` / `list_versions` accordingly; leave the `validate` route's own paging unchanged

**Checkpoint**: SC-001 is reachable through the API (configs by name → versions → runs).

## Phase 5: User Story 3 — Filter Prefect flow runs by Sync run and configuration (P2)

**Goal**: identifying tags on every flow run Sync submits, without losing existing tags.

**Independent test**: spec US3 scenarios 1–3 against a fake Prefect client.

- [ ] T024 [P] [US3] Tests in `tests/service/test_run_titles.py`: after submission the fake client received `update_flow_run` with the union of its existing tags (`infrahub-sync`, `service`, an operator tag) and `run:<id>`, `stage:<stage>`, `config:<id>`, `config-version:<id>@<v>`; a legacy run gets no `config*` tags; no tag contains the actor, branch or work pool; a failing `read_flow_run` or `update_flow_run` leaves the submission successful and logs a warning
- [ ] T025 [US3] Implement tags in `infrahub_sync/service/orchestration.py`: widen `_NamingClient` with `read_flow_run` and the `tags` argument of `update_flow_run`; build run tags from the submission parameters (`run_id`, `stage`, `config_id`, `registry_version`); in `_title`, read current tags, write `sorted(current | run_tags)` together with the name, inside the existing best-effort `try` block

**Checkpoint**: SC-002 can be checked in the Prefect UI.

## Phase 6: User Story 4 — Check a token (P3)

**Goal**: `GET /me`.

**Independent test**: spec US4 scenarios 1–2.

- [ ] T026 [P] [US4] Route tests in `tests/service/test_http_api.py`: owner token returns `administrator: false`, admin token returns `true`, the body has exactly `actor` and `administrator`, an invalid token returns 401 and writes a `refused-authentication` audit event, and no success audit event is written
- [ ] T027 [P] [US4] Add `PrincipalResource` to `infrahub_sync/client/models.py`
- [ ] T028 [US4] Add `GET /me` to `infrahub_sync/service/app.py` returning `PrincipalResource` from the `authenticate` dependency

## Phase 7: User Story 5 — Use the same verbs in the CLI and the API (P3)

**Goal**: client methods for the new routes, then the CLI command map in
[contracts/cli.md](contracts/cli.md). Depends on US1, US2 and US4.

### Tests for User Story 5

- [ ] T029 [P] [US5] Client tests in `tests/client/test_client.py` (with `httpx.MockTransport`): `list_runs` sends filters, limit and cursor and parses `RunPage`; `iter_runs` follows `next_cursor` until null; `me` parses `PrincipalResource`; `list_configs` and `list_config_versions` parse the page envelopes and support `name`, `limit`, `cursor`; iteration helpers for both lists
- [ ] T030 [P] [US5] CLI tests in `tests/cli/test_client_commands.py` (injected `MagicMock(spec=SyncClient)`): `plan` calls `client.plan`; `diff` behaves like `plan` and writes the deprecation warning to stderr and is hidden from `--help`; `verify RUN_ID` calls `client.verify`; `runs list` with and without `--config`/`--version`, prints the "more runs" hint, and `--all` follows cursors; `runs artifacts` calls `list_artifacts`; `runs cancel` without `--yes` refuses when not interactive (exit 2) and calls `cancel_run` with `--yes`; `whoami` prints actor and administrator; `configs list --name`; `configs versions` prints latest run columns
- [ ] T031 [P] [US5] Name-resolution tests in `tests/cli/test_client_commands.py` (parametrized): identifier found → used; identifier not found and one name match → used; several name matches → exit 2 listing the IDs; no match → exit 2 not-found message; applies to `plan`, `sync`, `runs list --config` and `configs versions`

### Implementation for User Story 5

- [ ] T032 [US5] Add to `SyncClient` in `infrahub_sync/client/client.py`: `list_runs(config_id=None, registry_version=None, limit=None, cursor=None) -> RunPage`, `iter_runs(...)`, `me() -> PrincipalResource`; change `list_configs` and `list_config_versions` to return pages with `name` / `limit` / `cursor` and add `iter_configs` / `iter_config_versions`; update `_request_list` callers that expected a bare array
- [ ] T033 [US5] Add a configuration resolver in `infrahub_sync/cli.py` (identifier first via `get_config`, then `iter_configs(name=...)`, with the error messages from the CLI contract) and use it for every `CONFIG` argument and `--config`/`--config-id` option
- [ ] T034 [US5] Rename `diff` to `plan` in `infrahub_sync/cli.py` (keep `--config-id` as an alias of `--config`), and register `diff` as a hidden command that writes `warning: 'diff' is deprecated, use 'plan'` to stderr and delegates to `plan`
- [ ] T035 [US5] Add `verify RUN_ID [--reason]`, `runs list`, `runs artifacts RUN_ID`, `runs cancel RUN_ID [--reason] [--yes]` and `whoami` to `infrahub_sync/cli.py`, following the output rules in the CLI contract; update `configs list` (name column, `--name`, `--limit`, `--all`) and `configs versions` (latest run columns, paging)

**Checkpoint**: SC-003 and SC-004 hold from the CLI.

## Phase 8: Polish and documentation

- [ ] T036 [P] Update `docs/docs/reference/sync-http-api.mdx`: a "Terms" section defining run, plan and stage and stating that verify and apply act on the run's plan; add `GET /runs` and `GET /me` to the route table; document `limit`/`cursor` paging and the list envelopes; add `422 invalid-cursor`; add a CLI-to-API mapping table; replace the `diff` wording
- [ ] T037 [P] Update `docs/docs/running-a-sync.mdx`: `plan` instead of `diff`, a "Find runs" section with `runs list` and `configs versions`, `whoami`, and how to filter flow runs in Prefect by the `run:`, `config:` and `config-version:` tags
- [ ] T038 [P] Replace `infrahub-sync diff` with `infrahub-sync plan` in the current docs pages listed in research R12 (`compose-deployment.mdx`, `using-netbox-or-nautobot-with-infrahub.mdx`, `development-stack.mdx`, `readme.mdx`, `migrating-from-netbox-or-nautobot.mdx`, `development/guides/adding-an-adapter.md`, `development/guidelines/testing-tiers.md`, the three tutorials, `reference/schema-mapping.mdx`); leave `release-notes/` unchanged
- [ ] T039 [P] Update `docs/docs/development/constitution.md` line 20 to name `plan` instead of `diff`, and the CLI command list in `AGENTS.md`
- [ ] T040 Regenerate the CLI reference with `uv run invoke docs.generate` (writes `docs/docs/reference/cli.mdx`) and confirm `diff` does not appear; state in it that a configuration value is tried as an identifier before a name (critique P2)
- [ ] T041 [P] Add changelog fragments under `changelog/`: `+run-discovery.added.md` (run list, token check, Prefect tags, new CLI commands), `+run-discovery.changed.md` (paged list envelopes, `diff` renamed `plan`), `+run-discovery.removed.md` (`offset` parameter on configuration lists)
- [ ] T042 Run `uv run invoke docs.lint`, `uv run invoke format`, `uv run invoke lint`, `uv run ty check .`, `uv run invoke tests.tests-unit`, and the CLI sanity commands from `AGENTS.md`; record results
- [ ] T043 Run the end-to-end scenario in [quickstart.md](quickstart.md) on the local development stack and record the outcome in `dev/specs/015-run-discovery-api/quickstart.md` notes

## Dependencies and execution order

- Phase 1 → Phase 2 → user stories.
- US1 (Phase 3) and US2 (Phase 4) depend only on Phase 2 and can run in parallel; both edit
  `store.py`, so serialize T013 and T021 or merge carefully.
- US3 (Phase 5) depends only on Phase 1; it touches `orchestration.py` only.
- US4 (Phase 6) depends only on Phase 1.
- US5 (Phase 7) depends on US1, US2 and US4.
- Phase 8 depends on all stories.

### Parallel examples

- Phase 2: T004, T005, T006, T007 together after T002–T003.
- US1 tests: T009, T010, T011 together.
- US3 and US4 can run beside US1/US2.
- US5 tests: T029, T030, T031 together.
- Phase 8: T036–T039 and T041 together.

## Implementation strategy

1. **MVP**: Phases 1–3 deliver the run list through the API, which unblocks the web UI
   (INFP-664) and the CLI `runs list`.
2. Add US2 so SC-001 holds end to end.
3. Add US3 and US4 in any order.
4. Add US5, then documentation.

Each phase leaves the unit tier green and can be reviewed as its own commit.
