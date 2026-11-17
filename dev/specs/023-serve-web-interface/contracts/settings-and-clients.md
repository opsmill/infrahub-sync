# Contract: settings, clients and the web interface's token

**Feature**: [../spec.md](../spec.md) | **Data model**: [../data-model.md](../data-model.md) | **Research**: R7,
R9, R11

## Service settings

| Variable | Read by | Meaning |
| --- | --- | --- |
| `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR` | `sync-api` | Actor name of the principal that requests without an `Authorization` header act as. Unset: such requests get 401. |
| `INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS` | `sync-api` | Host names a request without a token must name in `Host`. Default `localhost,127.0.0.1,::1`. |
| `INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY` | `sync-api` | Directory of the built web interface. Set by the image to `/opt/infrahub-sync/web`. |

Start-up errors (the service does not start). No message contains an actor name or a token; the
web directory error names the directory, which is not a secret, and the reason it could not be read:

- `INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR must name an actor in INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`
- `INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS must name at least one host`
- `INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS must list host names without a port or brackets, such as
  sync.example.net or ::1`, for an entry such as `sync.example.net:8001` or `[::1]`, which a `Host`
  header compared without its port could never match
- `INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY must name a readable directory that contains index.html:
  <directory> (<reason>)`

Start-up log line when anonymous access is on:

```text
INFO:     anonymous access is on: requests without a token act as "web-ui" from hosts 127.0.0.1, ::1, localhost
WARNING:     anonymous access is on: requests without a token act as administrator "admin" from hosts 127.0.0.1, ::1, localhost
```

`docker-compose.yml` passes both anonymous settings to `sync-api`:

```yaml
# Optional: the actor that requests without a token act as, for example the web interface.
# It applies to every client without a token, not only browsers.
INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR: "${INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR:-}"
# The host names those requests must use. Add the name you publish the Sync API under.
INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS: "${INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS:-localhost,127.0.0.1,::1}"
```

## Client settings (unchanged meaning)

| Variable | Meaning |
| --- | --- |
| `INFRAHUB_SYNC_API_URL` | The Sync API address, **without** `/api`. The CLI adds `/api`. |
| `INFRAHUB_SYNC_API_TOKEN` | The CLI's bearer token. In development, the Vite server also sends it, and it replaces a stored token. |

## Health checks

`docker-compose.yml` and `development/docker-compose.yml`:

```yaml
test: ["CMD", "python", "-c", "import httpx; httpx.get('http://127.0.0.1:8000/api/version', timeout=5).raise_for_status()"]
```

## Web interface token

| Behavior | Contract |
| --- | --- |
| Storage key | `localStorage["infrahub-sync.api-token"]` |
| Sent as | `Authorization: Bearer <token>` on every request from `fetchClient`, when stored |
| Stored when | `GET /api/me` with that token returns 200 |
| Not stored when | `GET /api/me` with that token returns 401; the token page shows the refusal |
| Deleted when | the person selects **Forget token** in the top bar |
| Refused stored token | `GET /api/me` returns 401 while a token is stored: the token page says the stored token was refused and offers **Replace** and **Forget token** |
| Other tab changes the token | the `storage` event clears the query cache, so the tab reloads its data as the new principal |
| Blocked storage | every read returns no token, every write is ignored; the page still works with the anonymous principal |
