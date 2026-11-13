# 26. The Compose deployment takes an external object store, adapter credentials and CA certificates from `.env`

**Status**: Proposed
**Date**: 2026-11-13
**Source**: dev/specs/021-compose-store-credentials-ca/research.md (R1, R3, R4)

## Context

[ADR 16](0016-the-compose-deployment-is-one-tag-pinned-file.md) makes the operator deployment
one file and a `.env`, with no secret files and no host path. Under it, Sync could store its
files only in the bundled object store of that file, the worker received the credentials of
three adapters, and the Sync services trusted only the public roots of the image.

## Decision

- **External object store.** An operator points Sync at an S3-compatible store with two `.env`
  settings, `INFRAHUB_SYNC_S3_ENDPOINT_URL` and `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` (`path` or
  `virtual`), beside the existing bucket, prefix, region and key settings, and leaves the
  bundled store out with `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`. That setting is the `scale` of
  the two bundled store services, and `sync-bootstrap`'s dependency on the store is
  `required: false`. Left out, Compose creates no bundled store container, so the store's
  credential reaches no bundled store process. Compose 2.24, the minimum this file states,
  honors both.
- **Bootstrap checks the store.** `sync-bootstrap` refuses an endpoint and a bundled store
  setting that disagree, checks for the bucket with `HeadBucket` before it creates one, and
  checks that the store refuses a second create-only write, each with a fixed failure name.
- **A fixed list of adapter credential variables.** Compose passes only the variables the file
  names. `sync-worker` receives a fixed list of `INFRAHUB_SYNC_CREDENTIAL_*` variables: a name
  for each credential setting of each bundled adapter, and every name the shipped examples and
  adapter pages use. A name that is not on the list does not reach the worker.
- **CA certificates.** An operator names a host directory of PEM files with
  `INFRAHUB_SYNC_CA_CERTIFICATES`. `sync-bootstrap`, `sync-api` and `sync-worker` mount it
  read-only. Unset, they mount an empty named volume. At start, when the directory holds
  certificate files, each process writes one bundle of the public roots and those files to its
  scratch directory and points the trust variables of its client libraries at it. With no
  certificate file, it writes no bundle and changes no trust variable. A file that holds a
  private key stops the process.

## Consequences

- A deployment with none of these settings runs as before.
- The Sync services share one read-only mount source, the CA directory. They still share no
  writable mount.
- A package for the Compose deployment uses the listed credential names. Two sources of one
  adapter that need different credentials under unlisted names cannot both run.
- An unreadable or private-key certificate file stops a process at start, naming the file.
- Moving to an external store does not move earlier files.
- `sync-bootstrap` sends three more S3 requests per start when the bucket exists, and four more
  when it creates the bucket.

## Alternatives considered

- A Compose profile for the bundled store. Rejected: a service with a profile starts only when
  `COMPOSE_PROFILES` names it, so every existing `.env` would lose its store.
- A per-command scale flag. Rejected: one `up` without it starts the bundled store with the
  external store's credential as its root credential.
- Passing all of `.env` to the worker. Rejected: the worker would hold the PostgreSQL
  administrator and Prefect database passwords.
- A worker-only credential file. Rejected: it is the kind of secret file ADR 16 removed.
- PEM text in a `.env` value, a file setting that defaults to `/dev/null`, and a fixed host
  directory created on first start. Rejected for the CA: hard to write correctly, not shared
  by every Docker host, or created owned by root.
