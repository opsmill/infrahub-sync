# 23. The Compose deployment takes an external object store and custom CA certificates from `.env`

**Status**: Accepted

## Context

ADR 16 makes the operator deployment one file and a `.env`. Sync could store its files only in
the MinIO service of that file, the Sync services trusted only the image's certificate
authorities, and no service mounted a host path.

## Decision

- An operator may point Sync at an S3-compatible store with `.env` settings for the endpoint,
  bucket, prefix, region, credential and addressing style, and leave the bundled MinIO out with
  `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`. Left out, Compose creates no MinIO container, so the
  store's credential reaches no MinIO process.
- An operator may name a host directory of PEM files with `INFRAHUB_SYNC_CA_CERTIFICATES`.
  `sync-bootstrap`, `sync-api` and `sync-worker` mount it read-only. Unset, they mount an empty
  named volume. At start, each process builds one bundle from the public roots and those files
  on its scratch root. A file that holds a private key stops the process.
- An engineer may publish the Prefect port on loopback with a second Compose file named on the
  command line. The file is not shipped.

## Consequences

- A deployment with none of these settings runs exactly as before.
- The Sync services share one read-only mount source. They still share no writable mount.
- An unreadable or invalid certificate file stops the process at start, naming the file.
- Moving to an external store does not move earlier files.

## Alternatives considered

A Compose profile for the bundled store; a per-command scale flag; a `.env` value holding the
PEM text; a file setting defaulting to `/dev/null`; an auto-loaded override file; a fixed host
directory created on first start.
