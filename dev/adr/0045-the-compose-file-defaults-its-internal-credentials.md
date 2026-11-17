# 45. The Compose file defaults its internal credentials

**Status**: Accepted
**Date**: 2026-11-16
**Source**: maintainer decision; partly supersedes the credentials rule of
[ADR 0016](0016-the-compose-deployment-is-one-tag-pinned-file.md)

## Context

[ADR 0016](0016-the-compose-deployment-is-one-tag-pinned-file.md) made every credential of the
root `docker-compose.yml` required with `${VAR:?…}`. It rejected default passwords under
Principle VI of the constitution, which said credentials are never committed.

As a result, someone who wants to try Sync had to write seven credentials into `.env` before
Compose would start a container. Six of them are used only between the file's own services, on
the deployment's network: the PostgreSQL administrator, product and Prefect passwords, the object
store keys, and the Prefect API credential. None of the services that use them publishes a port.
Only the API bearer tokens guard a port that the host publishes.

At this stage of V3, the maintainers want people to be able to test Sync with as few steps as
possible. An operator who runs a deployment with real data can set every value in `.env`.

## Decision

- The six internal credentials get fixed, non-empty defaults in `docker-compose.yml` with
  `${VAR:-default}`, the same value at every reference:
    - `INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD`: `infrahub-sync-postgres-admin`
    - `INFRAHUB_SYNC_PRODUCT_PASSWORD`: `infrahub-sync-product`
    - `INFRAHUB_SYNC_PREFECT_PASSWORD`: `infrahub-sync-prefect`
    - `INFRAHUB_SYNC_PREFECT_AUTH_STRING`: `infrahub-sync-prefect-api`
    - `INFRAHUB_SYNC_S3_ACCESS_KEY`: `infrahub-sync`
    - `INFRAHUB_SYNC_S3_SECRET_KEY`: `infrahub-sync-object-store`
- `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` stays required, because it guards the published API port.
- Source and destination credentials keep an empty default, as before: a run that needs one
  fails until it is set.
- The constitution (version 1.1.0) allows this as a named exception to Principle VI.
- The file's header comment and the operator documentation state each default and say to replace
  it for a deployment that holds real data.

## Consequences

- A test deployment starts with one variable set: the API bearer tokens.
- The six defaults are public, because they are written in the file. A deployment that keeps them
  depends on its Docker network alone to keep other processes away from PostgreSQL, the object
  store and the Prefect API. Anyone who can reach that network with the Prefect default can
  change what the worker runs.
- An operator who forgets to set a value gets the default without a warning, where Compose
  stopped before.
- PostgreSQL sets a role's password only when it creates the role. A deployment that first starts
  with the defaults keeps them in the database until an operator changes them there as well as in
  `.env`.
- The contract tests now check that every reference to an internal credential carries the same
  default, and that an empty Prefect credential falls back to that default rather than starting an
  unauthenticated server.

## Alternatives considered

- **Keep every credential required (ADR 0016).** Rejected for now: it keeps the safest default,
  but every first run needs a generated `.env`, which the maintainers consider too much for
  people who are trying Sync.
- **Default every credential, the API tokens included.** Rejected: the API port is published on
  the host, so a known default token would let any local process, or any host when
  `INFRAHUB_SYNC_BIND_ADDRESS` is changed, act as an administrator.
- **Keep the root file unchanged and ship the defaults in a separate test file or override.**
  Rejected: ADR 0016 keeps the deployment to one file, and a root `docker-compose.override.yml`
  would be merged into the operator file without notice.
