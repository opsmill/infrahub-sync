# 16. The Compose deployment is one tag-pinned file

**Status**: Accepted
**Date**: 2026-10-01
**Source**: dev/specs/archive/007-harbor-image-publishing/research.md (R3, R6, R7); spec.md clarifications

## Context

The v3 operator deployment shipped as a release bundle. It was `deploy/compose/compose.yaml`
plus an `infrahub-sync-compose` lifecycle wrapper (813 lines), a mounted bootstrap script, a
secret file and `defaults.conf`. The wrapper bound the deployment to one sha256 image through
an `image.bind` record, and the host had to satisfy that record by loading a tarball from the
release. Once the image is published to Harbor ([ADR 15](0015-images-publish-to-harbor-through-one-reusable-workflow.md)),
that machinery no longer has a reason to exist. Infrahub and infrahub-mcp each ship one root
`docker-compose.yml`, which operators download and run with `docker compose up`.

## Decision

The operator deployment is the root `docker-compose.yml`. It is one self-contained file, with
no wrapper, no mounted scripts and no secret files.

- **Sync image reference.** Every Sync service uses
  `${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-<release>}`.
- **Release pin.** The release PR sets `<release>` with `invoke release.update-docker-compose`.
  `release-publish.yml` refuses to tag when `invoke release.validate-docker-compose` fails.
  The pin is bumped for pre-releases too, so every tag names an image that exists.
- **Database bootstrap.** The script is inlined as a top-level Compose `configs.content`
  block, so the minimum is Docker Compose 2.24. Every shell `$` in it is written `$$`.
- **Credentials.** Credentials are required with `${VAR:?…}` and never defaulted. Operators
  supply them in a `.env` beside the file.
- **Development stack.** The development stack moves to `development/docker-compose.dev.yml`,
  because Compose prefers `compose.yaml` over `docker-compose.yml` in the same directory.

## Consequences

- **Fewer steps.** Deploying is three steps: fetch the file from a release tag, write `.env`,
  then `docker compose up -d --wait`. There is no load step and nothing to unpack.
- **Tag instead of digest.** The deployment is pinned by tag, not by digest. A tag can be
  re-pointed, which the old binding prevented. A cautious operator can still check the digest
  behind a tag against the image signature.
- **Lost wrapper safety checks.** The wrapper's port preflight, foreign-resource refusal,
  reset confirmation and instance labels are gone. `docker compose down -v` deletes data
  without asking, so the docs warn about it.
- **Rotated passwords.** Postgres role passwords are set only when a role is first created.
  Rotating one means changing it in the database as well as in `.env`.
- **Tests.** The opt-in `-m compose` suite drives this file directly. The clean-host
  qualification suite was removed.

## Alternatives Considered

- **Keep the wrapper and strip its binding.** Rejected. Operators would still need the
  release's support files, not one file, which diverges from the siblings.
- **Rename only the development stack and keep `deploy/compose/`.** Rejected as the smallest
  change that still leaves the bespoke bundle in place.
- **Ship default passwords, as infrahub's file does.** Rejected under the constitution's rule
  that credentials are never committed.
- **A root `docker-compose.override.yml` for development.** Rejected. A developer running
  `docker compose up` in a checkout would silently merge development credentials into the
  operator file.
