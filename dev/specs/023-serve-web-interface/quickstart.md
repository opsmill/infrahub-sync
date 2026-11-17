# Quickstart: validate the web interface served by the Sync API

**Feature**: [spec.md](./spec.md) | **Contracts**: [contracts/](./contracts/)

## Prerequisites

- Docker with Buildx.
- The submodule checked out: `git submodule update --init frontend/vendor/infrahub`.
- A bearer token mapping, for example:

  ```bash
  export INFRAHUB_SYNC_SERVICE_BEARER_TOKENS='{"operator": {"token": "operator-token-0000001", "administrator": true}, "web-ui": {"token": "web-ui-token-00000001"}}'
  ```

## 1. Build the image

```bash
docker build -t infrahub-sync:web .
docker run --rm --entrypoint sh infrahub-sync:web -c 'command -v node || echo "no node"; ls /opt/infrahub-sync/web/index.html'
```

Expected: the build succeeds, the command prints `no node` and lists `index.html` (FR-002).

## 2. Story 1: open the web interface without a token

```bash
INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=web \
INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR=web-ui \
docker compose up -d
docker compose ps sync-api          # healthy, through /api/version (FR-009)
docker compose logs sync-api | grep "anonymous access is on"   # FR-018
curl -s http://127.0.0.1:8001/api/me
curl -sI -H 'Accept: text/html' http://127.0.0.1:8001/runs/abc | grep -iE 'content-security|x-frame|cache-control'
```

Expected: one log line names `web-ui` and the allowed hosts. `/api/me` returns `"actor": "web-ui"` with no token. The
page response carries the policy, `X-Frame-Options: DENY` and `Cache-Control: no-cache` (FR-006).

Open `http://127.0.0.1:8001/` in a browser: the configuration list loads and the top bar shows `web-ui`. Open a run,
reload the page: the same run is shown (FR-003). Start a run: the audit log in the `sync-api` logs names `web-ui`.

Check that the liveness checks still run (FR-011): stop `sync-worker` while a run is in progress, and confirm that the
run is marked interrupted within the liveness threshold.

## 3. Requests from other sites

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: rebound.evil.example:8001' http://127.0.0.1:8001/api/me
curl -s -o /dev/null -w '%{http_code}\n' -H 'Origin: http://evil.example' -X POST http://127.0.0.1:8001/api/runs
```

Expected: `401` for the host that is not allowed (FR-015), `403` for the other origin (FR-016).

## 4. Story 2: clients after the move

```bash
export INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001 INFRAHUB_SYNC_API_TOKEN=operator-token-0000001
uv run infrahub-sync whoami
uv run infrahub-sync runs list
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' http://127.0.0.1:8001/api/no-such-route
curl -s -H 'Authorization: Bearer wrong-token-0000000001' -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/api/me
```

Expected: `whoami` prints `operator`; `runs list` succeeds; the unknown route prints `404 application/json`; the wrong
token prints `401`, not the `web-ui` principal (FR-014).

## 5. Story 3: personal token

```bash
INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR= docker compose up -d sync-api
```

In a browser at `http://127.0.0.1:8001/`:

1. The token page explains the refusal and names `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR` (FR-020).
2. Paste `wrong-token-0000000001`: the page shows the refusal; the browser's `localStorage` has no
   `infrahub-sync.api-token` (FR-022).
3. Paste `operator-token-0000001`: the top bar shows `operator` (FR-021).
4. Reload, then open a second tab: both act as `operator` (FR-023).
5. Remove `operator` from `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` and restart `sync-api`, then reload: the page says the
   stored token was refused (FR-025).
6. Select **Forget token**: `localStorage` no longer holds the key (FR-024).

## 6. Start-up checks

```bash
INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR=nobody docker compose up sync-api
INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR=operator docker compose up -d sync-api && docker compose logs sync-api | grep WARNING
```

Expected: the first container exits, and its log names `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR` without any token
(FR-017, FR-019). The second starts and logs a warning that `operator` is an administrator (FR-012, FR-018).

## 7. Python package without the web interface

```bash
INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY= uv run invoke dev.api
curl -s http://127.0.0.1:8001/ | head -5
```

Expected: an HTML page that names the released image (FR-005).

## 8. Automated checks

```bash
uv run invoke format && uv run invoke lint
uv run invoke tests.tests-unit
uv run pytest -m docker tests/image
uv run invoke dev.smoke                         # SC-002
(cd frontend && pnpm check && pnpm typecheck && pnpm test && pnpm build)
```

With `uv run invoke dev.api` running, check that the development server keeps the `/api` prefix (FR-010):

```bash
(cd frontend && pnpm dev) &
curl -s http://127.0.0.1:3000/api/version
```

Expected: the version document of the Sync API.
