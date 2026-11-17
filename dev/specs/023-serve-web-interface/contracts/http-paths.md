# Contract: HTTP paths at the Sync API address

**Feature**: [../spec.md](../spec.md) | **Research**: R1, R2, R3, R4, R8

`<address>` is the Sync API address, the value of `INFRAHUB_SYNC_API_URL`, for example `http://127.0.0.1:8001`.

## Sync API: `<address>/api/...`

Every route the Sync API serves today moves from `<address><path>` to `<address>/api<path>`. Request bodies,
responses, status codes and the error envelope do not change. There are no aliases at the old paths.

| Before | After | Token |
| --- | --- | --- |
| `GET /version` | `GET /api/version` | no |
| `GET /status` | `GET /api/status` | no |
| `GET /docs` | `GET /api/docs` | no |
| `GET /openapi.json` | `GET /api/openapi.json` | no |
| `GET /me` | `GET /api/me` | yes, or the anonymous principal |
| every `/runs…`, `/configs…`, `/events` route | the same path under `/api` | yes, or the anonymous principal |

The paths inside `/api/openapi.json` stay relative to `/api` (for example `/runs/{run_id}`). The document lists
`/api` as its server URL. `GET /api` redirects with 307 to `/api/`, which returns the 404 envelope.

The paths recorded in the audit log, such as the `path` of an authentication refusal, start with `/api`.

### Unknown path or method under `/api`

```http
GET /api/no-such-route
→ 404 Content-Type: application/json
{"error": {"code": "not-found", "message": "no Sync API route matches this path", "status": 404}}
```

A known path with a method it does not accept returns the same envelope with status 405 and code
`method-not-allowed`.

### Requests without a token

The rules are in [../data-model.md](../data-model.md#anonymous-access-new-settings). The new refusal:

```http
POST /api/runs
Origin: http://evil.example
Host: 127.0.0.1:8001
→ 403 Content-Type: application/json
{"error": {"code": "cross-origin-refused", "message": "a request without a token must come from the Sync API's own origin", "status": 403}}
```

## Web interface: every other path

### With a built web interface (the released image)

Served by `app.frontend("/", fallback="index.html")`:

| Request | Response |
| --- | --- |
| `GET /assets/<hashed file>` | the file |
| `GET /<file that exists in the build>`, for example `/favicon.ico` | the file |
| `GET` on any other path with `Accept: text/html`, for example `/`, `/runs/abc`, `/configs/x` | `index.html` |
| `GET` on any other path without `Accept: text/html` | 404 |
| `HEAD` on any of the above | the same status and headers, no body |
| a path that resolves outside the build directory | 404 |
| `POST`, `PUT`, `PATCH` or `DELETE` | 404, or 405 for a path that names a file |

Headers on every response outside `/api`:

| Header | Value |
| --- | --- |
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' 'sha256-…'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' https://rsms.me; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'` |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Cache-Control` | `public, max-age=31536000, immutable` for a built file under `/assets/` (status 200, not HTML); `no-cache` everywhere else, including a missing file under `/assets/` |

The `sha256-…` values are the hashes of the inline scripts in the built `index.html`. None of these headers is added
under `/api`, so `/api/docs` keeps loading Swagger UI.

### Without a built web interface (`INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY` unset)

| Request | Response |
| --- | --- |
| `GET /` | 200, an HTML page stating that this installation serves the Sync API at `/api` and that the web interface is included in the released image |
| any other `GET` outside `/api` | 404, the same page |
