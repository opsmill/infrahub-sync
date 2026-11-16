# Implementation Plan: Open the web interface from the address of the Sync API

**Branch**: `017-serve-web-interface` | **Date**: 2026-11-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/017-serve-web-interface/spec.md`, issue
[#421](https://github.com/opsmill/infrahub-sync/issues/421).

## Summary

The Sync service serves the built web interface at the root of its address and every Sync API
route under `/api`. An outer Starlette application mounts the existing FastAPI application at
`/api`, forwards its lifespan, and serves the built files through one catch-all route with an
`index.html` fallback for page paths. A person enters a Sync API token in the web interface, which
keeps it for the browser tab and sends it as a bearer header. The CLI and Python client add `/api`
to the configured address. The image gains a Node build stage, digest-pinned, whose output is
copied to `/opt/infrahub-sync/web`; no Node runtime reaches the runtime image. Research and
evidence: [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.11-3.13 for the service (the service is excluded from the 3.10
profile); TypeScript with React 19 and Vite for `frontend/`.

**Primary Dependencies**: FastAPI 0.141.1 and Starlette 1.9.0 (already in the `service` extra),
httpx 0.28.1, uvicorn; TanStack Router and Query, openapi-fetch, `@infrahub/ui` from the
`frontend/vendor/infrahub` submodule. No new Python or npm dependency.

**Storage**: None added. The built web interface is a read-only directory in the image. The token
is held in the browser's `sessionStorage` for the tab.

**Testing**: pytest (`uv run invoke tests.tests-unit`) with `starlette.testclient.TestClient` and
`httpx.MockTransport`; `tests/image/` against a built image; opt-in live tiers (`tests/dev_stack`,
`tests/compose`, `tests/integration`). Frontend: `pnpm check`, `pnpm typecheck`, `pnpm build` (no
test runner exists).

**Target Platform**: The Sync image on linux/amd64 and linux/arm64; current desktop browsers.

**Project Type**: Web service with a command-line client and a single-page web interface.

**Performance Goals**: The entry page and its assets load from the same service as the API; total
size of the built web interface about 1.1 MB (SC-007 allows less than 10 MB of image growth).

**Constraints**:

- The OpenAPI path keys stay unprefixed (`/runs`), with `servers: [{"url": "/api"}]`.
- The service starts with no built web interface (unit tests, base-install CI job, pip install).
- No token in the built files and none in the service environment for the browser.
- `frontend/` is owned by another maintainer: change only what the need requires.
- Every external base image pinned by digest; `.dockerignore` stays an allowlist.

**Scale/Scope**: 14 route decorators move under `/api` by one mount; 4 page paths; about 25
repository files outside tests and documentation.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Assessment |
|---|---|
| I. Read-only and dry-run by default | Pass. No new mutating behaviour. The web interface already starts verify, apply and cancel; they keep requiring a token with the same permissions and the approval rule. |
| II. Idempotency and safety | Pass. Routing only; requests keep their `Idempotency-Key`. A 401 in the browser never retries a write. |
| III. Adapter symmetry | Not affected. |
| IV. Type safety and explicit contracts | Pass. New Python code typed; `ty` clean without overrides. The HTTP contract change is recorded in [contracts/http-routing.md](contracts/http-routing.md) and an ADR. |
| V. Test discipline | Pass. New unit tests for the outer application, the 404/405 envelope, the client base address and the CLI message; updated mock and live tests. |
| VI. Security, secrets, input boundaries | Pass with an accepted risk. Tokens are never logged, printed or bundled; path traversal is refused; the client never prints a response body. Accepted risk (decision D-2): a script in the page can read the token for the tab. |
| VII. Simplicity | Pass. No new dependency; one module and one factory added; a mount instead of rewriting 14 routes and 265 test literals. |
| Command-line sanity and docs | Pass. `--help` texts updated and regenerated; user docs updated in the same change. |

No violation; Complexity Tracking is empty.

Post-design re-check: pass. The design adds `infrahub_sync/service/web.py`, one factory, one setting
and one exception class, and changes no permission.

## Project Structure

### Documentation (this feature)

```text
dev/specs/017-serve-web-interface/
├── spec.md
├── plan.md                 # this file
├── research.md             # R-1 to R-12, with evidence
├── data-model.md           # setting, built directory, token store, client address, error
├── quickstart.md           # validation scenarios
├── contracts/
│   ├── http-routing.md     # addresses, answers, headers of the service
│   ├── cli-client.md       # client address rule and the not-a-sync-api error
│   └── web-token.md        # token entry, storage, header, 401 handling
├── checklists/requirements.md
└── tasks.md                # produced by speckit-tasks
```

### Source Code (repository root)

```text
infrahub_sync/
├── service/
│   ├── web.py              # NEW: create_site(api, directory); catch-all route; lifespan forwarding
│   ├── serve.py            # build_service() factory; main() serves it
│   ├── settings.py         # WEB_DIRECTORY_ENV, web_directory()
│   └── app.py              # HTTPException handler -> error envelope (404/405)
├── client/
│   ├── client.py           # base address + /api; NotSyncAPIError detection
│   └── errors.py           # NotSyncAPIError (or the module that holds SyncClientError)
├── cli.py                  # render not-a-sync-api; help text of --api-url
└── testcontainers/docker-compose.yml   # health check (byte-identical to the root file)

frontend/
├── src/api/token.ts                    # NEW: token store for the tab
├── src/components/token-entry.tsx      # NEW: token entry screen
├── src/main.tsx                        # gate before RouterProvider
├── src/api/client.ts                   # bearer header, 401 handling
├── src/api/queries.ts                  # 401 wording
├── src/features/runs/plan-download.tsx # header, 401, cache cleared with the token
├── src/features/runs/run-sections.tsx  # artifact download through fetch
├── src/routes/__root.tsx               # "Remove token" in StatusMenu
├── vite.config.ts                      # no rewrite, no token
├── package.json                        # codegen reads /api/openapi.json
└── README.md

Dockerfile, .dockerignore               # web stage, runtime copy, ENV
docker-compose.yml, development/docker-compose.yml   # health checks
tasks/dev.py, tasks/docs.py             # factory, /api/status, no frontend token, build-input hash
.github/workflows/{ci-docker-image,workflow-nightly-e2e}.yml, .github/file-filters.yml
tests/service/test_web_site.py (NEW), tests/client/, tests/cli/, tests/image/, tests/dev_stack/,
tests/compose/, tests/integration/, tests/test_workflow_contracts.py
docs/docs/compose-deployment.mdx (new section "Open the web interface"), reference/sync-http-api.mdx,
quickstart-compose.mdx, development-stack.mdx, operations/day-2-operations.mdx, tutorials (3),
README.md, dev/adr/0043-*.md, changelog/421.added.md, changelog/421.changed.md
```

**Structure Decision**: Existing single repository layout. The outer application lives in a new
module of `infrahub_sync/service/` so `create_app` and `build_app` keep returning the API
application that the unit tests use with unprefixed paths.

## Implementation notes

### Service

- `create_site(api: FastAPI, directory: Path | None) -> Starlette`: routes
  `[Mount("/api", app=api), Route("/{path:path}", web_endpoint, methods=[all])]`, lifespan
  `async with api.router.lifespan_context(api): yield`. The decision order of the catch-all route is
  R-3; answers are fixed in [contracts/http-routing.md](contracts/http-routing.md).
- The 404 envelope outside `/api` uses the existing `ErrorEnvelope` with code `not-found` and the
  message "the Sync API is served under /api".
- Every answer of the catch-all route carries the security headers of research R-3a. The
  `Content-Security-Policy` is computed once in `create_site` from the inline scripts of the built
  `index.html` (FR-034, critique E3).
- A test asserts that the liveness reconciler task starts and stops through `build_service()`
  (critique E1), and a test asserts the policy contains the hash of an inline script of a test
  `index.html`.
- `build_service()` in `serve.py` = `create_site(build_app(), web_directory())`; `main()` runs it;
  `tasks/dev.py` `API_FACTORY` points at it.
- The `HTTPException` handler on the API application (R-2) keeps the exception headers.

### Client and CLI

- Address rule and error in [contracts/cli-client.md](contracts/cli-client.md).
- `tasks/docs.py:58` and `cli.py:151`, `:225` describe `INFRAHUB_SYNC_API_URL` as "the address of the
  Sync service; the client adds /api". Regenerate `docs/docs/reference/cli.mdx` and
  `python-api.mdx`.

### Image and CI

- R-8 for the stage, R-9 for the build-input hash, R-10 for CI.
- `tests/image/test_build_context.py` `ALLOWED_CONTEXT` gains `frontend`; `tests/image/conftest.py`
  `READ_ONLY_ROOTS` gains `/opt/infrahub-sync/web`; `test_image_artifact.py` asserts `index.html`
  and `assets/` exist and are readable by uid 10001, `node`, `npm`, `pnpm` and `corepack` are not on
  `PATH`, and no `node_modules` exists in the image.

### Web interface

- [contracts/web-token.md](contracts/web-token.md) and R-12. Keep every change local to the listed
  files and follow `biome.json`.
- Keep the token rules (trim, store, clear only for a 401 on the held token) in small pure
  functions in `token.ts` and `client.ts`. `frontend/` has no test runner, so the browser steps of
  quickstart scenario 3 are required evidence in the implementation report (critique E6).
- The Node version of the `web` stage and of `.github/workflows/workflow-linter.yml` change
  together (critique E8).
- The header stops displaying the token's actor (spec Clarifications, FR-019): the "Requests
  sent as" row, the actor badge on the status button and the actor in its `aria-label`. The
  `GET /me` query stays, because the header uses it to detect a refused token.

### Records

- ADR `dev/adr/0043-the-web-interface-is-served-at-the-root-and-the-api-under-api.md`: the mount,
  the token for the tab, the client adding `/api`. No internal links.
- `changelog/421.added.md`: the web interface is served by the Sync service; a person enters a
  token. `changelog/421.changed.md`: every Sync API route moved under `/api`; the CLI and the
  service must be upgraded and rolled back together; an older CLI prints `error: compatibility`
  with no server version; scripts that call the API add `/api`; the operation recorded for a
  refused token now carries `/api`.
- The "Open the web interface" section states that an operator gives each person their own token
  through `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` (FR-031, critique P2).
- User docs per R-11; `docs/docs/development-stack.mdx` "Run the web interface" no longer sets a
  token.

### Commands

```bash
uv sync --extra dev --extra prefect --extra service
git submodule update --init frontend/vendor/infrahub
uv run invoke format
uv run invoke lint
uv run ruff check .
uv run invoke tests.tests-unit
(cd frontend && pnpm install --frozen-lockfile && pnpm check && pnpm typecheck && pnpm build)
uv run invoke docs.generate
docker build -t infrahub-sync:web-check .
uv run pytest tests/image -m image   # marker as declared in tests/image; see quickstart.md
```

## Complexity Tracking

No constitution violation to justify.
