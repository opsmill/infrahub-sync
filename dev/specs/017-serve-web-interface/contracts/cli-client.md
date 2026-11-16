# Contract: client address and the not-a-sync-api error

**Feature**: [../spec.md](../spec.md) | **Research**: [../research.md](../research.md) R-6, R-7

## Address rule

`INFRAHUB_SYNC_API_URL` and `--api-url` hold the address of the Sync service. The client:

1. validates it as today (http or https, a host, no user name, password, query or fragment);
2. removes trailing `/` characters;
3. removes one trailing path segment `api`, then trailing `/` again;
4. sends every request to `<result>/api<route>`.

`SyncClient.service_url` returns the result of step 3. Examples are in
[../data-model.md](../data-model.md), "Client address".

Help text of `--api-url` and of the documented variable: "Address of the Sync service, for example
http://127.0.0.1:8001. The client adds /api."

## The not-a-sync-api error

Raised as `NotSyncAPIError` (attributes `operation`, `url`, `status`) when:

- the `Content-Type` of an answer is not `application/json` or a `+json` type; or
- `GET /api/version` answers 404 with a body that is not an error envelope.

The CLI prints, on standard error, and exits 1:

```text
error: not-a-sync-api
operation: get_version
address: http://127.0.0.1:8001/api/version
status: 200
hint: Check INFRAHUB_SYNC_API_URL. It holds the address of the Sync service, without /api. The Sync service and the CLI must come from the same release.
```

The response body is never printed, logged or stored. The address is printed without query string.

## Unchanged

- Every other error, its fields and its exit code.
- `CompatibilityError` when `/api/version` answers with a valid version document whose API versions
  do not include the client's.
