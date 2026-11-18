# 43. The Sync service serves the web interface at the root of its address and the Sync API under `/api`

**Status**: Accepted; partly superseded on 2026-11-18 by
[ADR 0049](0049-sign-in-uses-local-accounts-and-the-environment-defines-admin.md) — the web
interface asks for an account name and a password and keeps the session token that the sign-in
returns, in place of a Sync API token that a person enters, and
`INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` is no longer read. The body below is unchanged.
**Date**: 2026-11-16
**Source**: `dev/specs/017-serve-web-interface/spec.md` (decisions D-1 to D-3, FR-001 to FR-034),
`dev/specs/017-serve-web-interface/research.md` (R-1 to R-7, R-12), issue #421

## Context

The web interface in `frontend/` was not part of the released image, and the Sync service did
not serve it. To open it, an operator ran a Node development server next to the service. That
server forwarded every request to the Sync API with one token read from its own environment, so
every person who opened the page acted with that token, including for a verify, an apply or a
cancel.

Issue #421 asks the Sync service to serve the web interface itself. Three page paths of the web
interface are also Sync API routes: `/runs`, `/runs/{run_id}` and `/configs/{config_id}`. One
address cannot answer both a browser that opens the page and a client that calls the route.

## Decision

1. The Sync service serves the built web interface at the root of its address and every Sync API
   route under `/api`, including `/api/version`, `/api/status`, `/api/docs` and
   `/api/openapi.json`, which still answer without a token. No route answers at its earlier
   address. An outer Starlette application mounts the unchanged FastAPI application at `/api` and
   enters its lifespan, because Starlette does not run the lifespan of a mounted application. One
   route answers every other address:
   - a file of the built web interface, or the entry page for a browser that asks for HTML;
   - a "not found" error in the Sync API's error format, stating that the Sync API is served under
     `/api`, for any other client and for a missing file under `assets/`.
2. `INFRAHUB_SYNC_WEB_DIRECTORY` names the built web interface. The image sets it to
   `/opt/infrahub-sync/web`. Without it, the Sync API still starts under `/api`. A browser (a
   client that accepts HTML) that opens the root address receives an answer that the web
   interface is not included in this install; any other client receives the "not found" error in
   the Sync API's error format.
3. A person enters a Sync API token in the web interface. The web interface checks it with
   `GET /api/me`, keeps it in the session storage of the browser tab, and sends it as a bearer
   header with every request. A 401 removes the token and returns to the token screen, and the
   status menu offers "Remove token". The service sets no cookie, keeps no browser session and
   sends no token to the browser. The development server forwards `/api` unchanged and adds no
   token.
4. The CLI and the Python client add `/api` to `INFRAHUB_SYNC_API_URL`. A value that already ends
   in `/api` is used as it is. The variable keeps its meaning, the address of the Sync service,
   as the Infrahub Python SDK does with the address of the Infrahub server. An answer that is not
   a Sync API answer fails with `error: not-a-sync-api`, which names the operation, the address
   and the status, and never prints the body.
5. Every answer outside `/api` carries `Content-Security-Policy`, `X-Content-Type-Options`,
   `X-Frame-Options` and `Referrer-Policy` headers. The policy allows scripts only from the Sync
   service and the inline scripts of the built entry page, identified by their SHA-256 hash. It
   allows inline styles, because the component library inserts a `<style>` element when the page
   runs. It allows requests only to the Sync service, fonts only from the Sync service and
   `https://rsms.me`, and no display inside a frame. Answers under `/api` are unchanged.

## Consequences

- The address of every Sync API route changes once. A script that calls the Sync API directly
  adds `/api` to its addresses. The values of `INFRAHUB_SYNC_API_URL` in Compose files and in
  operators' environments stay valid.
- The CLI and the service are upgraded and rolled back together. A CLI from before this change
  calls `/version`, receives the "not found" error, and prints `error: compatibility` with no
  server version. A newer CLI against an older service prints `error: not-a-sync-api`.
- The operation that the service records for a refused token now starts with `/api`, because it
  is the path of the request.
- The health checks of the Compose files and the development tasks call `/api/version` and
  `/api/status`.
- A person acts in the web interface with the permissions of the token they entered, as with the
  CLI. An operator gives each person their own token through
  `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`. A script running in the page can read the token while
  the tab is open; the web interface loads no script from another address.
- The image build needs the `frontend/vendor/infrahub` submodule and a Node build stage. The
  runtime stage holds only the built files.

## Alternatives considered

- **Add the `/api` prefix to the routes with `APIRouter(prefix="/api")`.** Rejected: every path in
  the OpenAPI document changes, which breaks the generated types of the web interface and about
  265 test addresses, and the document gains no `servers` entry.
- **Set `root_path="/api"` on the FastAPI application without a mount.** Rejected: the routes keep
  answering at the root as well, and the page paths still collide with them.
- **A login route that sets a session cookie.** Not chosen by the maintainer: the service would
  keep a browser session and set a cookie, while a token kept for the tab uses the bearer
  authentication the Sync API already has.
- **A separate static file server in front of the Sync service, for example a reverse proxy in
  the Compose file.** Rejected: the Sync API would keep its addresses, but the deployment would
  have two addresses and one more container, which issue #421 rules out.
- **Operators add `/api` to `INFRAHUB_SYNC_API_URL` themselves.** Rejected: every Compose file,
  environment and document would change.
