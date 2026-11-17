# Research: Serve the web interface from the Sync API

**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Date**: 2026-11-14, updated 2026-11-15

Every finding below was checked against `feature/v3-develop` at f4638188, with the locked FastAPI 0.141.1 and
Starlette 1.9.0. The 2026-11-15 update applies [`critiques/critique-20261115.md`](./critiques/critique-20261115.md).

## R1. How the Sync API moves under `/api`

**Decision**: Keep `create_app()` in `infrahub_sync/service/app.py` as the builder of the Sync API application, with
its routes at its own root. Add a new outer application, built in `infrahub_sync/service/site.py`, that mounts the
Sync API application at `/api` and serves the web interface for every other path. `serve.build_app()` returns the
outer application.

The outer application's lifespan enters the Sync API application's lifespan:

```python
@asynccontextmanager
async def lifespan(_outer: FastAPI):
    async with api.router.lifespan_context(api):
        yield
```

**Rationale**:

- A mounted application keeps its OpenAPI paths relative to its mount point. `/api/openapi.json` still lists `/runs`,
  `/runs/{run_id}` and so on, and declares `servers: [{"url": "/api"}]` (checked by test). So
  `frontend/src/api/types.generated.ts` and the `baseUrl: "/api"` in `frontend/src/api/client.ts` stay correct
  without regeneration.
- `/api/docs` and `/api/openapi.json` follow from FastAPI's defaults on the mounted application (FR-007).
- `tests/service/test_http_api.py` (118 KB) and the other service tests build `create_app()` directly and call
  `/runs`. They keep working unchanged.
- Starlette does not run a mounted application's lifespan (checked by test: the inner lifespan did not run).
  `create_app()` starts the run liveness reconciler in its lifespan, so without the forwarding above the reconciler
  would never run (FR-011). `tests/service/test_site.py` builds the outer application with a stub reconciler and
  asserts that `reconcile_once()` is called.
- Inside the mount, `request.url.path` is the full path, such as `/api/configs` (checked by test). The paths recorded
  by `record_authentication_refusal()` and the audit log therefore start with `/api` (FR-027).
- `GET /api` without a trailing slash returns a 307 redirect to `/api/`, which then returns the 404 envelope.

**Alternatives considered**:

- One application with `APIRouter(prefix="/api")`: needs no mount and no lifespan forwarding, but the OpenAPI paths
  would start with `/api`. The frontend's generated types, `queries.ts` and every service test would change.
- A reverse proxy in front of uvicorn: adds a process to the image, which SC-001 rules out.

## R2. 404 for unknown `/api` paths

**Decision**: Add a handler for Starlette's `HTTPException` to `create_app()` that returns the existing
`ErrorEnvelope` with code `not-found` (404) or `method-not-allowed` (405).

**Rationale**: Today an unknown path returns FastAPI's default `{"detail": "Not Found"}` (checked by test, including
`GET /api/nope` with `Accept: text/html` through the mount). `client.ts` throws `SyncApiUnavailable` for any error
body without an envelope, so the web interface would report the Sync API as down (FR-004).

## R3. Serving the built web interface with `app.frontend()`

**Decision**: The outer application serves the build with FastAPI's `app.frontend()`:

```python
outer.frontend("/", directory=web_directory, fallback="index.html", check_dir=True)
```

The directory comes from the new setting `INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY`. When it is set, `site.py` calls
`frontend()` and converts the `RuntimeError` that `check_dir=True` raises for a missing directory or `index.html`
into a `ValueError` that names the setting. When it is not set, `site.py` registers a `GET /` route that returns the
page naming the released image, and every other path outside `/api` returns 404 with the same page (FR-005).

Behavior of `app.frontend()`, checked by test with the Sync API mounted at `/api`:

| Request | Result |
| --- | --- |
| `GET /`, `/runs`, `/runs/abc` with `Accept: text/html` | 200, `index.html` |
| `GET /runs/abc` with `Accept: application/json` | 404 |
| `GET /assets/<file>` | 200, the file |
| `GET /api/runs` | the Sync API; mounted routes match first |
| `GET /api/nope` with `Accept: text/html` | the Sync API's 404, never `index.html` |
| `HEAD` on a page path | 200 |
| `POST` on a page path | 404 |

`StaticFiles` underneath resolves paths inside the directory only and does not follow symbolic links.

**Rationale**: `app.frontend()` is FastAPI's documented way to serve a single-page build, and FastAPI's own guidance
says to use it instead of mounting `StaticFiles` by hand. It falls back to `index.html` only for requests that accept
HTML, which is stricter than a fallback for every `GET`: a script that forgets the `/api` prefix gets 404, not HTML.
It needs no new dependency; `pyproject.toml` already pins `fastapi==0.141.1`.

**Alternatives considered**: A hand-written fallback route over `StaticFiles` (the first version of this plan).
Rejected: it repeats what `app.frontend()` does and handles navigation requests less precisely.

## R4. Response headers of the web interface

**Decision**: A middleware on the outer application adds headers to every response whose path is not under `/api`:

- `Content-Security-Policy: default-src 'self'; script-src 'self' <hashes>; style-src 'self' 'unsafe-inline';
  img-src 'self' data:; font-src 'self' https://rsms.me; connect-src 'self'; frame-ancestors 'none';
  base-uri 'self'; form-action 'self'`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Cache-Control: public, max-age=31536000, immutable` for paths under `/assets/`, which Vite names by content hash;
  `Cache-Control: no-cache` for every other path, including `index.html`.

`<hashes>` are the SHA-256 hashes of the inline `<script>` blocks in the built `index.html`, computed once when the
outer application is built. Today that is the theme script in `frontend/index.html:21`.

**Rationale**:

- `frame-ancestors 'none'` and `X-Frame-Options: DENY` stop another site from framing the web interface and tricking
  a person into selecting **Apply** (FR-006, critique E5).
- The script policy stops injected inline script from running, which protects the stored token (FR-006, critique
  E6). Computing the hashes from the served file keeps the policy correct when the inline script changes.
- `app.frontend()` sets no `Cache-Control` (checked by test). `StaticFiles` still sends `ETag` and `Last-Modified`.
- `/api/docs` loads Swagger UI from a CDN, so the policy is not applied under `/api`.
- The web interface loads its font, Inter, from `https://rsms.me` (`frontend/index.html` and the built CSS), so
  `font-src` allows that host. Found during implementation: without it, `default-src 'self'` blocks the font. A run in
  headless Chromium against the image showed no policy violation.

## R5. Building the web interface in the image

**Decision**: Add a `web` stage to the `Dockerfile`:

- `FROM --platform=$BUILDPLATFORM node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee`.
  Node 22 matches `actions/setup-node` in `workflow-linter.yml`. The digest was read on 2026-11-14 with
  `docker buildx imagetools inspect`.
- `corepack enable`. `frontend/package.json` changes `packageManager` to
  `pnpm@10.34.5+sha512.a4ee05f2f73658255bd6a89859c065a45c28a57daefae2c893a168ee2b73168c37b91e83e57ea67654ad03f03031746430e8bce38e362e042605fb8abc80192e`,
  so Corepack checks the downloaded pnpm against the hash that the npm registry publishes (critique E11).
- Copy `frontend/package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml` and
  `frontend/vendor/infrahub/frontend/packages/ui/package.json`, then `pnpm install --frozen-lockfile` with a cache
  mount, then copy the rest of the sources and run `pnpm build`.
- The runtime stage copies `frontend/dist` to `/opt/infrahub-sync/web`, owned by root, and sets
  `INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY=/opt/infrahub-sync/web`.

`.dockerignore` adds `frontend` with exclusions for `node_modules`, `dist` and every path under
`frontend/vendor/infrahub` except `frontend/packages/ui`. `pnpm-workspace.yaml` lists only `.` and
`vendor/infrahub/frontend/packages/ui`, and it mirrors the catalog locally, so no other file of the submodule is
needed. `tests/image/test_build_context.py` adds `frontend` to `ALLOWED_CONTEXT`.

`tests/image/test_image_artifact.py` asserts that `/opt/infrahub-sync/web/index.html` exists, that `node` is not on
the runtime image's path, and that the started container serves `/` as HTML and `/api/version` as JSON. A broken web
build therefore fails CI instead of stopping a deployment at start-up (critique E4).

**Rationale**:

- `$BUILDPLATFORM` builds the static files once for both `linux/amd64` and `linux/arm64` images.
- The runtime stage copies only static files, so Node is not in the runtime image (FR-002).
- Every other external base in the `Dockerfile` is pinned by digest, and `tests/image/conftest.py`
  `external_image_references()` reads them.

**Alternatives considered**: Building the web interface in CI and copying `dist/` into the context. Rejected because a
local `docker build` would then produce an image without the web interface.

## R6. CI checks out the submodule

**Decision**: Add `submodules: true` to the `actions/checkout` step of `.github/workflows/ci-docker-image.yml`. Every
workflow that builds the image calls that reusable workflow.

**Rationale**: Only `workflow-linter.yml` checks out the submodule today. Without it, `frontend/vendor/infrahub` is
empty and `pnpm install` fails. The submodule is a public repository, so the checkout needs no credential.

## R7. Anonymous access settings and their validation

**Decision**: Two settings, read in `infrahub_sync/service/settings.py`:

| Variable | Value | Default |
| --- | --- | --- |
| `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR` | actor name of one principal | unset: off |
| `INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS` | comma-separated host names | `localhost,127.0.0.1,::1` |

`serve.build_app()` resolves them after the principal resolver is built and passes an
`AnonymousAccess(principal, allowed_hosts)` value, or `None`, to `create_app(anonymous=...)`.
A new protocol, `PrincipalDirectory`, declares `principal_for_actor(actor: str) -> Principal | None`, and
`EnvironmentPrincipalResolver` implements it. `PrincipalResolver` is unchanged: adding the method there would break every
test resolver that `create_app()` receives, and `create_app()` does not need it.

The start-up line is written with the standard `logging` module, as the rest of `infrahub_sync/service/` does.
Uvicorn configures only its own loggers, so `serve.main()` gives the `serve` logger a handler that prints in uvicorn's
format (`INFO:     …`).

- An unknown actor raises `ValueError` naming `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR` and stating that it must name an
  actor in `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`. The message does not include the value (FR-017).
- An administrator principal is accepted (FR-012). The start-up log line is then a warning (FR-018).
- The start-up log line names the actor and the allowed hosts. Neither is a secret, and no token is logged (FR-019).

**Rationale**: The names follow the existing `INFRAHUB_SYNC_SERVICE_*` names. Validating at start-up follows
`validate_settings()` and `EnvironmentPrincipalResolver.from_environment()`. `tests/service/test_environment_names.py`
asserts the declared names and gains both.

## R8. Which requests act as the anonymous principal

**Decision**: `authenticate()` in `create_app()` resolves a principal in this order:

1. The request has an `Authorization` header: it must hold a bearer token that resolves, or the request is refused
   with 401 as today (FR-014). `HTTPBearer(auto_error=False)` returns `None` both for a missing header and for a
   header with another scheme, so the check reads `request.headers` directly.
2. No `Authorization` header and anonymous access is off: 401 as today (FR-013).
3. No `Authorization` header and an `Origin` header whose host and port differ from the `Host` header, or an `Origin`
   of `null`: 403 with code `cross-origin-refused` (FR-016).
4. No `Authorization` header and the host name in the `Host` header, without its port and compared without case, is
   not an allowed host: 401 (FR-015).
5. Otherwise: the anonymous principal.

Both headers are parsed with `urllib.parse.urlsplit`: `urlsplit("//" + host)` for `Host` and `urlsplit(origin)` for
`Origin`. This removes IPv6 brackets and ports the same way for both, so `Host: [::1]:8001` gives the host name `::1`.
A missing port counts as the default port of the scheme.

**Rationale**:

- Step 1 keeps a malformed or refused credential from falling back to the anonymous principal.
- Step 4 defeats DNS rebinding: a rebound page sends its own host name in `Host`, which is not an allowed host
  (critique E3).
- Step 3 refuses cross-site requests that need no preflight, such as a form `POST` to
  `/api/configs/{id}/versions/{v}/validate`, which needs no custom header today (critique E3). Comparing `Origin` with
  `Host` rather than with a full URL keeps it correct behind a TLS proxy, where the scheme the browser uses differs
  from the scheme uvicorn receives.
- The defaults cover the compose file's default publication on `127.0.0.1`, so SC-001 needs no second variable.

Refusals keep their existing audit record through `record_authentication_refusal()`, with the reasons
`anonymous-host-not-allowed` and `cross-origin-refused`.

## R9. Clients that add `/api`

**Decision**: Add `API_PATH_PREFIX = "/api"` to `infrahub_sync/client/`. `SyncClient` builds its `httpx.Client` with
`base_url=f"{url}{API_PATH_PREFIX}"`. `INFRAHUB_SYNC_API_URL` keeps its meaning and validation (FR-008).

The other places that build a Sync API path from the address:

- `tasks/dev.py`: `status_url` and the "checking dev.api" message.
- `tests/dev_stack/smoke/`: the `httpx` clients built from `settings.sync_api_url`, and the recorded path in
  `test_evidence.py:51`, which becomes `/api/configs`.
- `tests/image/conftest.py` `wait_for_api()` and `test_image_artifact.py`.
- `tests/client/` and `tests/cli/`: the mock transports that match `request.url.path == "/version"` and similar.
- `docker-compose.yml` and `development/docker-compose.yml` health checks (FR-009).
- `infrahub_sync/testcontainers/docker-compose.yml`, a byte-equal copy of the root `docker-compose.yml` that the
  package ships. `tests/testcontainers/test_packaging.py` fails when the two files differ, so every change to the root
  file is copied over it.
- The other root paths in `tests/dev_stack/smoke/`: `test_config_lifecycle.py:277` and `:317`, and the
  `/openapi.json` readiness URL in `conftest.py:112`.
- `frontend/package.json` `codegen:openapi`, which reads `${INFRAHUB_SYNC_API_URL}/openapi.json`.

The direct Prefect flow does not call the Sync API: `INFRAHUB_SYNC_API_URL` is read only by `cli.py`,
`client/client.py` and `tasks/dev.py`, and only `cli.py` and `tasks/bench.py` import the client.

**Rationale**: httpx joins a `base_url` path with a request path, so `"/version"` becomes `/api/version` without
changing each call.

## R10. Version compatibility across the move

**Decision**: Keep `API_VERSIONS = ("v3-unstable",)`.

**Rationale**: `SyncClient` calls the version route before any other request and turns a non-200 answer into a
`CompatibilityError`. An older CLI calling `/version` on a new server, and a new CLI calling `/api/version` on an old
server, both receive 404 and report an incompatible server before any write.

## R11. Token handling in the web interface

**Decision**:

- `frontend/src/api/token.ts` reads and writes the token under the `localStorage` key `infrahub-sync.api-token`. Each
  access is wrapped in `try`/`catch`, so a browser that blocks storage behaves as if no token is stored.
- `fetchClient.use({ onRequest })` in `client.ts` adds `Authorization: Bearer <token>` when a token is stored.
- `__root.tsx` renders a new `TokenGate` in place of the page when `meQuery()` fails with 401. It shows the
  explanation and documentation link (FR-020), the message for a refused stored token (FR-025), and the token field.
- Submitting the field calls `GET /me` with that token in the header, and stores the token only on 200 (FR-021,
  FR-022).
- The top bar shows **Forget token** when a token is stored. It deletes the token and clears the query cache
  (FR-024).
- A `storage` event listener clears the query cache when another tab changes the token (FR-023).

The Vite development server keeps adding `INFRAHUB_SYNC_API_TOKEN` when that variable is set. Its header replaces a
stored token in development. `frontend/README.md` states this.

**Rationale**: The user chose `localStorage` with a delete action during grilling.

## R12. Tests for the web interface

**Decision**: Add Vitest with jsdom and Testing Library as development dependencies of `frontend/`, with a
`pnpm test` script. Tests cover `token.ts` (read, write, delete, blocked storage) and the states of `TokenGate`: no
token, a pasted token refused, a pasted token accepted, a stored token refused. The `frontend-lint` job in
`workflow-linter.yml` runs `pnpm test`.

**Rationale**: FR-020 to FR-025 decide who a browser acts as, so they need automated tests (critique E8). The new
packages are development-only and never reach the image or the browser bundle, which is the justification that
constitution principle VII asks for.

## R13. Documentation link from the token page

**Decision**: The token page links to
`https://docs.infrahub.app/sync/compose-deployment#open-the-web-interface`, the "Open the web interface" section of
`docs/docs/compose-deployment.mdx`.

**Rationale**: `docs/docusaurus.config.ts` holds a placeholder `url`, but `README.md:28` names
`docs.infrahub.app/sync` as the published site. The documentation uses `routeBasePath: "/"`, so the page path is the
file name without its extension. The anchor is the slug of the new section heading that T042 adds.
