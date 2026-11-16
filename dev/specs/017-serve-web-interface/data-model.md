# Data model: Open the web interface from the address of the Sync API

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

No persistent data changes. This feature adds one setting, one directory, one browser-side value
and one client error.

## Web directory setting

| Field | Value |
|---|---|
| Name | `INFRAHUB_SYNC_WEB_DIRECTORY` |
| Declared in | `infrahub_sync/service/settings.py` as `WEB_DIRECTORY_ENV` |
| Accessor | `web_directory() -> Path \| None` |
| Default in Python | unset, which means no web interface |
| Default in the image | `/opt/infrahub-sync/web` (`ENV` in `Dockerfile`) |
| Read | once, when `build_service()` builds the outer application |

Validation:

- Unset or empty: `None`.
- Set to a path whose resolved directory holds no regular file `index.html`: a warning is logged
  once (`structlog`, with the path, no secret), and the value is treated as `None`.
- Never makes `validate_settings()` fail (FR-029).

## Built web interface

A directory produced by `pnpm build` (`frontend/dist`) and copied to `/opt/infrahub-sync/web`.

| Part | Description | Browser cache |
|---|---|---|
| `index.html` | Entry page for every page path | `Cache-Control: no-cache` |
| `assets/*` | Scripts and style sheets whose names carry a content hash | `Cache-Control: public, max-age=31536000, immutable` |
| other top-level files | for example an icon, if a future build adds one | default (no header) |

Owned by root and read-only in the image. Contains no token and no environment value.

## Token for the tab

Held by the web interface only.

| Field | Value |
|---|---|
| Storage | `sessionStorage`, key `infrahub-sync.api-token`; in-memory fallback when storage is blocked |
| Set | after `GET /api/me` with the typed token answers 200 |
| Value | the typed token with surrounding white space removed |
| Sent | as `Authorization: Bearer <token>` on requests to `/api` on the same origin only |
| Cleared | when the person chooses "Remove token"; when a request that carried this token receives 401 |
| On clear | the query cache and the plan download cache are emptied; the token entry screen is shown |

States: `absent` → (valid token entered) → `held` → (Remove token, or 401 for this token) →
`absent`. A 403 leaves the state `held`.

## Client address

| Input `INFRAHUB_SYNC_API_URL` | `SyncClient.service_url` | Base address of requests |
|---|---|---|
| `http://sync-api:8000` | `http://sync-api:8000` | `http://sync-api:8000/api` |
| `http://sync-api:8000/` | `http://sync-api:8000` | `http://sync-api:8000/api` |
| `http://sync-api:8000/api` | `http://sync-api:8000` | `http://sync-api:8000/api` |
| `http://sync-api:8000/api/` | `http://sync-api:8000` | `http://sync-api:8000/api` |
| `https://proxy.example/sync` | `https://proxy.example/sync` | `https://proxy.example/sync/api` |

The existing validation of `_service_url` is unchanged (scheme, host, no user name, password,
query or fragment).

## `NotSyncAPIError`

Subclass of the client's base error (`SyncClientError`).

| Attribute | Meaning |
|---|---|
| `operation` | the client operation, for example `list_configs` or `get_version` |
| `url` | the full address called, without query string |
| `status` | the HTTP status received |

Raised when an answer to any call has a `Content-Type` that is not JSON, or when `/version`
answers 404 with a body that is not an error envelope. The response body is never kept.
