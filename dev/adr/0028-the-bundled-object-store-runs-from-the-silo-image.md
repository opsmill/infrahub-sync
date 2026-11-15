# 28. The bundled object store runs from the Silo image

**Status**: Accepted
**Date**: 2026-10-22
**Source**: `dev/specs/archive/008-prefect-from-sync-image/research.md` (R3),
`dev/specs/archive/008-prefect-from-sync-image/spec.md` (D-5)

## Context

Every Sync Compose stack bundles an S3-compatible object store for saved plans and run files.
The stacks ran it from `cgr.dev/chainguard/minio:latest-dev`, a build of upstream MinIO. Upstream
MinIO Community Edition stopped publishing images on 2025-10-23, and its repository is archived.
The `latest-dev` tag is also not a fixed release.

The replacement had to keep MinIO's S3 API, environment variables and on-disk format, ship
linux/amd64 and linux/arm64 images, pull without a registry login, and run the existing volume
repair step (`stat` and `chown`) and a health check without `wget`, which the image lacks.

## Decision

The root `docker-compose.yml`, its packaged copy and the development stack run the bundled
object store, and its `object-store-init` repair step, from `pgsty/silo`, pinned by dated release
tag and digest. Silo is a maintained fork of MinIO.

- **Standard image, not `-distroless`.** The repair step needs `/bin/sh`, `stat` and `chown`,
  which only the standard image has.
- **Same user as before.** The server runs as `65532:65532`. The standard image runs as root
  and creates a new volume as `0:0`, so the repair step changes the owner when `stat` shows
  another one.
- **Health check.** `["CMD", "silo", "healthcheck", "ready"]`, the same command the
  `-distroless` variant uses as its built-in check.
- **Bucket creation stays in `sync-bootstrap`.** It already creates the bucket over the S3 API
  and treats an existing bucket as success, so no `mc` or `aws` container is added.
- **Not mirrored.** The image is pulled from Docker Hub, not copied into `registry.opsmill.io`.
- **Licence.** Silo is licensed under the AGPLv3. It runs unmodified in its own container. The
  operator documentation names the image, its licence and its source; no notice file ships.

## Consequences

- A deployment pulls three images: PostgreSQL, Silo and Sync.
- Moving to a new Silo release is a change to the pin in each Compose file, checked by the
  compose contract tests (tag and digest format, health check, user).
- The Silo project published 12 GitHub releases from 2025-12-15 to 2026-10-18, about one a
  month. If it stops publishing, the stacks need another image again.
- The image ran on linux/arm64 and, under emulation, on linux/amd64. A run on a native amd64
  host is not recorded.
- An operator who cannot accept the AGPLv3 image can use their own S3-compatible store
  instead ([ADR 0026](0026-the-compose-deployment-takes-an-external-store-credentials-and-ca-from-env.md)).

## Alternatives considered

- **The `-distroless` variant.** Rejected: it has no shell, so the repair step cannot run in it.
- **A second image only for the repair step.** Rejected: it adds a fourth image to every
  deployment.
- **Keep the Chainguard MinIO build.** Rejected: its upstream is archived, and the tag is not a
  fixed release.
