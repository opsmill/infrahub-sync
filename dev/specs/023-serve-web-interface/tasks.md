---
description: "Task list for serving the web interface from the Sync API"
---

# Tasks: Serve the web interface from the Sync API

**Input**: Design documents from `specs/023-serve-web-interface/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Included. The plan's constitution check (principle V) names the test files: `tests/service/test_site.py`,
`tests/service/test_anonymous_access.py`, the image tests, the smoke suite, and Vitest tests for the token flow (R12).
Write each test task before the implementation task it covers, and confirm that it fails first.

**Organization**: Tasks are grouped by user story, so each story can be built and tested on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with the other [P] tasks of its phase (different files, no dependency on an unfinished
  task).
- **[Story]**: The user story the task belongs to (US1, US2, US3).

## Path conventions

Python sources are under `infrahub_sync/`, Python tests under `tests/`, and the web interface under `frontend/`, as
listed in the plan's source tree.

---

## Phase 1: Setup (shared tooling)

**Purpose**: Add the build and test tooling that later phases depend on.

- [X] T001 [P] Change `packageManager` in `frontend/package.json` to
  `pnpm@10.34.5+sha512.a4ee05f2f73658255bd6a89859c065a45c28a57daefae2c893a168ee2b73168c37b91e83e57ea67654ad03f03031746430e8bce38e362e042605fb8abc80192e`
  (research R5), then run `pnpm install` in `frontend/` and confirm `pnpm-lock.yaml` does not change.
- [X] T002 [P] Add Vitest, jsdom, `@testing-library/react` and `@testing-library/jest-dom` as development dependencies
  in `frontend/package.json`, add the script `"test": "vitest run"`, and add a `test` block to `frontend/vite.config.ts`
  with `environment: "jsdom"` (research R12). Update `frontend/pnpm-lock.yaml` with `pnpm install`.
- [X] T003 [P] Add a step that runs `pnpm test` after `pnpm typecheck` in the `frontend-lint` job of
  `.github/workflows/workflow-linter.yml` (research R12).
- [X] T004 [P] Add `submodules: true` to the `actions/checkout` step of `.github/workflows/ci-docker-image.yml`
  (research R6).

---

## Phase 2: Foundational (move the Sync API under `/api`)

**Purpose**: Build the outer application that mounts the Sync API at `/api`. Every story depends on it.

**Warning**: After this phase, the CLI, the compose health checks and the smoke suite call the old paths and fail until
User Story 2 is done. Merge this phase only together with User Story 2.

### Tests for the foundation

- [X] T005 [P] Write `tests/service/test_site.py` with tests for the mount, using a `create_app()` built with a stub
  reconciler (research R1):
    - `GET /api/version` returns the version document, and `GET /version` returns 404.
    - `GET /api/openapi.json` lists `/runs/{run_id}` without `/api` and declares `servers: [{"url": "/api"}]`.
    - Entering the outer application's lifespan calls the stub reconciler's `reconcile_once()` (FR-011).
    - `request.url.path` seen by a route inside the mount starts with `/api`.
- [X] T006 [P] Add tests to `tests/service/test_http_api.py` for the 404 and 405 envelopes (research R2,
  `contracts/http-paths.md`): an unknown path returns `{"error": {"code": "not-found", ...}}` with status 404, and a
  known path with a wrong method returns code `method-not-allowed` with status 405.

### Implementation for the foundation

- [X] T007 Add a handler for `starlette.exceptions.HTTPException` in `create_app()` in `infrahub_sync/service/app.py`
  that returns the existing `ErrorEnvelope` with code `not-found` for 404 and `method-not-allowed` for 405, and the
  message from `contracts/http-paths.md` (FR-004, research R2).
- [X] T008 Create `infrahub_sync/service/site.py` with `create_site(api: FastAPI, *, web_directory: Path | None) ->
  FastAPI`. It builds the outer application with `docs_url`, `redoc_url` and `openapi_url` set to `None`, mounts `api`
  at `/api`, and enters `api.router.lifespan_context(api)` inside its own lifespan (FR-007, FR-011, research R1). Leave
  the web interface parts to T017 and T018.
- [X] T009 Change `build_app()` in `infrahub_sync/service/serve.py` to return `create_site(create_app(...),
  web_directory=None)`. Keep `main()` serving the returned application on port 8000.

**Checkpoint**: `uv run pytest tests/service/test_site.py tests/service/test_http_api.py` passes. The Sync API answers
under `/api` only.

---

## Phase 3: User Story 1 - Open the web interface from a deployment without a token (Priority: P1) 🎯 MVP

**Goal**: A deployment from the image, with `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR=web-ui`, serves a working web
interface at the root of the Sync API address, and requests without a token act as `web-ui` from allowed hosts and the
same origin.

**Independent Test**: [quickstart.md](./quickstart.md) steps 1, 2, 3 and 6: build the image, start
`docker-compose.yml` with the anonymous actor set, open `http://127.0.0.1:8001/`, start a run, and check the audit log,
the start-up log line, and the refusals for another host and another origin.

### Tests for User Story 1

- [X] T010 [P] [US1] Write `tests/service/test_anonymous_access.py` for every row of the resolution table in
  `data-model.md`, with anonymous actor `web-ui` and the default allowed hosts (FR-012 to FR-016):
    - no `Authorization`, no `Origin`, `Host: 127.0.0.1:8001`: `GET /api/me` returns actor `web-ui`
    - `Origin: http://evil.example` or `Origin: null`: 403 with code `cross-origin-refused`
    - `Host: rebound.evil.example:8001`: 401
    - `Host: LOCALHOST:8001`, `Host: [::1]:8001` and `Host: 127.0.0.1` (no port): actor `web-ui`
    - `Host: [::1]:8001` with `Origin: http://[::1]:8001`: actor `web-ui`; with `Origin: http://[::1]:9000`: 403
    - a bearer token that resolves: that token's principal, whatever the `Host` and `Origin`
    - a bearer token that does not resolve, and `Authorization: Basic …`: 401, never `web-ui`
    - anonymous access off: 401 for a request without a token
    - each refusal records an authentication refusal with reason `anonymous-host-not-allowed` or
      `cross-origin-refused`, and a path that starts with `/api`
- [X] T011 [P] [US1] Add start-up tests to `tests/service/test_anonymous_access.py` (FR-017 to FR-019,
  `contracts/settings-and-clients.md`):
    - an actor that is not in `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` raises `ValueError` that names
      `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR` and contains neither the value nor any token
    - `INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS=" , "` raises `ValueError` that names the variable
    - an empty or whitespace-only actor turns anonymous access off
    - a non-administrator actor logs one INFO line naming the actor and the hosts; an administrator actor logs one
      WARNING line that says the actor is an administrator; no log line contains a token
- [X] T012 [P] [US1] Add `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR`, `INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS` and
  `INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY` to the declared names asserted in `tests/service/test_environment_names.py`.
- [X] T013 [P] [US1] Add web interface tests to `tests/service/test_site.py`, using a temporary directory with an
  `index.html` that holds one inline `<script>` and an `assets/app-abc123.js` file (research R3, R4,
  `contracts/http-paths.md`):
    - `GET /`, `/runs` and `/runs/abc` with `Accept: text/html` return `index.html` (FR-001, FR-003)
    - `GET /runs/abc` with `Accept: application/json` returns 404
    - `GET /api/nope` with `Accept: text/html` returns the 404 envelope, never `index.html` (FR-004)
    - `POST /runs` returns 404
    - responses outside `/api` carry `Content-Security-Policy` with the SHA-256 hash of the inline script,
      `frame-ancestors 'none'`, `X-Frame-Options: DENY` and `X-Content-Type-Options: nosniff` (FR-006)
    - `/assets/app-abc123.js` carries `Cache-Control: public, max-age=31536000, immutable`, and `/` carries
      `Cache-Control: no-cache`
    - responses under `/api`, including `/api/docs`, carry none of these headers
    - a directory without `index.html` raises `ValueError` that names `INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY`
    - with `web_directory=None`, `GET /` returns 200 HTML that names the released image, and `GET /runs` returns 404
      with the same page (FR-005)
- [X] T014 [P] [US1] Add `frontend` to `ALLOWED_CONTEXT` in `tests/image/test_build_context.py`.
- [X] T015 [P] [US1] Add tests to `tests/image/test_image_artifact.py` (FR-002, research R5): the runtime image holds
  `/opt/infrahub-sync/web/index.html`, `command -v node` fails in it, no `*.tsx` file and no `frontend/` directory
  exists in it, and the started container serves `/` with `Accept: text/html` as HTML and `/api/version` as JSON.

### Implementation for User Story 1

- [X] T016 [P] [US1] Add `principal_for_actor(actor: str) -> Principal | None` to the `PrincipalResolver` protocol and
  to `EnvironmentPrincipalResolver` in `infrahub_sync/service/auth.py`, and add the frozen dataclass
  `AnonymousAccess(principal: Principal, allowed_hosts: frozenset[str])` (research R7). Done differently: the method is
  on a new `PrincipalDirectory` protocol, because adding it to `PrincipalResolver` broke every test resolver (R7).
- [X] T017 [US1] In `infrahub_sync/service/site.py`, serve the web interface when `web_directory` is set with
  `outer.frontend("/", directory=web_directory, fallback="index.html", check_dir=True)`, and convert the `RuntimeError`
  for a missing directory or `index.html` into a `ValueError` that names `INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY`. When
  `web_directory` is `None`, register `GET /` returning the HTML page that names the released image, and a 404 handler
  that returns the same page for other paths outside `/api` (FR-001, FR-003, FR-005, research R3).
- [X] T018 [US1] In `infrahub_sync/service/site.py`, add a middleware that sets the headers of research R4 on every
  response whose path does not start with `/api`. Compute the `script-src` hashes once, from the inline `<script>`
  blocks of the served `index.html`, when `create_site()` runs (FR-006).
- [X] T019 [US1] In `infrahub_sync/service/settings.py`, read `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR`,
  `INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS` (default `localhost,127.0.0.1,::1`, each entry trimmed and lowercased) and
  `INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY`, with the validation and error messages of `data-model.md` and
  `contracts/settings-and-clients.md`. Follow the existing `validate_settings()` pattern.
- [X] T020 [US1] Change `authenticate()` in `create_app()` in `infrahub_sync/service/app.py` to take
  `anonymous: AnonymousAccess | None` and resolve the principal in the order of research R8: an `Authorization` header
  read from `request.headers` must resolve or the request gets 401; then off gives 401; then an `Origin` of `null` or
  with a host and port that differ from `Host` gives 403 `cross-origin-refused`; then a `Host` name that is not allowed
  gives 401; otherwise the anonymous principal. Parse both headers with `urllib.parse.urlsplit`: `urlsplit("//" +
  host)` for `Host` and `urlsplit(origin)` for `Origin`, so that IPv6 brackets and ports are removed the same way.
  Compare host names in lowercase, and treat a missing port as the scheme's default port. Record each refusal with `record_authentication_refusal()` and the
  reasons `cross-origin-refused` and `anonymous-host-not-allowed` (FR-012 to FR-016).
- [X] T021 [US1] In `build_app()` in `infrahub_sync/service/serve.py`, resolve the anonymous actor through
  `principal_for_actor()`, raise the `ValueError` of FR-017 for an unknown actor, build `AnonymousAccess`, pass it to
  `create_app(anonymous=...)`, pass the web directory setting to `create_site()`, and log the start-up line of
  `contracts/settings-and-clients.md` with structlog: INFO for a non-administrator, WARNING for an administrator
  (FR-018, FR-019). Done with the standard `logging` module, as the rest of the service does, and a handler in
  `serve.main()` (R7).
- [X] T022 [US1] Add a `web` stage to `Dockerfile` as described in research R5: `FROM --platform=$BUILDPLATFORM
  node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee`, `corepack enable`,
  copy the package and workspace files, `pnpm install --frozen-lockfile` with a cache mount, copy the sources,
  `pnpm build`. In the runtime stage, copy `frontend/dist` to `/opt/infrahub-sync/web` owned by root, and set
  `ENV INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY=/opt/infrahub-sync/web` (FR-002).
- [X] T023 [US1] Add `frontend` to `.dockerignore`, and exclude `frontend/node_modules`, `frontend/dist` and every
  path under `frontend/vendor/infrahub` except `frontend/vendor/infrahub/frontend/packages/ui` (research R5).
- [X] T024 [US1] Add `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR` and `INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS`, with the
  comments shown in `contracts/settings-and-clients.md`, to the `sync-api` environment in `docker-compose.yml`. Then
  copy `docker-compose.yml` over `infrahub_sync/testcontainers/docker-compose.yml`, which must stay byte-equal
  (`tests/testcontainers/test_packaging.py`).

**Checkpoint**: `uv run pytest tests/service/test_site.py tests/service/test_anonymous_access.py
tests/service/test_environment_names.py` and `uv run pytest -m docker tests/image` pass. Quickstart steps 1, 2, 3 and
6 give the expected results.

---

## Phase 4: User Story 2 - Keep the CLI and other clients working after the API moves (Priority: P1)

**Goal**: Every client in this repository keeps reading `INFRAHUB_SYNC_API_URL` as the address without `/api` and adds
the prefix itself, and the compose health checks pass.

**Independent Test**: [quickstart.md](./quickstart.md) step 4, and `uv run invoke dev.smoke` against the development
stack built from this feature (SC-002).

### Tests for User Story 2

- [X] T025 [P] [US2] Change the mock transports in `tests/client/` that match paths such as `request.url.path ==
  "/version"` to match `/api/version` and the other paths with the `/api` prefix, and add a test in
  `tests/client/test_client.py` that `SyncClient("http://127.0.0.1:8001", ...)` sends `GET /api/version` first
  (FR-008).
- [X] T026 [P] [US2] Change the mock transports in `tests/cli/` to match the `/api` paths.
- [X] T027 [P] [US2] Change every root path in `tests/dev_stack/smoke/` to its `/api` path, then search the folder
  for other root paths of Sync API routes:
    - the `httpx` clients built from `settings.sync_api_url`
    - the recorded path in `test_evidence.py:51`, which becomes `/api/configs`
    - `client.get("/version")` in `test_config_lifecycle.py:277` and the expected request `("GET", "/version", 200)`
      in `test_config_lifecycle.py:317`
    - the `/openapi.json` readiness URL in `conftest.py:112`, which becomes `/api/openapi.json`
- [X] T028 [P] [US2] Change the probe in `wait_for_api()` in `tests/image/conftest.py` to request
  `http://127.0.0.1:8000/api/version`, and its messages to name `GET /api/version`.

### Implementation for User Story 2

- [X] T029 [US2] Add `API_PATH_PREFIX = "/api"` to `infrahub_sync/client/__init__.py`, and build the `httpx.Client` in
  `infrahub_sync/client/client.py` with `base_url=f"{url}{API_PATH_PREFIX}"`. Keep the validation of
  `INFRAHUB_SYNC_API_URL` unchanged (FR-008, research R9).
- [X] T030 [P] [US2] Change `status_url` in `tasks/dev.py` (line 635) to `f"{settings.sync_api_url}/api/status"`.
- [X] T031 [P] [US2] Change the `sync-api` health check to request `http://127.0.0.1:8000/api/version` in
  `docker-compose.yml` (line 263) and `development/docker-compose.yml` (line 257) (FR-009). Then copy
  `docker-compose.yml` over `infrahub_sync/testcontainers/docker-compose.yml`, which must stay byte-equal
  (`tests/testcontainers/test_packaging.py`).
- [X] T032 [P] [US2] Remove the `rewrite` option from the `/api` proxy in `frontend/vite.config.ts`, so that the
  development server keeps the prefix (FR-010). Check it by running `pnpm dev` against `uv run invoke dev.api` and
  requesting `http://127.0.0.1:3000/api/version`, as quickstart step 8 describes.
- [X] T033 [P] [US2] Change the `codegen:openapi` script in `frontend/package.json` to read
  `${INFRAHUB_SYNC_API_URL:-http://127.0.0.1:8001}/api/openapi.json`, run it against a local Sync API, and confirm that
  `frontend/src/api/types.generated.ts` does not change.

**Checkpoint**: `uv run invoke tests.tests-unit` passes. `uv run invoke dev.smoke` passes against the development
stack, and `uv run infrahub-sync whoami` works with `INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001`.

---

## Phase 5: User Story 3 - Use the web interface with a personal token (Priority: P2)

**Goal**: With anonymous access off, the web interface shows a token page, keeps an accepted token across reloads and
tabs, and deletes it on **Forget token**.

**Independent Test**: [quickstart.md](./quickstart.md) step 5, and `pnpm test` in `frontend/`.

### Tests for User Story 3

- [X] T034 [P] [US3] Write `frontend/src/api/token.test.ts`: read returns `null` when nothing is stored, write then
  read returns the token under the key `infrahub-sync.api-token`, delete removes the key, and a `localStorage` that
  throws on every access makes read return `null` and write and delete do nothing (research R11, R12).
- [X] T035 [P] [US3] Write `frontend/src/components/token-gate.test.tsx` for the four states of research R12, with a
  mocked `fetch` for `GET /api/me`:
    - no token stored: the page names `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR`, links to the documentation and shows the
      token field (FR-020)
    - a pasted token that gets 401: the refusal is shown and `localStorage` holds no token (FR-022)
    - a pasted token that gets 200: the token is stored and the gate calls its accepted callback (FR-021)
    - a stored token that gets 401: the page says the stored token was refused and offers **Replace** and
      **Forget token** (FR-025)
- [X] T036 [P] [US3] Write `frontend/src/routes/__root.test.tsx` for the top bar, with a mocked `fetch` for
  `GET /api/me` (FR-023, FR-024):
    - with a stored token, **Forget token** is offered; selecting it removes `infrahub-sync.api-token` from
      `localStorage`, and the next request carries no `Authorization` header
    - with no stored token, **Forget token** is not offered
    - a `storage` event for the key `infrahub-sync.api-token` clears the query cache, so `GET /api/me` is requested
      again

### Implementation for User Story 3

- [X] T037 [P] [US3] Create `frontend/src/api/token.ts` with `readToken()`, `writeToken(token)` and `deleteToken()`
  over `localStorage["infrahub-sync.api-token"]`, each wrapped in `try`/`catch` (research R11).
- [X] T038 [US3] In `frontend/src/api/client.ts`, add an `onRequest` middleware to `fetchClient` that sets
  `Authorization: Bearer <token>` when `readToken()` returns a token (FR-021).
- [X] T039 [US3] Create `frontend/src/components/token-gate.tsx`. It explains that requests without a token are
  refused, names `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR`, links to
  `https://docs.infrahub.app/sync/compose-deployment#open-the-web-interface` (research R13), and offers a token field. On submit it
  calls `GET /api/me` with the pasted token in the `Authorization` header, stores the token with `writeToken()` only on
  200, and then clears the query cache. When a token was already stored, it shows that the stored token was refused
  and offers **Replace** and **Forget token** (FR-020, FR-022, FR-025).
- [X] T040 [US3] In `frontend/src/routes/__root.tsx`, render `TokenGate` in place of the page when `meQuery()` fails
  with 401 (line 132), add **Forget token** to the Sync service details in the top bar when a token is stored, which
  calls `deleteToken()` and clears the query cache, and add a `storage` event listener that clears the query cache when
  the key `infrahub-sync.api-token` changes in another tab (FR-023, FR-024). Do not name the actor in any new text.
- [X] T041 [US3] Update `frontend/README.md`: the `/api` paths, `pnpm test`, and that `INFRAHUB_SYNC_API_TOKEN` set for
  the development server replaces a stored token (research R11).

**Checkpoint**: `cd frontend && pnpm check && pnpm typecheck && pnpm test && pnpm build` passes. Quickstart step 5
gives the expected results.

---

## Phase 6: Polish and cross-cutting work

**Purpose**: Documentation, release note and the full check.

- [X] T042 [P] Update `docs/docs/compose-deployment.mdx` with an "Open the web interface" section and the items of
  FR-026: the `/api` paths, the anonymous access setting and that it applies to every client without a token, the
  allowed hosts setting and when to add a host name, run ownership when a person switches between the anonymous
  principal and a personal token, and that a stored token stays in the browser until **Forget token**.
- [X] T043 [P] Update `docs/docs/reference/sync-http-api.mdx` with the `/api` prefix of every route, the new
  `not-found`, `method-not-allowed` and `cross-origin-refused` envelopes, and the resolution rules for requests without
  a token (FR-026).
- [X] T044 [P] Update `docs/docs/development-stack.mdx` with the `/api` paths of `dev.api` and the Vite proxy that keeps
  the prefix.
- [X] T045 [P] Create `changelog/421.changed.md` with `uv run --extra dev towncrier create`, stating the items of
  FR-027: every route moved under `/api` with no aliases, `/api/version` and `/api/status` for health checks outside
  the compose files, audit log paths that now start with `/api`, and that rolling back to an earlier image requires the
  old paths.
- [X] T046 Run `uv run invoke docs.generate`, `uv run invoke format` and `uv run invoke lint`, and fix every finding in
  the changed files.
- [ ] T047 Partly done: `tests.tests-unit` (8331 passed), `pytest -m docker tests/image` and the CLI checks pass. Not
  run: `uv run invoke dev.smoke`, which needs the full development stack. Run `uv run invoke tests.tests-unit`, `uv run pytest -m docker tests/image`, `uv run invoke dev.smoke`, and
  the CLI checks `uv run infrahub-sync --help`, `uv run infrahub-sync configs --help` and
  `uv run infrahub-sync runs --help`.
- [ ] T048 Partly done against the root compose file with the locally built image, on port 8011: steps 1, 2 (start-up
  line, `/api/me`, headers, page reload), 3, 4, 5 except the removed-principal case (covered by `token-gate.test.tsx`),
  and 6 pass, steps 2 and 5 in headless Chromium. Not run: starting a run and reading its audit record, the liveness
  check by stopping `sync-worker` (covered by `test_site.py`), and step 7 by hand (covered by `test_site.py`). Run every
  step of [quickstart.md](./quickstart.md), including step 7 (no web directory) and the liveness
  check in step 2, and record the results in the pull request.

---

## Dependencies and execution order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on nothing in Phase 1. Blocks every story.
- **User Story 1 (Phase 3)**: depends on Phase 2. T022 (Dockerfile) depends on T001 (pnpm hash) and T004 (submodule
  in CI) for the CI build.
- **User Story 2 (Phase 4)**: depends on Phase 2 only. It must merge together with Phase 2, because Phase 2 breaks the
  CLI and the health checks.
- **User Story 3 (Phase 5)**: depends on Phase 2 and T002 (Vitest). It is tested in a browser against an image or a
  development server from US1 and US2, but its code does not depend on theirs.
- **Polish (Phase 6)**: depends on every story it documents.

### Order inside each story

- Tests first, and confirm they fail.
- US1: T016 before T020 and T021; T019 before T021; T017 before T018.
- US2: T029 before T025 can pass.
- US3: T037 before T038, T039 and T040; T039 before T040.

### Parallel opportunities

- Phase 1: T001 to T004 all run in parallel.
- Phase 2: T005 and T006 in parallel.
- After Phase 2, US1, US2 and US3 can be built in parallel by different people: they change different files, except
  `docker-compose.yml` (T024 in US1, T031 in US2) and `frontend/package.json` (T002 in Phase 1, T033 in US2).
- US1 tests T010 to T015 in parallel; T016 in parallel with T017.
- US2 tests T025 to T028 in parallel; T030 to T033 in parallel.
- US3 tests T034, T035 and T036 in parallel.
- Phase 6: T042 to T045 in parallel.

## Parallel example: User Story 1

```text
Tests, in parallel:
  T010 resolution table in tests/service/test_anonymous_access.py
  T013 web interface tests in tests/service/test_site.py
  T014 ALLOWED_CONTEXT in tests/image/test_build_context.py
  T015 image content in tests/image/test_image_artifact.py

Then, in parallel:
  T016 principal_for_actor() and AnonymousAccess in infrahub_sync/service/auth.py
  T017 app.frontend() in infrahub_sync/service/site.py
```

## Parallel example: User Story 2

```text
Tests, in parallel: T025 tests/client, T026 tests/cli, T027 tests/dev_stack/smoke, T028 tests/image/conftest.py
Then T029 infrahub_sync/client/client.py, and in parallel T030 tasks/dev.py, T031 compose files,
T032 frontend/vite.config.ts, T033 frontend/package.json
```

## Implementation strategy

### First release (Phases 1 to 4)

1. Phase 1 and Phase 2.
2. Phase 4 (US2), so that the CLI, the health checks and the smoke suite work again.
3. Phase 3 (US1).
4. Validate with quickstart steps 1 to 4 and 6 to 8. This covers the need in issue #421.

US1 alone is not a releasable increment, because the `/api` move in Phase 2 breaks clients until US2 is done.

### Then

5. Phase 5 (US3): the token page for deployments without anonymous access.
6. Phase 6: documentation, release note and the full check. The pull request states that the change lands before
   3.0.0 (plan, pull request notes).
