# Quickstart: validate the web interface served by the Sync service

**Feature**: [spec.md](spec.md) | **Contracts**: [contracts/](contracts/)

## Prerequisites

```bash
uv sync --extra dev --extra prefect --extra service
git submodule update --init frontend/vendor/infrahub
(cd frontend && pnpm install --frozen-lockfile)
```

Docker with Buildx for scenarios 4 and 5.

## 1. Unit tier

```bash
uv run invoke format
uv run invoke lint
uv run ruff check .
uv run invoke tests.tests-unit
```

Expected: all pass. New tests cover the outer application (every row of
[contracts/http-routing.md](contracts/http-routing.md)), the lifespan forwarding, the 404 and 405
envelope under `/api`, the client address rule and the not-a-sync-api error
([contracts/cli-client.md](contracts/cli-client.md)).

## 2. Frontend checks

```bash
cd frontend && pnpm check && pnpm typecheck && pnpm build
grep -rl "Bearer " dist || true          # expected: no file holds a token value
```

Expected: the three commands pass; `dist/index.html` and `dist/assets/` exist.

## 3. Service with a local build

```bash
export INFRAHUB_SYNC_WEB_DIRECTORY="$PWD/frontend/dist"
uv run invoke dev.start
uv run invoke dev.api     # serves build_service on the development port
```

With the development address (default `http://127.0.0.1:8001`):

| Request | Expected |
|---|---|
| `curl -s $ADDR/api/version` | version JSON |
| `curl -s -o /dev/null -w '%{http_code}' $ADDR/api/runs` | 401 |
| `curl -s $ADDR/version` | 404 envelope, message "the Sync API is served under /api" |
| `curl -s -H 'Accept: text/html' $ADDR/runs/abc` | `index.html` |
| `curl -s -o /dev/null -w '%{http_code}' $ADDR/assets/missing.js` | 404 |
| `curl -s $ADDR/api/openapi.json \| jq .servers` | `[{"url": "/api"}]` |

In a browser: open `$ADDR/`, confirm the token entry screen; enter a wrong token (refused); enter a
configured token (list of runs); open `$ADDR/runs/<run_id>` in a new tab (token asked again, then
the run page); choose "Remove token" (token entry screen).

## 4. CLI against the service

```bash
export INFRAHUB_SYNC_API_URL=$ADDR          # unchanged value, no /api
uv run infrahub-sync whoami
uv run infrahub-sync configs list
uv run infrahub-sync runs list
INFRAHUB_SYNC_API_URL=$ADDR/api uv run infrahub-sync whoami   # same result, no /api/api
```

Expected: every command succeeds. Pointing the CLI at a server that answers HTML (for example the
Vite development server on port 3000) prints `error: not-a-sync-api` with the address and the hint.

## 5. Image

```bash
docker build -t infrahub-sync:web-check .
docker run --rm infrahub-sync:web-check ls /opt/infrahub-sync/web       # index.html assets
docker run --rm infrahub-sync:web-check sh -c 'command -v node || echo absent'   # absent
```

Then run the image tests the image gate runs (`tests/image/test_image_artifact.py`, see
`.github/workflows/ci-docker-image.yml`) against that tag.

Build without the submodule (in a scratch clone without `git submodule update`): the build fails
with the message that names `frontend/vendor/infrahub`.

## 6. Compose deployment

Start the root `docker-compose.yml` with the locally built tag, wait for `sync-api` to become
healthy, open `http://127.0.0.1:8001/` and repeat the browser steps of scenario 3.
