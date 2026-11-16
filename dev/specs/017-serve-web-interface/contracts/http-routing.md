# Contract: addresses and answers of the Sync service

**Feature**: [../spec.md](../spec.md) | **Research**: [../research.md](../research.md) R-1 to R-3

## Addresses

| Before | After | Token |
|---|---|---|
| `GET /version` | `GET /api/version` | no |
| `GET /status` | `GET /api/status` | no |
| `GET /docs`, `GET /redoc` | `GET /api/docs`, `GET /api/redoc` | no |
| `GET /openapi.json` | `GET /api/openapi.json` | no |
| every other route `<path>` | `/api<path>` | yes, unchanged |

No route answers at its earlier address. The OpenAPI document keeps unprefixed path keys and
declares `servers: [{"url": "/api"}]`.

## Answers under `/api`

| Request | Status | Body |
|---|---|---|
| a route | as today | as today |
| `/api/<no route>` | 404 | `{"error": {"code": "not-found", "message": "...", "status": 404, ...}}` |
| a route with a method it does not accept | 405, `Allow` header kept | `{"error": {"code": "method-not-allowed", ...}}` |
| other `HTTPException` raised by FastAPI or Starlette | its status | `{"error": {"code": "http-error", ...}}` |

## Answers outside `/api`

Evaluated in this order. "Envelope naming /api" means status 404 and
`{"error": {"code": "not-found", "message": "the Sync API is served under /api", "status": 404}}`
with `Content-Type: application/json`.

| # | Condition | Answer |
|---|---|---|
| 1 | method is not `GET` or `HEAD` | envelope naming /api |
| 2 | no built web interface, `Accept` includes `text/html` | 404, `text/plain`: "The web interface is not included in this install. The Sync API is served under /api." |
| 2 | no built web interface, otherwise | envelope naming /api |
| 3 | the path names a regular file inside the built directory | the file; `index.html` with `Cache-Control: no-cache`, `assets/*` with `Cache-Control: public, max-age=31536000, immutable`; 304 on a matching `If-None-Match` or `If-Modified-Since` |
| 4 | the path starts with `assets/` | envelope naming /api, status 404 |
| 5 | `Accept` includes `text/html` | `index.html`, 200, `Cache-Control: no-cache` |
| 6 | otherwise | envelope naming /api |

`GET /api` without a trailing slash is outside the mount and follows this table (rows 5 or 6).

Never sent: a file whose resolved path is outside the built directory, whatever the encoding of
`..` or `/` in the request.

## Not set

- No cookie, no session (FR-018).
- No token or environment value in any answer outside `/api`.

## Lifespan

The service's lifespan enters the API application's lifespan, so the liveness reconciler starts
and stops with the service, as before.
