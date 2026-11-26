# Contract: the token in the web interface

**Feature**: [../spec.md](../spec.md) | **Research**: [../research.md](../research.md) R-12

## Token entry screen

Shown instead of every page while no token is held. The address the person opened is kept and
displayed once a token is accepted.

| Element | Text |
|---|---|
| Title | Enter a Sync API token |
| Description | The web interface sends this token with every request to the Sync API. It is kept for this browser tab only. |
| Field label | Sync API token |
| Field | password input, no autocomplete of a user name |
| Button | Continue |
| Refused (401) | The Sync API refused the token. |
| No answer (network error, timeout, 5xx) | The Sync service did not answer. Check that it is running, then try again. |

The screen never displays the actor or any field returned by `/api/me`.

## Checking a token

`GET /api/me` with `Authorization: Bearer <typed token, trimmed>`. 200 stores the token. 401 shows
"refused". Any other status or a network failure shows "no answer". An empty field is not sent.

## Sending the token

- Every request through the API client to `/api` carries `Authorization: Bearer <token>`, unless the
  request already has an `Authorization` header (the check above).
- The plan download and the artifact download fetch with the same header and save the file from
  the browser.
- No request to another origin carries the token.

## Losing the token

| Event | Effect |
|---|---|
| A request that carried the held token receives 401 | token removed from the tab; query cache and plan download cache emptied; token entry screen shown |
| A 401 for a request that carried a different, earlier token | no effect |
| 403 | no effect on the token; the page shows the refusal as today |
| "Remove token" in the status menu | as for a 401 |
| The tab is closed | the browser discards `sessionStorage` for the tab |

## Development server

`pnpm dev` forwards `/api` to `INFRAHUB_SYNC_API_URL` (default `http://127.0.0.1:8001`) without
changing the path and without adding a header. The person enters a token as in a deployment.
`pnpm codegen:openapi` reads `${INFRAHUB_SYNC_API_URL:-http://127.0.0.1:8001}/api/openapi.json`.
