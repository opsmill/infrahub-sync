# Research: Open the web interface from the address of the Sync API

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-11-16

Installed versions used for every measurement: FastAPI 0.141.1, Starlette 1.9.0, httpx 0.28.1,
Python 3.11.8, openapi-typescript 7.13.0, pnpm 10.34.5. Line numbers refer to
`feature/v3-develop` at `e8f05899`.

## R-1 Put every Sync API route under `/api`

**Decision**: Keep `create_app` (`infrahub_sync/service/app.py:301`) as the API application with
unprefixed routes. A new outer Starlette application mounts it with `Mount("/api", app=api)`, adds
one catch-all route for the web interface, and has a lifespan that enters
`api.router.lifespan_context(api)`.

**Rationale**:

- Starlette does not run the lifespan of a mounted application: `Router.lifespan`
  (`starlette/routing.py:647-656`) enters only its own `lifespan_context`, and `Mount.matches`
  (`:399`) matches `http` and `websocket` scopes only. The lifespan of `create_app` starts the
  liveness reconciler loop (`app.py:315-337`); without forwarding, that loop would silently stop.
  A probe showed the order `outer-start, inner-start, inner-stop, outer-stop` when the outer
  lifespan enters the inner one.
- Under a mount, FastAPI adds `servers: [{"url": "/api"}]` to `/api/openapi.json` from
  `scope["root_path"]` (`fastapi/applications.py:1108-1118`) and keeps the path keys unprefixed.
  `/api/docs` requests `/api/openapi.json` (`:1123-1130`). `openapi-typescript` produces identical
  types for two documents that differ only by `servers`, so `frontend/src/api/types.generated.ts`
  and its `baseUrl: "/api"` (`frontend/src/api/client.ts:25`) stay valid.
- The inner exception handlers and the `contain_unhandled_error` middleware keep running under the
  mount (probe: `GET /api/boom` returned the inner envelope).
- The 19 test files that build `TestClient(create_app(...))` and about 265 route literals in
  `tests/service/` keep working unchanged, including six assertions on OpenAPI path keys.

**Alternatives considered**:

- `APIRouter(prefix="/api")`: every OpenAPI path key becomes `/api/...` and no `servers` entry is
  added, which breaks the generated frontend types, changes about 20 lines in
  `frontend/src/api/queries.ts` and `actions.ts`, and about 265 test literals.
- `FastAPI(root_path="/api")` without a mount: the routes keep answering at the root as well
  (probe: `GET /runs` returned 200), which FR-002 forbids.

**Consequences**:

- `request.url.path` inside the API becomes `/api/runs`, so the operation recorded by
  `record_authentication_refusal` (`app.py:348`, `:352`) for a refused token becomes `/api/...`.
  Accepted; documented in the changelog.
- `GET /api` without a trailing slash does not match the mount (the mount pattern is
  `/api/{path:path}`). The outer route answers it with the 404 envelope.

## R-2 Unknown routes under `/api` answer with the error envelope

**Decision**: Register a handler for `starlette.exceptions.HTTPException` on the API application.
It returns the existing `ErrorEnvelope` (`infrahub_sync/client/models.py:1078`, built by
`_error_content`, `app.py:90-98`) with code `not-found` for 404, `method-not-allowed` for 405 and
`http-error` for any other status, and keeps the headers of the exception (the `Allow` header of a
405).

**Rationale**: No handler exists today. An unknown path returns FastAPI's
`{"detail": "Not Found"}` and a wrong method `{"detail": "Method Not Allowed"}`; the client cannot
parse either as an envelope. No existing test requests an unknown path. A probe with the handler
returned `404 {"error":{"code":"not-found",...}}` for `GET /api/nope` and
`GET /api/`, and a 405 envelope with `allow: GET` for `POST /api/runs`.

**Alternatives considered**: A catch-all route inside the API application. It would appear in the
OpenAPI document and would not cover 405.

## R-3 Serve the built web interface

**Decision**: A small custom route `Route("/{path:path}", ...)` registered after the mount, in a
new module `infrahub_sync/service/web.py`, accepting every method. Order of decisions:

1. Method other than `GET` or `HEAD`: 404 envelope with the message "the Sync API is served under
   /api".
2. No built directory: when `Accept` includes `text/html`, a plain-text 404 with "The web interface
   is not included in this install. The Sync API is served under /api."; otherwise the 404
   envelope naming `/api`.
3. The path names an existing regular file inside the directory: send it, whatever `Accept` says.
   `index.html` is sent with `Cache-Control: no-cache`; files under `assets/` with
   `Cache-Control: public, max-age=31536000, immutable` (their names carry a content hash).
4. The path starts with `assets/`: 404 envelope, never the entry page (FR-006).
5. `Accept` includes `text/html`: `index.html` with `Cache-Control: no-cache` (FR-005, FR-009).
6. Otherwise: 404 envelope naming `/api` (FR-007).

Path safety reuses `StaticFiles.lookup_path` (`starlette/staticfiles.py:160-179`) on a
`StaticFiles(directory=<resolved>, check_dir=False)` instance, which rejects absolute paths,
resolves with `os.path.realpath` and requires the result to stay inside the directory, and
`file_response`, which answers `If-None-Match` and `If-Modified-Since` with 304. The directory is
resolved before use, because `commonpath` compares strings and a macOS temporary directory resolves
from `/var` to `/private/var`.

**Rationale**: `StaticFiles(html=True)` serves `index.html` only for a directory address, returns
plain-text 404 for `/runs/abc`, sets no `Cache-Control`, and raises at construction when the
directory is absent (`staticfiles.py:56-57`, `:140-158`). A probe of the custom route passed every
case, including `/%2e%2e/secret.txt`, `/assets/%2e%2e/%2e%2e/secret.txt` and
`/assets/..%2f..%2fsecret.txt`. The first version of the probe checked `Accept` before looking for
the file and refused `GET /favicon.ico` with `Accept: image/*`; step 3 before step 5 fixes it.

No new dependency: Starlette 1.9.0 file responses use `anyio`, already installed; `aiofiles` is
not needed.

## R-3a Security headers for the web interface

**Decision** (critique finding E3): every answer of the catch-all route carries

```text
Content-Security-Policy: default-src 'self'; script-src 'self' 'sha256-<inline script>'...;
  style-src 'self' 'unsafe-inline'; font-src 'self' https://rsms.me; img-src 'self' data:; connect-src 'self';
  object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
```

`create_site` reads the built `index.html` once, finds every `<script>` element without a `src`
attribute, and adds the base64 SHA-256 of its exact content to `script-src`. Today there is one
(the theme script in `frontend/index.html`, which reads `localStorage` before the first frame).
Computing the hash from the built file keeps `frontend/` unchanged and keeps working when the
script changes. The answers under `/api` are JSON and are not changed.

**Evidence**: `frontend/index.html` has one inline script and a `data:` icon; the built style
sheet loads `https://rsms.me/inter/font-files/InterVariable.woff2` (measured in the built
`dist/assets/*.css`); `index.html` also preloads that font. No `eval`, `new Function`,
`dangerouslySetInnerHTML` or `style="..."` attribute exists in `frontend/src`. React sets style
properties through the DOM, which `style-src` does not block.

**Browser check** (quickstart scenario 3): the first policy used `style-src 'self'`. During the
browser check on 2026-11-16, Chrome blocked a `<style>` element that the press handling of
React Aria inserts into the page at run time. The policy therefore uses
`style-src 'self' 'unsafe-inline'`. Scripts stay limited to `'self'` and the hashed inline scripts.

**Alternatives considered**: moving the theme script to a file under `public/` changes
`frontend/`; `'unsafe-inline'` for scripts defeats the purpose.

## R-4 Where the outer application is built

**Decision**: A new factory `build_service()` in `infrahub_sync/service/serve.py` returns
`create_site(build_app(), web_directory())`. `main()` serves it. `tasks/dev.py` `API_FACTORY`
(`:116`) points at `infrahub_sync.service.serve:build_service`, and
`tests/dev_stack/test_deps_mode.py:216` follows. `build_app` and `create_app` keep returning the API
application.

**Rationale**: `tests/service/test_validation_secret_containment.py:284-290` and the `app_factory`
tests (`test_config_routes.py:1102-1262`, `test_environment_names.py:61-65`) use `build_app`
directly with unprefixed paths. The development API (`dev.api`, `uvicorn --factory`) must match the
image, so it moves to the new factory.

## R-5 Setting for the built directory

**Decision**: `INFRAHUB_SYNC_WEB_DIRECTORY`, declared in `infrahub_sync/service/settings.py` as
`WEB_DIRECTORY_ENV` with an accessor `web_directory() -> Path | None`. Unset or empty means no web
interface. The image sets it to `/opt/infrahub-sync/web`. It is read once when the outer
application is built and never fails `validate_settings()` (FR-029, FR-030). A path that is set
but holds no `index.html` is logged once with `structlog` as a warning and treated as absent.

**Rationale**: `settings.py` is where the service declares environment settings
(`REQUIRE_APPROVAL_ENV`, `:13`); `tests/service/test_environment_names.py:39` asserts the declared
names and is updated.

## R-6 The CLI and Python client call `/api`

**Decision**: `SyncClient._service_url` (`infrahub_sync/client/client.py:150-162`) keeps its
validation, removes trailing `/`, removes one trailing `/api` segment, and the constructor passes
`<address>/api` as the `httpx` base URL. `SyncClient.service_url` (the value shown to the person)
stays the configured address without `/api`.

**Rationale**: httpx 0.28.1 `_merge_url` appends the request path to the base path
(`base.raw_path + path.lstrip("/")`); measured: base `http://host:8000/api` and path `/runs` give
`http://host:8000/api/runs`. The 22 root-relative path literals in the client need no change. No
other module builds a Sync API address (`orchestration/flow.py` calls `run_remote_request` in
process; `tests/cli/test_parity_and_closure.py:190` asserts `client.py` holds the only
`httpx.Client(`).

**Alternatives considered**: Operators append `/api` to `INFRAHUB_SYNC_API_URL` themselves. Rejected
in decision D-3 of the specification: every Compose file, environment and document would change.

## R-7 A readable failure when the address is not a Sync API

**Decision**: A new exception `NotSyncAPIError(SyncClientError)` with the attributes `operation`,
`url` (the full address called) and `status`. The client raises it when an operation that expects
JSON receives a `Content-Type` that is not JSON (for example HTML from a newer service's web
interface, or from a proxy), when any operation receives an error status with a non-JSON body, or
when `/version` answers 404 with a body that is not an envelope (an older service without `/api`).
A 200 answer of `get_artifact` (`client.py:645-667`) is excluded: artifact content has any media
type by design and is checked against its `Digest` header (critique finding E2). The CLI prints `error: not-a-sync-api`, `operation:`, `address:`, `status:`, and a
hint: "Check INFRAHUB_SYNC_API_URL. It holds the address of the Sync service, without /api. The
Sync service and the CLI must come from the same release." The body is never printed or stored.

**Rationale**: Today every such answer becomes `CompatibilityError("the Sync API is not compatible
with this client")` with `server_version: <none>`, or `ProtocolError`, with no hint (measured with
`httpx.MockTransport`). An older CLI against the new service calls `/version` with `Accept: */*`,
receives the 404 envelope naming `/api` from R-3 step 6, and reports `error: compatibility`; that
older CLI cannot be changed, so the changelog says the two must be upgraded together.

## R-8 Build the web interface into the image

**Decision**:

- A stage `web` placed after the Python `build` stage:
  `FROM node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS web`.
  Digest resolved on 2026-11-16 with `docker buildx imagetools inspect node:22-bookworm-slim`; it is
  an OCI index for linux/amd64 and linux/arm64/v8 (Node 22.23.5, Corepack 0.36.0).
- `corepack enable`, then pnpm 10.34.5 from `packageManager` (`frontend/package.json:5`).
- The stage copies only `frontend/{.npmrc,package.json,pnpm-lock.yaml,pnpm-workspace.yaml,
  index.html,vite.config.ts,tsconfig.json,tsr.config.json,src}` and
  `frontend/vendor/infrahub/frontend/packages/ui` (244 KB of a 122 MB submodule). A build from a
  copy holding only these paths produced the same 9 files.
- Before `pnpm install`, a guard fails with "frontend/vendor/infrahub is missing: run
  git submodule update --init frontend/vendor/infrahub" when
  `vendor/infrahub/frontend/packages/ui/package.json` is absent (FR-026). Without the guard,
  `pnpm install --frozen-lockfile` succeeds and the failure appears later in `vite build`.
- `pnpm install --frozen-lockfile`, then `pnpm build`. Output: `frontend/dist`, 1.1 MB, `index.html`
  plus `assets/` (1 CSS, 7 JS). The TanStack devtools plugin removes devtools code from the
  production build (0 matches in `dist`).
- The runtime stage copies `dist` to `/opt/infrahub-sync/web`, owned by root, and sets
  `ENV INFRAHUB_SYNC_WEB_DIRECTORY=/opt/infrahub-sync/web`. `/opt/infrahub-sync` is already
  root-owned and in the read-only set of `tests/image/conftest.py:44-53`. No Node runtime reaches
  the runtime stage (FR-024).
- `.dockerignore` (allowlist, `*` then `!` entries) gains one `!frontend/<path>` line per path above
  and `!frontend/vendor/infrahub/frontend/packages/ui`, and `**/node_modules` and `**/dist` in the
  exclusion block. `!frontend` alone would admit 122 MB of submodule and a local `node_modules`.

**Rationale**: Node 22 matches the frontend CI job (`.github/workflows/workflow-linter.yml:156`);
the build passed inside the pinned image on linux/arm64. The submodule's own `package.json` asks for
Node 24 or later, but the Sync build does not read it. Packaging the files in the Python wheel was
rejected: the PyPI job has no Node and no submodule (`workflow-publish.yml:68`), and the
development stack mounts `../infrahub_sync` over site-packages
(`development/docker-compose.yml:73-75`), which would hide packaged files.

**Not verified locally**: a build on linux/amd64; the full `docker build` of the image (run during
implementation, T-verification).

**Dependency on placement**: `tests/image/test_version_policy.py:73` and
`test_build_context.py:31` assume the first `FROM` is the Python base, and `:81` splits on the
literal `FROM base AS build`, so the Node stage comes after the Python build stage.
`test_version_policy.py:131-140` requires the digest on every `FROM`.

## R-9 Rebuild the development image when the web interface changes

**Decision**: `tasks/dev.py` `BUILD_INPUT_FILES` (`:89`) stays for single files; the hash
(`:390-395`) also covers every file under the frontend paths of R-8 (sorted, skipping
`node_modules` and `dist`) and every file under `frontend/vendor/infrahub/frontend/packages/ui`
when present. A missing submodule contributes nothing, so the unit-test checkouts, which do not
fetch it (`workflow-tests.yml:34,57,112,140`), still compute a hash.
`tests/dev_stack/test_build_inputs.py:62-69` and the comment at `Dockerfile:79-81` follow.

## R-10 CI fetches the submodule where the image is built

**Decision**: Add `submodules: true` to the checkout steps that precede an image build:
`.github/workflows/ci-docker-image.yml:147`, `workflow-nightly-e2e.yml:272` and `:48`.
`tests/test_workflow_contracts.py:666-671` and `:746-751` assert those two nightly checkouts as
exact dictionaries and are updated. `.github/file-filters.yml:84-98` `image_inputs` gains
`frontend/**` and `.gitmodules`, which also satisfies the contract at
`tests/test_workflow_contracts.py:2056-2067` (every literal `COPY` source listed). ADR 0015's
reusable workflow is `ci-docker-image.yml` in this repository, so no other repository changes.

## R-11 Every caller of a moved route

Full inventory: 787 lines with file and line for every item, kept outside the repository during
research. Summary of what changes:

- Health checks on `/version`: `docker-compose.yml:285`, its byte-identical copy
  `infrahub_sync/testcontainers/docker-compose.yml:285`, `development/docker-compose.yml:278`.
- `tasks/dev.py:705` (`/status` readiness poll) and `:822` (message).
- `frontend/vite.config.ts:22` (rewrite) and `:23-25` (token header); `frontend/package.json:12`
  (`codegen:openapi` reads `.../api/openapi.json`); `frontend/README.md`.
- Tests against a live service, which call routes directly or pin recorded paths:
  `tests/dev_stack/smoke/*` (`test_service_api.py` base address at 149, `conftest.py:112`, and the
  recorded path tuples in five files, since `evidence.py:47` records `request.url.path`),
  `tests/dev_stack/test_deps_mode.py:428,442,453`, `tests/compose/*` (six files),
  `tests/integration/test_managed_write_guard_live.py:123,156`, `tests/image/conftest.py:280`.
- Mock-transport tests that match `request.url.path`: `tests/client/test_client.py` (59 lines),
  `test_resume.py` (4), `test_wait.py` (5), `tests/cli/test_apply_confirm_left_out.py` (1),
  `tests/service/test_prefect_wording.py` (6). The literals are updated to `/api/...` rather than
  stripped by a helper, so a wrong base address fails the tests.
- CLI help "Absolute URL of the Sync API" (`infrahub_sync/cli.py:151`, `:225`, `tasks/docs.py:58`),
  pinned by `tests/cli/test_client_commands.py:2378`; regenerated into `docs/docs/reference/cli.mdx`
  and `python-api.mdx` with `uv run invoke docs.generate`.
- Documents with a full address or a `curl` of a route: `docs/docs/reference/sync-http-api.mdx`
  (8), `compose-deployment.mdx` (6), `quickstart-compose.mdx` (2), `development-stack.mdx` (2 and the
  section "Run the web interface"), `operations/day-2-operations.mdx` (1), three tutorials (1
  each), `README.md` (2). Route names in prose without a host stay as they are, and
  `sync-http-api.mdx` states once that every route is relative to `/api`; this keeps
  `tests/test_run_events_docs.py:16,41`, which pins "`GET /events`", valid.
- Not changed: every `INFRAHUB_SYNC_API_URL` value (for example `http://sync-api:8000`, pinned by
  `tests/compose/test_compose_contract.py:1040`), `.github/scripts/nightly_e2e.py:93`, the examples
  and `dev/adr/` records, which are historical.

## R-12 The token in the web interface

**Decision**:

- `frontend/src/api/token.ts`: a module store copied from the pattern of `frontend/src/lib/theme.ts`
  (`getToken`, `setToken`, `clearToken`, `useToken` with `useSyncExternalStore`), backed by
  `sessionStorage` inside `try`/`catch`, with an in-memory fallback when storage is blocked. No
  state library is added.
- `frontend/src/main.tsx` renders the token entry screen instead of `<RouterProvider>` while no
  token is held. `router.load()` runs only when `RouterProvider` mounts (TanStack Router 1.170.46,
  `Transitioner`), so no loader request leaves without a token. No route file, no
  `routeTree.gen.ts` change, and the address the person opened is kept.
- `frontend/src/components/token-entry.tsx`: a `type="password"` input using the input classes and
  `Field` layout of `frontend/src/features/runs/form.tsx:9-48` (`@infrahub/ui` has no input
  component), checks the typed token with `GET /me` and stores it only on success. A 401 shows
  "The Sync API refused the token."; a network failure or 5xx shows that the Sync service did not
  answer. It never displays the actor returned by `/me` (FR-019).
- `frontend/src/api/client.ts` middleware: `onRequest` sets `Authorization: Bearer <token>` when the
  request has none (so the entry screen can check a token before storing it); `onResponse` on 401
  clears the token and the query cache only when the request carried the token currently held, so
  a late 401 cannot remove a newly entered token. 403 does not clear. The 401 step is exported for
  the two raw `fetch` callers.
- Two requests bypass `fetchClient`: the plan download (`features/runs/plan-download.tsx:64`, raw
  `fetch` to avoid the 20 s GET timeout) sends the header and calls the exported 401 step, and its
  module cache `downloads` (`:114`) is emptied when the token is cleared; the artifact link
  (`features/runs/run-sections.tsx:76-86`, `<LinkButton href=... download>`) becomes a button that
  fetches with the header, builds a `Blob` and saves it through a temporary link.
- A "Remove token" item in the existing `StatusMenu` of `frontend/src/routes/__root.tsx` (FR-016).
- `frontend/src/api/queries.ts:353-362` `describeError`: the 401 text that tells developers to set
  `INFRAHUB_SYNC_API_TOKEN` becomes one sentence for any person.
- `frontend/vite.config.ts`: delete the `rewrite` and `headers` lines and the
  `INFRAHUB_SYNC_API_TOKEN` read; keep `target` and `changeOrigin` (FR-020).
- `tasks/dev.py`: remove `FRONTEND_TOKEN_VARIABLE` (`:130`), `frontend_token` (`:894-922`) and its
  use (`:941`); `dev.frontend` prints which configured token label to enter, never the token.
  `tests/dev_stack/test_dev_tasks.py:416-465` follows.

**Rationale**: The frontend has no test runner; CI runs `pnpm check`, `pnpm typecheck`, `pnpm build`
(`.github/workflows/workflow-linter.yml:136-166`). The baseline passes locally. Biome: line width
100, double quotes, organised imports, and `a11y/noAutofocus` needs an ignore comment if the input
takes focus.

**Alternatives considered**: a `/login` route with `beforeLoad` redirects needs a regenerated
`routeTree.gen.ts` and loses the requested address unless it is passed along; a gate in
`__root.tsx` lets the four loaders and `StatusMenu`'s `/me` query fire before the gate renders.

**Known outside origin**: `frontend/index.html` loads a font style sheet from `https://rsms.me/`.
It is a style sheet, not a script, so the assumption "no script from another origin" in the
specification holds. Self-hosting the font is out of scope.
