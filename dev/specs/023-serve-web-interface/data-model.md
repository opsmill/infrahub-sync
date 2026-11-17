# Data model: Serve the web interface from the Sync API

**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

This feature stores no new data on the server. It adds three settings and one browser-side value.

## Principal (existing, `infrahub_sync/service/auth.py`)

| Field | Type | Rule |
| --- | --- | --- |
| `actor` | string, at least 1 character | Unique in `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`. |
| `administrator` | boolean | Unchanged. A principal that is not an administrator acts only on runs its actor started. |

New: `PrincipalDirectory.principal_for_actor(actor)`, implemented by `EnvironmentPrincipalResolver`, returns the
principal with that actor name, or `None`.

## Anonymous access (new settings)

| Variable | Value | Default | Validation |
| --- | --- | --- | --- |
| `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR` | the actor name of one principal | unset: off | At start-up. An actor that is not in `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` stops the service with an error that names the variable and does not include the value. Surrounding whitespace is removed; an empty value means off. An administrator is accepted, with a warning. |
| `INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS` | comma-separated host names, without ports | `localhost,127.0.0.1,::1` | At start-up. Each entry is trimmed and lowercased; an empty list stops the service with an error that names the variable. Read only when anonymous access is on. |

`create_app()` receives them as one value, `AnonymousAccess(principal, allowed_hosts)`, or `None`.

How a request resolves its principal (anonymous actor `web-ui`, default allowed hosts):

| `Authorization` | `Origin` | `Host` | Setting unset | Setting set |
| --- | --- | --- | --- | --- |
| absent | absent | `127.0.0.1:8001` | 401 | `web-ui` |
| absent | `http://127.0.0.1:8001` | `127.0.0.1:8001` | 401 | `web-ui` |
| absent | `http://evil.example` or `null` | `127.0.0.1:8001` | 401 | 403 `cross-origin-refused` |
| absent | absent or same as `Host` | `rebound.evil.example:8001` | 401 | 401 |
| `Bearer <token that resolves>` | any | any | that token's principal | that token's principal |
| `Bearer <token that does not resolve>` | any | any | 401 | 401 |
| any other value, such as `Basic …` | any | any | 401 | 401 |

Routes that need no token today (`/version`, `/status`, `/docs`, `/openapi.json`) stay open, now under `/api`.

## Web interface directory (new setting)

| Variable | Value | Default | Validation |
| --- | --- | --- | --- |
| `INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY` | absolute path to the built web interface | unset; the image sets `/opt/infrahub-sync/web` | At start-up, through `app.frontend(check_dir=True)`. A directory without `index.html` stops the service with an error that names the variable. |

## Stored token (new, browser only)

| Key | Storage | Written | Deleted |
| --- | --- | --- | --- |
| `infrahub-sync.api-token` | `localStorage` of the Sync API address's origin | after `GET /api/me` with the pasted token returns 200 | when the person selects **Forget token** |

States of the web interface:

```text
no token stored ──paste token, /me 200──▶ token stored
      ▲                                      │
      │                          Forget token│   /me 401
      └──────────────────────────────────────┘   ──▶ "stored token was refused": replace or forget
```

With no token stored, requests carry no `Authorization` header. They succeed when anonymous access is on and the
host is allowed, and show the token page otherwise.
