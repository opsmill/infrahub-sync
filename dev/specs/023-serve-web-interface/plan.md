# Implementation Plan: Serve the web interface from the Sync API

**Branch**: `023-serve-web-interface` | **Date**: 2026-11-14, updated 2026-11-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/023-serve-web-interface/spec.md`, and the approved findings of
[`critiques/critique-20261115.md`](./critiques/critique-20261115.md).

## Summary

The Sync API process serves the built web interface at the root of its address, and the Sync API moves under `/api`.
A new outer application mounts the existing Sync API application at `/api`, forwards its lifespan so that the run
liveness checks keep running, and serves the build with FastAPI's `app.frontend()` (research R1, R3). A middleware
adds a content security policy, frame protection and cache headers to every response outside `/api` (R4). The
`Dockerfile` builds the web interface in a Node stage and copies only the static files into the runtime image (R5).

Requests without an `Authorization` header act as the principal named by `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR`, which
is off by default and may be an administrator, with a warning. They do so only from an allowed host
(`INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS`, loopback names by default) and never from another origin, which blocks DNS
rebinding and cross-site requests (R7, R8). When requests are refused, the web interface shows a token page, and a
pasted token is kept in `localStorage` until **Forget token** (R11).

The CLI keeps `INFRAHUB_SYNC_API_URL` as the address without `/api` and adds the prefix itself (R9).

## Technical Context

**Language/Version**: Python 3.11–3.13 for the Sync service. TypeScript and React 19 for the web interface, built with
Node 22 and pnpm 10.34.5.

**Primary Dependencies**: FastAPI 0.141.1 (pinned; provides `app.frontend()`), Starlette 1.9.0, uvicorn and httpx, all
existing. Vite, TanStack Router, TanStack Query and openapi-fetch, all existing in the frontend. New:

- Build-only base image `node:22-bookworm-slim`, pinned by digest.
- Frontend development dependencies Vitest, jsdom and Testing Library (R12). They are not in the image or the bundle.

**Storage**: No server-side change. The browser stores one key in `localStorage`.

**Testing**: pytest (`tests/service`, `tests/client`, `tests/cli`), `-m docker` for `tests/image`, `-m smoke` for
`tests/dev_stack/smoke`. Frontend: `pnpm check`, `pnpm typecheck`, `pnpm test`, `pnpm build`.

**Target Platform**: The `linux/amd64` and `linux/arm64` image, and modern desktop browsers.

**Project Type**: Web service with a single-page web interface, plus a CLI client.

**Performance Goals**: No new target. Static files are served by the existing uvicorn process; hashed assets are
cacheable for a year.

**Constraints**:

- No Node in the runtime image (FR-002).
- Every external base image pinned by digest; pnpm pinned by hash.
- The build context stays an allow-list (`.dockerignore`, `tests/image/test_build_context.py`).
- No token value in any log (FR-019).
- No database change.

**Scale/Scope**: Six web interface pages, about 30 Sync API routes, two compose files, two CI workflows.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
| --- | --- | --- |
| I. Read-only by default | Anonymous access is off by default, must name an existing principal, and works only from allowed hosts and the same origin. An administrator principal is allowed with a start-up warning, by the user's decision. No new mutating route; apply still needs its checksum and, where configured, an approval. | Pass |
| II. Idempotency and safety | Routes and idempotency keys are unchanged. Old and new CLIs against the other server version fail the version check before any write (R10). The lifespan forwarding keeps the liveness reconciler running (R1). | Pass |
| III. Adapter symmetry | No adapter changes. | Not affected |
| IV. Type safety | New Python code typed; a new `PrincipalDirectory` protocol with one typed method; specific `ValueError` at start-up. No `ty` overrides. | Pass |
| V. Test discipline | Unit tests for the outer application (mount, lifespan, `frontend()`, headers), the 404 envelope, anonymous resolution with host and origin checks, and settings validation. Image tests for the web files and the absence of Node. Vitest for the token flow. Smoke suite for the CLI after the move. | Pass |
| VI. Secrets and boundaries | Start-up errors name variables, not values. The start-up line names the actor and hosts, which are not secrets. Host and origin checks guard the anonymous principal. The content security policy limits scripts that could read the stored token. | Pass |
| VII. Simplicity | No new Python dependency. `app.frontend()` replaces a hand-written fallback. New build-only image and frontend development dependencies, justified in R5 and R12. One new module (`site.py`). | Pass |
| Documentation | `compose-deployment.mdx`, `sync-http-api.mdx`, `development-stack.mdx` and `frontend/README.md` change in the same pull request (FR-026). | Planned |
| Changelog | A fragment under `changelog/` with the items of FR-027. | Planned |

**Gates crossed** (from the idea brief): API change, auth change, CI/CD change, new build dependency. No database
change; marking audit records of anonymous requests is out of scope for that reason.

**Post-design re-check**: The design keeps every result above. The lifespan forwarding (R1) and the host and origin
checks (R8) resolve the two must-address findings of the critique.

## Project Structure

### Documentation (this feature)

```text
specs/023-serve-web-interface/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-paths.md
│   └── settings-and-clients.md
├── critiques/critique-20261115.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
infrahub_sync/
├── client/
│   ├── __init__.py              # API_PATH_PREFIX
│   └── client.py                # base_url gains /api
└── service/
    ├── app.py                   # create_app(anonymous=...), 404/405 envelope, authenticate() with host and origin checks
    ├── auth.py                  # PrincipalDirectory, AnonymousAccess, host and origin checks
    ├── settings.py              # anonymous actor, allowed hosts, web directory
    ├── site.py                  # NEW: outer app, /api mount, lifespan forwarding, frontend(), headers middleware
    └── serve.py                 # build_app() resolves anonymous access, logs it, returns the outer app

frontend/
├── package.json                 # packageManager with hash, codegen:openapi reads /api/openapi.json, test script, Vitest
├── vite.config.ts               # proxy keeps /api; Vitest config
├── README.md
└── src/
    ├── api/client.ts            # onRequest adds the stored token
    ├── api/token.ts             # NEW: localStorage read, write, delete
    ├── api/token.test.ts        # NEW
    ├── components/token-gate.tsx       # NEW: token page
    ├── components/token-gate.test.tsx  # NEW
    ├── routes/__root.test.tsx   # NEW: Forget token, storage event
    ├── test-setup.ts            # NEW: jest-dom, relative request URLs and matchMedia under jsdom
    └── routes/__root.tsx        # TokenGate on 401, Forget token in the top bar

Dockerfile                       # web stage, runtime copy, ENV
.dockerignore                    # allow frontend sources
docker-compose.yml               # health check, anonymous settings
infrahub_sync/testcontainers/docker-compose.yml  # byte-equal copy of docker-compose.yml
development/docker-compose.yml   # health check
.github/workflows/ci-docker-image.yml  # submodules: true
.github/workflows/workflow-linter.yml  # pnpm test
.github/file-filters.yml         # image_inputs includes frontend/**
pyproject.toml                   # Ruff, rumdl and ty exclude the frontend/vendor submodule
tasks/dev.py                     # /api/status; the image build fetches the frontend submodule
docs/docs/compose-deployment.mdx
docs/docs/reference/sync-http-api.mdx
docs/docs/development-stack.mdx
changelog/421.changed.md

tests/
├── service/test_site.py         # NEW: mount, lifespan, frontend(), headers, no-web page
├── service/test_anonymous_access.py  # NEW: resolution table, host, origin, start-up errors and log line
├── service/test_http_api.py     # 404 and 405 envelopes
├── service/test_environment_names.py
├── service/web_fixtures.py      # NEW: shared builders for the site tests
├── service/test_prefect_wording.py, test_config_routes.py, test_validation_secret_containment.py  # the site and /api
├── dev_stack/test_deps_mode.py  # /api/status
├── test_workflow_contracts.py   # a folder pattern covers the files under it
├── client/, cli/                # /api paths in mock transports
├── image/conftest.py             # /api/version probe; FROM lines with --platform
├── image/test_build_context.py  # frontend in the allowed context
├── image/test_image_artifact.py # index.html present, no node, / and /api/version served
└── dev_stack/smoke/             # /api paths, test_evidence.py recorded path
```

**Structure Decision**: The existing layout is kept. The only new Python module is `infrahub_sync/service/site.py`,
which owns everything that is not the Sync API itself: the `/api` mount and its lifespan, the web interface, its
headers, and the page for installations without it. The anonymous access rules stay in `create_app()`'s
`authenticate()`, where every other authentication decision is made.

## Risks

- **Image build time.** `pnpm install` and `pnpm build` add a stage. The install layer is cached on the lock files,
  and the stage runs once for both architectures (R5).
- **Submodule size in CI.** `submodules: true` fetches `opsmill/infrahub` at the recorded commit. The checkout can use
  a shallow fetch if it is slow.
- **Content security policy and future frontend changes.** A new inline script or an external resource in the web
  interface is blocked until the policy changes. The hashes are computed from the served `index.html`, so a changed
  inline script needs no code change; an external resource does.
- **Operators who publish the Sync API under a host name.** Requests without a token through that name get 401 until
  the name is added to `INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS`. The documentation and the compose comment state this.
- **Development token override.** With `INFRAHUB_SYNC_API_TOKEN` set, the Vite server replaces a stored token. This
  is documented in `frontend/README.md` (R11).

## Pull request notes

- State that the change lands before 3.0.0 so that the `/api` move happens before a stable release (critique P1).
- The release-intent label is chosen when the pull request opens.

## Complexity Tracking

No constitution violations to justify.
