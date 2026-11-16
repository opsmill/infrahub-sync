# Tasks: Open the web interface from the address of the Sync API

**Input**: Design documents from `dev/specs/017-serve-web-interface/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: Required. The constitution (principle V) and the plan require tests for every behaviour
change. `frontend/` has no test runner; its checks are `pnpm check`, `pnpm typecheck`,
`pnpm build`, and the browser steps of quickstart scenario 3.

**Conventions for every task**:

- Read the research item named in the task before editing; it holds the file and line evidence.
- Python: typed, Ruff-clean, specific exceptions, `structlog` for logs, no secret in logs.
- Run the targeted tests of the task with `uv run pytest <paths> -q` before marking it done.
- Commit with explicit paths (`git commit -- <paths>`); several sessions share the index. Never
  stage `frontend/vendor/infrahub` (its working tree shows unrelated changes).
- Interface text: sentence case, no period on titles and buttons, never name a user.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 to US4 from [spec.md](spec.md)

---

## Phase 1: Setup

- [ ] T001 Confirm the baseline: `git submodule status frontend/vendor/infrahub` shows the recorded revision without a leading `-` (run `git submodule update --init frontend/vendor/infrahub` if it does); `cd frontend && pnpm install --frozen-lockfile && pnpm check && pnpm typecheck && pnpm build` passes; `uv run invoke tests.tests-unit` passes. Record the unit test count in `dev/specs/017-serve-web-interface/opsmill-implement-report.md` notes (no code change).

---

## Phase 2: Foundational (blocks every story)

**Purpose**: Move the Sync API under `/api`, serve the built web interface at the root, and move
every in-repository caller that would otherwise break. After this phase the service answers
[contracts/http-routing.md](contracts/http-routing.md).

- [ ] T002 [P] Add `WEB_DIRECTORY_ENV = "INFRAHUB_SYNC_WEB_DIRECTORY"` and `web_directory() -> Path | None` to `infrahub_sync/service/settings.py` per [data-model.md](data-model.md) "Web directory setting" and research R-5: unset or empty returns `None`; a path whose resolved directory holds no regular file `index.html` logs one `structlog` warning with the path and returns `None`; return the resolved path otherwise; do not touch `validate_settings()`. Update the declared-names assertion in `tests/service/test_environment_names.py` (around line 39). Add tests in `tests/service/test_settings_web_directory.py` for unset, empty, missing directory, directory without `index.html`, and a valid directory (use `tmp_path`, `monkeypatch`).
- [ ] T003 [P] Register a `starlette.exceptions.HTTPException` handler in `create_app` in `infrahub_sync/service/app.py` (next to the handlers at lines 361-392) that returns the existing error envelope through `_error_content` (lines 90-98) with code `not-found` for 404, `method-not-allowed` for 405 and `http-error` otherwise, `message` from the exception detail, `status` the status code, and the exception's headers kept (the `Allow` header of a 405). Research R-2. Add tests in `tests/service/test_http_errors.py` using `TestClient(create_app(...))` as the helper in `tests/service/test_http_api.py:212` does: `GET /nope` → 404 envelope; `POST /version` → 405 envelope with `Allow`; an existing route still answers as before.
- [ ] T004 Create `infrahub_sync/service/web.py` with `create_site(api: FastAPI, directory: Path | None) -> Starlette`: routes `[Mount("/api", app=api), Route("/{path:path}", <endpoint>, methods=["GET","HEAD","POST","PUT","PATCH","DELETE","OPTIONS"])]`; lifespan `async with api.router.lifespan_context(api): yield`; the endpoint follows the six-row decision order of [contracts/http-routing.md](contracts/http-routing.md) "Answers outside /api" exactly (research R-3); path lookup through `StaticFiles(directory=<resolved>, check_dir=False).lookup_path` and `file_response` (research R-3 for why, and the `/private/var` resolution note); `Cache-Control: no-cache` on `index.html`, `public, max-age=31536000, immutable` on `assets/*`; the 404 envelope built with the same `ErrorEnvelope` model and `_error_content` helper as `app.py` (import, do not copy), code `not-found`, message `the Sync API is served under /api`; the plain-text 404 for a missing web interface; and the security headers of research R-3a on every answer of the endpoint, with the `Content-Security-Policy` computed once in `create_site` from the inline `<script>` elements (no `src`) of the built `index.html` (base64 SHA-256 of the exact content, `'sha256-...'`). Depends on T003 only for the shared envelope import.
- [ ] T005 Add tests in `tests/service/test_web_site.py` for `create_site` with a `tmp_path` directory holding `index.html` (with one inline script), `assets/app-abc.js`, and a file outside the directory: every row of [contracts/http-routing.md](contracts/http-routing.md) "Answers outside /api" (including `HEAD`, `POST /runs`, `GET /api` without a slash, `/assets/missing.js` with `Accept: text/html`, `/favicon.ico` with `Accept: image/*` when the file exists); the traversal paths `/%2e%2e/secret.txt`, `/assets/%2e%2e/%2e%2e/secret.txt`, `/assets/..%2f..%2fsecret.txt` never return the outside file; `directory=None` rows; the cache headers; the security headers, and that the CSP holds the SHA-256 of the test inline script; `GET /api/openapi.json` has unprefixed path keys and `servers == [{"url": "/api"}]`; `GET /api/docs` references `/api/openapi.json`; `GET /api/nope` returns the T003 envelope; a lifespan probe: an API app whose lifespan appends to a list runs start and stop through `with TestClient(create_site(...))`. Use a small FastAPI app for routing cases and one case with the real `create_app` (reuse the helper pattern of `tests/service/test_http_api.py:212`).
- [ ] T006 Add `build_service() -> Starlette` to `infrahub_sync/service/serve.py` returning `create_site(build_app(), web_directory())`; `main()` serves `build_service()` instead of `build_app()` and keeps its `TrustStoreError` handling. `build_app` and `create_app` keep returning the API application (research R-4). Add a test in `tests/service/test_serve_site.py` that calls `build_service` with the same factory injection used by `tests/service/test_validation_secret_containment.py:284-290` (extend `build_service` with the same keyword arguments it passes through to `build_app` if needed) and asserts `/api/version` answers and that the liveness reconciler loop of `create_app`'s lifespan runs through the outer application (critique E1): inject a reconciler stub whose `reconcile_once` records a call and whose `cadence_seconds` is small.
- [ ] T007 Point `API_FACTORY` in `tasks/dev.py` (line 116) at `infrahub_sync.service.serve:build_service`; change the readiness poll at line 705 to `f"{settings.sync_api_url}/api/status"` and the message at line 822 to name `/api`; update `tests/dev_stack/test_deps_mode.py` line 216 (factory) and lines 428, 442, 453 (`http://127.0.0.1:8001/api/status`). Research R-4, R-11.
- [ ] T008 [P] Change the `sync-api` health check from `/version` to `/api/version` in `docker-compose.yml` (line 285), `infrahub_sync/testcontainers/docker-compose.yml` (line 285, must stay byte-identical to the root file) and `development/docker-compose.yml` (line 278). Run `uv run pytest tests/compose/test_compose_contract.py -q` (offline contract tests) and fix any pinned health check text there.

**Checkpoint**: `uv run invoke tests.tests-unit` passes; `uv run python -c "from infrahub_sync.service.serve import build_service"` imports.

---

## Phase 3: User Story 1 - Open the web interface from a deployment (P1) 🎯 MVP

**Goal**: The released image contains the built web interface and serves it at the root.

**Independent test**: quickstart scenario 5, then scenario 6 (`/runs/<id>` opened directly).

- [ ] T009 [US1] Add the `web` stage to `Dockerfile` after the Python `build` stage and before `runtime` (research R-8 and the placement constraint): `FROM node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS web` with a comment in the style of lines 1-6 (tag for the reader, digest is the authority); `WORKDIR /src/frontend`; `RUN corepack enable`; copy exactly `frontend/.npmrc`, `package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `index.html`, `vite.config.ts`, `tsconfig.json`, `tsr.config.json`, `src/` and `frontend/vendor/infrahub/frontend/packages/ui` (to the same relative path); a guard `RUN test -f vendor/infrahub/frontend/packages/ui/package.json || { echo "frontend/vendor/infrahub is missing: run git submodule update --init frontend/vendor/infrahub" >&2; exit 1; }` before install; `pnpm install --frozen-lockfile` with a pnpm store cache mount; `pnpm build`. In `runtime`: `COPY --from=web --chown=0:0 /src/frontend/dist /opt/infrahub-sync/web` and `INFRAHUB_SYNC_WEB_DIRECTORY=/opt/infrahub-sync/web` added to the existing `ENV` block. Update the comment at lines 79-81 about `BUILD_INPUTS` (inputs now include the web interface). Check `.npmrc` exists in `frontend/` first; drop it from the list if it does not.
- [ ] T010 [US1] Extend `.dockerignore` (allowlist) with one `!frontend/<path>` line per path copied in T009 and `!frontend/vendor/infrahub/frontend/packages/ui`, and add `**/node_modules` and `**/dist` to the exclusion block (research R-8). Do not add `!frontend` alone.
- [ ] T011 [US1] Update `tests/image/`: `test_build_context.py` `ALLOWED_CONTEXT` gains `frontend`; `conftest.py` `READ_ONLY_ROOTS` (lines 44-53) gains `/opt/infrahub-sync/web`, and `conftest.py:280` calls `/api/version` (or the address it calls) instead of the root route; `test_image_artifact.py` gains assertions that `/opt/infrahub-sync/web/index.html` and at least one `assets/*.js` and `assets/*.css` exist and are readable by uid 10001, that `node`, `npm`, `pnpm` and `corepack` are not on `PATH` (same form as the existing PATH assertion around line 236), that no `node_modules` directory exists under `/opt` or `/usr/local/lib`, and that the image's `INFRAHUB_SYNC_WEB_DIRECTORY` is `/opt/infrahub-sync/web`; confirm `test_version_policy.py` accepts the new digest-pinned `FROM` and the stage order. Run the offline tests of `tests/image/` that do not need a built image.
- [ ] T012 [US1] Build the image: `docker build -t infrahub-sync:web-check .`, then run `tests/image/test_image_artifact.py` against that tag the way `.github/workflows/ci-docker-image.yml` (around line 183) runs it. Record the commands, the image size before and after (`docker image inspect --format '{{.Size}}'` of a build of `feature/v3-develop` or the size reported in `tests/image` if no earlier build exists) and the results in `dev/specs/017-serve-web-interface/opsmill-implement-report.md`. If Docker is not available, mark the task blocked with the reason, do not mark it done.

**Checkpoint**: the image serves `/` and `/api/version` (quickstart scenario 5).

---

## Phase 4: User Story 2 - Act in the web interface with one's own token (P1)

**Goal**: A person enters a token; every request carries it; 401 returns to the entry screen.

**Independent test**: quickstart scenario 3 browser steps.

Read [contracts/web-token.md](contracts/web-token.md) and research R-12 first. Follow
`frontend/biome.json` (line width 100, double quotes, organised imports).

- [ ] T013 [P] [US2] Create `frontend/src/api/token.ts`, a module store in the pattern of `frontend/src/lib/theme.ts`: `getToken()`, `setToken(value)` (trims), `clearToken()`, `useToken()` with `useSyncExternalStore`, and `onTokenCleared(listener)` for caches outside React Query; storage key `infrahub-sync.api-token` in `sessionStorage` inside `try`/`catch` with an in-memory fallback. Keep the rules in small exported pure functions (critique E6).
- [ ] T014 [US2] Change `frontend/src/api/client.ts`: an `onRequest` middleware sets `Authorization: Bearer <getToken()>` when a token is held and the request has no `Authorization` header; extend the `onResponse` middleware so a 401 whose request carried the held token calls `clearToken()` and `queryClient.clear()` before the existing envelope check; export that 401 step as a function (for example `handleUnauthorized(request, response)`) for raw `fetch` callers. 403 changes nothing. Depends on T013.
- [ ] T015 [US2] Create `frontend/src/components/token-entry.tsx`: the screen of [contracts/web-token.md](contracts/web-token.md) with exactly its texts; a `type="password"` input using the input classes and `Field` layout of `frontend/src/features/runs/form.tsx:9-48`; on submit, trim, skip when empty, call `fetchClient.GET("/me", { headers: { Authorization: "Bearer <typed>" } })`; 200 → `setToken`; 401 → "The Sync API refused the token."; any other status or a thrown error → "The Sync service did not answer. Check that it is running, then try again."; never render a field of the `/me` answer; show errors with the existing `Notice tone="danger"` component. Use the page wrapper classes of `frontend/src/routes/__root.tsx` (around lines 386 and 441) so the screen matches the layout. Depends on T013, T014.
- [ ] T016 [US2] Change `frontend/src/main.tsx`: a small `App` component inside `<QueryClientProvider>` renders `<RouterProvider router={router} />` when `useToken()` returns a token and `<TokenEntry />` otherwise (research R-12: the router loads only when `RouterProvider` mounts, so no loader runs without a token). Depends on T015.
- [ ] T017 [P] [US2] Change `frontend/src/features/runs/plan-download.tsx`: the raw `fetch` at line 64 sends `Authorization: Bearer <getToken()>` and passes its response to the exported 401 step of T014; empty the module cache `downloads` (line 114) through `onTokenCleared`. Depends on T013, T014.
- [ ] T018 [P] [US2] Change `frontend/src/features/runs/run-sections.tsx` lines 76-86: replace the `<LinkButton href=... download>` with a `Button` that fetches the artifact with the bearer header, passes the response to the 401 step, builds a `Blob`, and saves it through a temporary `<a download>` element that is removed afterwards; show a failure with the existing error presentation of the section. Keep the visible label. Depends on T013, T014.
- [ ] T019 [P] [US2] Add a "Remove token" item to the existing `StatusMenu` in `frontend/src/routes/__root.tsx` that calls `clearToken()` and `queryClient.clear()`. In the same component, stop displaying the token's actor (FR-019, spec Clarifications): remove the "Requests sent as" row and its explanatory sentence (around lines 282-288), the actor badge on the status button (around lines 224-231) and the actor in the button's `aria-label` (line 218). Keep the `meQuery` call, which detects a refused token (line 131). Depends on T013.
- [ ] T020 [P] [US2] Replace the 401 text of `describeError` in `frontend/src/api/queries.ts` (lines 353-362), which tells developers to set `INFRAHUB_SYNC_API_TOKEN`, with one sentence for any person: "The Sync API refused the token. Enter a token again."
- [ ] T021 [US2] Run `cd frontend && pnpm check && pnpm typecheck && pnpm build`; fix every finding; then `grep -rIl "INFRAHUB_SYNC_API_TOKEN" frontend/dist frontend/src` returns nothing outside comments and the built files contain no token value.

**Checkpoint**: frontend checks pass; browser steps run in Phase 7.

---

## Phase 5: User Story 3 - Keep using the CLI and the Compose deployment (P1)

**Goal**: An unchanged `INFRAHUB_SYNC_API_URL` keeps working; mismatches fail readably.

**Independent test**: quickstart scenario 4.

- [ ] T022 [US3] In `infrahub_sync/client/client.py`, apply the address rule of [contracts/cli-client.md](contracts/cli-client.md) (research R-6): `_service_url` (lines 150-162) keeps its validation, strips trailing `/`, strips one trailing `api` segment and trailing `/` again; `__init__` (lines 113-131) passes `f"{service_url}/api"` as the `httpx` base URL and keeps `service_url` without `/api`. Add parametrized tests in `tests/client/test_client.py` next to lines 137-142 for every row of [data-model.md](data-model.md) "Client address", asserting the request URL seen by `httpx.MockTransport`.
- [ ] T023 [US3] Update every `httpx.MockTransport` path match in `tests/client/test_client.py` (59 lines), `tests/client/test_resume.py` (4), `tests/client/test_wait.py` (5), `tests/cli/test_apply_confirm_left_out.py` (1) and `tests/service/test_prefect_wording.py` (6) from `/<route>` to `/api/<route>` (research R-11: literals are updated, not stripped by a helper). Run those files.
- [ ] T024 [US3] Add `NotSyncAPIError(SyncClientError)` with `operation`, `url` (no query string) and `status` to `infrahub_sync/client/errors.py`, export it where `SyncClientError` is exported (`infrahub_sync/client/__init__.py`, and `tests/client/test_public_surface.py` if it pins the public names). Raise it in `infrahub_sync/client/client.py` under exactly the conditions of [contracts/cli-client.md](contracts/cli-client.md) (critique E2: a 200 answer of `get_artifact` at lines 645-667 is excluded); find the single place where JSON answers are parsed (`_success`, `_request_model`, `_ensure_compatible`, `_raise_response_error`) and add the check there, never storing or logging the body. Add tests in `tests/client/test_not_sync_api.py`: HTML 200 to `/api/version`; HTML 404; `{"detail": "Not Found"}` 404 to `/api/version`; HTML 502 to `list_runs`; `get_artifact` 200 with `text/plain` and a valid digest still succeeds; the body text never appears in `str(error)` or `repr(error)`.
- [ ] T025 [US3] Render `NotSyncAPIError` in `infrahub_sync/cli.py` `_error_fields` (around line 230) as `error: not-a-sync-api`, `operation:`, `address:`, `status:` and the `hint:` line of [contracts/cli-client.md](contracts/cli-client.md), exit 1. Change the help text and the client-input hint that call `INFRAHUB_SYNC_API_URL` "absolute ... URL of the Sync API" (`infrahub_sync/cli.py` lines 151 and 225, `tasks/docs.py` line 58) to "Address of the Sync service, for example http://127.0.0.1:8001. The client adds /api." (keep the client-input hint a complete instruction). Update `SERVICE_URL_HINT` in `tests/cli/test_client_commands.py` (line 2378) and add a CLI test for the not-a-sync-api output with a `MockTransport` that answers HTML.
- [ ] T026 [US3] Update the live-service tests to `/api` (they are opt-in and may not run locally; keep them correct by reading): `tests/dev_stack/smoke/test_service_api.py` (base address around line 149), `tests/dev_stack/smoke/conftest.py:112` (`/openapi.json` probe), the recorded path tuples in `tests/dev_stack/smoke/test_config_lifecycle.py`, `test_netbox_package.py`, `test_run_review.py`, `test_run_completion.py`, `test_schema_drift.py`, `test_cli_client.py` (evidence records `request.url.path`, `evidence.py:47`); `tests/compose/lifecycle.py` (lines 207, 231, 244), `tests/compose/test_lifecycle.py` (122, 355), `tests/compose/test_testcontainers_fixture.py` (164, 266), `test_managed_run.py`, `test_external_store.py`, `test_credential_leaks.py`; `tests/integration/test_managed_write_guard_live.py` (123, 156). Search each file for direct route literals and full addresses; leave calls made through `SyncClient` unchanged. Run `uv run pytest --collect-only -q` on these directories to confirm they import.
- [ ] T027 [US3] Run `uv run invoke docs.generate` and confirm `docs/docs/reference/cli.mdx` and `docs/docs/reference/python-api.mdx` carry the new help text.

**Checkpoint**: `uv run invoke tests.tests-unit` passes.

---

## Phase 6: User Story 4 - Build and develop with the web interface included (P2)

**Goal**: The development build, the development server and CI follow the new layout.

**Independent test**: US4 acceptance scenarios 1 to 5 in [spec.md](spec.md).

- [ ] T028 [P] [US4] Extend the build-input hash in `tasks/dev.py` (`BUILD_INPUT_FILES` at line 89, hash at lines 390-395) per research R-9: also hash every file under the frontend paths copied in T009 (sorted relative paths and bytes, skipping `node_modules` and `dist`) and every file under `frontend/vendor/infrahub/frontend/packages/ui` when present; a missing submodule contributes nothing. Update `tests/dev_stack/test_build_inputs.py` (lines 62-69) and add a test that changing a file under `frontend/src` changes the hash and that a missing submodule still yields a hash.
- [ ] T029 [P] [US4] Change `frontend/vite.config.ts`: delete the `rewrite` line and the `headers` lines and stop reading `INFRAHUB_SYNC_API_TOKEN`; keep `target` and `changeOrigin` (FR-020). Change `frontend/package.json` `codegen:openapi` to `${INFRAHUB_SYNC_API_URL:-http://127.0.0.1:8001}/api/openapi.json`.
- [ ] T030 [P] [US4] In `tasks/dev.py`, remove `FRONTEND_TOKEN_VARIABLE` (line 130), `frontend_token` (lines 894-922), its use in `dev_frontend` (line 941) and the docstring lines about it (935-936); `dev.frontend` prints which configured token label to enter using the existing `bearer_label` helper (lines 522-538), never a token value. Update `tests/dev_stack/test_dev_tasks.py` (remove the two `frontend_token` tests at lines 416-443, update the expected environment and `delenv` lines at 446-465).
- [ ] T031 [P] [US4] Add `submodules: true` to the checkout steps before an image build in `.github/workflows/ci-docker-image.yml` (around line 147) and `.github/workflows/workflow-nightly-e2e.yml` (around lines 48 and 272); add `frontend/**` and `.gitmodules` to `image_inputs` in `.github/file-filters.yml` (lines 84-98). Update `tests/test_workflow_contracts.py` (the exact checkout dictionaries at lines 666-671 and 746-751, and the COPY-sources contract at lines 2056-2067) and run it.
- [ ] T032 [US4] Rewrite the sections of `frontend/README.md` named in research R-12 ("Getting Started" lines 21-23, "Calling the Sync API" lines 44-57, "Building For Production" lines 96-102): the development server forwards `/api` unchanged and adds no token; a person enters a token in the page; the Sync service serves the production build from `INFRAHUB_SYNC_WEB_DIRECTORY`; `codegen:openapi` reads `/api/openapi.json`. Change the sentence after the "Pages" table (lines 83-84) so that `GET /me` checks the token and the top bar no longer displays its actor.

**Checkpoint**: `uv run invoke tests.tests-unit` and the frontend checks pass.

---

## Phase 7: Polish and records

- [ ] T033 [P] Documentation per FR-031 and research R-11: add a section "Open the web interface" to `docs/docs/compose-deployment.mdx` next to "Open the Prefect UI" (around line 537): the address, entering and removing a token, that an operator gives each person their own token through `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`, that the token is kept for the browser tab only, and that the Sync API is under `/api`; document `INFRAHUB_SYNC_WEB_DIRECTORY` in the variables table (around lines 144-146). Update every full address or `curl` of a route to `/api` in `docs/docs/reference/sync-http-api.mdx` (8, and one sentence near the top that every route below is relative to `/api`, with `/api/docs` and `/api/openapi.json`), `docs/docs/compose-deployment.mdx` (6), `docs/docs/quickstart-compose.mdx` (2, plus a pointer to the new section), `docs/docs/development-stack.mdx` (2, and "Run the web interface" around lines 197-216 no longer sets a token), `docs/docs/operations/day-2-operations.mdx` (1), `docs/docs/tutorials/nautobot-to-existing-infrahub.mdx`, `netbox-to-existing-infrahub.mdx`, `netbox-demo-to-infrahub.mdx` (1 each), `README.md` (2). Keep route names in prose relative (`GET /events`), which `tests/test_run_events_docs.py` pins. Use the exact lines listed in the inventory of research R-11; re-find them with `grep -n` because line numbers may have moved. Follow the organization writing rules (plain words, customer perspective, no idioms).
- [ ] T034 [P] Write `dev/adr/0043-the-web-interface-is-served-at-the-root-and-the-api-under-api.md` in the format of the latest records in `dev/adr/` and add it to `dev/adr/README.md`: context (issue 421, page paths collide with routes), decision (mount at `/api`, token for the browser tab, the client adds `/api`, security headers), alternatives (router prefix, `root_path`, session cookie, separate static server), consequences (one breaking address change, CLI and service upgraded together, recorded operation carries `/api`). No internal links (no Slack, Jira or Claude artifact links); the GitHub issue link is allowed only if other records link issues, otherwise name it by number.
- [ ] T035 [P] Add `changelog/421.added.md` (the Sync service serves the web interface at the root of its address; a person enters their own Sync API token, kept for the browser tab) and `changelog/421.changed.md` (every Sync API route moved under `/api`, including `/version`, `/status`, `/docs`, `/openapi.json`; the CLI adds `/api` so `INFRAHUB_SYNC_API_URL` keeps its value; upgrade and roll back the CLI and the service together; an older CLI prints `error: compatibility` with no server version; scripts that call the API add `/api`; the operation recorded for a refused token now starts with `/api`), in the style of `changelog/+sync-api-port-8001.changed.md`.
- [ ] T036 Run the required workflow and fix every finding: `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run invoke tests.tests-unit`, `uv run infrahub-sync --help`, `uv run infrahub-sync configs --help`, `uv run infrahub-sync runs --help`, `uv run rumdl check .`.
- [ ] T037 Run quickstart scenarios 3 and 4 against the development stack (`uv run invoke dev.start`, `INFRAHUB_SYNC_WEB_DIRECTORY=$PWD/frontend/dist uv run invoke dev.api`): every `curl` row, the CLI commands with `INFRAHUB_SYNC_API_URL` with and without `/api`, the not-a-sync-api output against the Vite development server, and the browser steps with the console open (no CSP violation; if a style violation appears, apply the fallback of research R-3a and record it). Record the evidence (commands, status codes, what the browser displayed) in `dev/specs/017-serve-web-interface/opsmill-implement-report.md`. If the development stack or a browser is not available, mark the task blocked with the reason.

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 → Phases 3 to 6 → Phase 7.
- Phase 2: T002 and T003 in parallel; T004 after T003; T005 after T004; T006 after T002 and T004;
  T007 after T006; T008 independent.
- US1 (T009-T012) needs Phase 2. T012 also needs US2's frontend changes to build what will ship,
  so run T012 after T021.
- US2 (T013-T021) needs only the routes of Phase 2 for manual checks; its code is independent.
- US3 (T022-T027) needs Phase 2. T023 after T022. T025 after T024.
- US4 (T028-T032) needs T009 (T028 hashes the paths T009 copies).
- T037 needs every earlier task.

## Parallel Example

```text
After Phase 2:
  US1: T009 → T010 → T011
  US2: T013 → T014 → (T015, T017, T018, T019, T020) → T016 → T021
  US3: T022 → T023; T024 → T025; T026; T027
  US4: T029, T030, T031 in parallel; T028 after T009
```

## Implementation Strategy

1. MVP: Phases 1-2 and US1 give an image that serves the web interface and the API under `/api`.
2. US2 makes the served page usable (token entry); ship US1 and US2 together.
3. US3 keeps the CLI and scripts working; required before release because Phase 2 moves every
   route.
4. US4 and Phase 7 complete the development flow, CI and records.
