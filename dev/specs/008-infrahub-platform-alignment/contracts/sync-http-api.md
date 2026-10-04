# Contract: Sync HTTP API changes

The Sync HTTP API stays the only entry point for the CLI and the Python client (FR-019). This
contract lists what changes; routes not listed keep their current shape.

## Authentication (FR-021)

- Every request carries the caller's Infrahub token: `X-INFRAHUB-KEY: <token>` or
  `Authorization: Bearer <Infrahub JWT>`.
- `401` when the token is missing, expired or revoked. `403` when the Infrahub permission in
  [research.md](../research.md) R6 is missing. The response names the required permission, never
  the token.
- `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` is removed.

## Configurations

Configurations are created and edited in Infrahub. A Sync connected to Infrahub answers these
routes with `410 configurations-in-infrahub` and points the caller to Infrahub; a Sync without
Infrahub still accepts them (tasks T037, T039):

- `POST /configs`
- `POST /configs/{config_id}/versions`

These remain, read from Infrahub:

| Route | Change |
|---|---|
| `GET /configs` | Lists `SyncConfiguration` nodes; `config_id` is the configuration `name` |
| `GET /configs/{config_id}` | Unchanged shape. The current document's checksum is in the branch validation response |
| `GET /configs/{config_id}/versions` | Unchanged shape |
| `GET /configs/{config_id}/versions/{number}` | Unchanged shape |

New validation route (FR-009):

```http
POST /configs/{config_id}/validate?branch=<branch>
```

Validates the configuration's document as it stands on `branch` (default branch if omitted).
Returns `{config_id, branch, checksum, total_findings, next_offset, findings[]}` with the existing
finding shape. Creates nothing. The old route `POST /configs/{id}/versions/{v}/validate` stays for
an existing version.

## Runs

`POST /runs` request body (`CreateRunRequest`):

| Field | Change |
|---|---|
| `config_id` | Configuration `name` |
| `registry_version` | **Optional.** Omitted: the run uses the current document and reuses or creates its version (FR-006, FR-008) |
| other fields | Unchanged |

The response keeps its shape: `registry_version` is the version the run recorded or reused. The
`SyncRun` node is found by `run_id`. A run refused for invalid content returns `422`
`configuration-invalid` and creates no version; `POST /configs/{config_id}/validate` lists the
findings.

`POST /runs/{run_id}/apply` records a `SyncApproval` naming the caller and the approved checksum
after the apply is accepted, best effort. `409` on checksum mismatch, as today. A composed `sync`
needs the same permission but records no `SyncApproval`: no plan checksum exists at admission.

Verify, apply and cancel act on any run the caller's Infrahub permission covers, not only the
caller's own runs.

## CLI

| Command | Change |
|---|---|
| `configs register`, `configs version` | Refused by a Sync connected to Infrahub with a message that points to Infrahub; a Sync without Infrahub still accepts them (task T040) |
| `configs validate <config-id> [--branch B]` | Validates the current document on a branch; `<version>` becomes `--version N` |
| `diff`, `sync` | `--version` becomes optional |
| global | `INFRAHUB_SYNC_TOKEN` replaces the Sync bearer token; `INFRAHUB_SYNC_API_TOKEN` is read when it is unset. No command-line flag: a token on the command line shows in process listings |
