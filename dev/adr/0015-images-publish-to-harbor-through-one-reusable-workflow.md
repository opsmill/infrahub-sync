# 15. Images publish to Harbor through one reusable workflow

**Status**: Accepted
**Date**: 2026-10-01
**Source**: dev/specs/archive/007-harbor-image-publishing/research.md (R1, R2, R5, R10)

## Context

V3 built its container image in CI and handed it on as GitHub Actions artifacts: an OCI
layout, per-platform `docker load` archives, a candidate workflow, a private tester packet, and
a clean-host gate. None of it ever reached a registry, so every consumer had to load a
tarball. It was also unlike every other OpsMill product. `infrahub` and `infrahub-mcp` publish
to the OpsMill Harbor registry through one reusable build-and-push workflow, and users run
`docker pull`.

## Decision

Every Sync image is built by one reusable workflow, `.github/workflows/ci-docker-image.yml`,
which copies the structure of infrahub-mcp's. The workflow works like this:

- **Build and smoke test.** A matrix builds linux/amd64 on `ubuntu-24.04` and linux/arm64 on
  `ubuntu-24.04-arm`. Each leg loads its image and runs the smoke test
  (`tests/image/test_image_artifact.py`) before it may push.
- **Push and tag.** With `publish` set, each leg pushes by digest to
  `${HARBOR_HOST}/opsmill/infrahub-sync`. `merge` then creates the multi-platform tag only
  after every leg has passed. `sign` signs the manifest list keylessly with cosign, and
  `sbom` attaches SPDX and CycloneDX SBOMs.
- **Callers.** There are three:
    - pull requests build without publishing and without secrets;
    - a release publishes the version tag;
    - a manual dispatch publishes whatever tags the maintainer lists.
- **`latest`.** It moves only for a stable release that is also the newest stable release.
  `release-publish.yml` flags PEP 440 pre-releases, and it passes `--latest=false` for a
  stable backport.
- **Required check.** The required check keeps its name, "Full qualification". It passes
  when the image job succeeds, or when the job is skipped because no image input changed.
  The `qualify` label is gone.
- **Registry settings and access.** Registry settings are repository-level, as in
  infrahub-mcp: `HARBOR_HOST`, `HARBOR_USERNAME` and `HARBOR_PASSWORD`. The Harbor project
  stays private until 3.0.0.

## Consequences

- **Pulling.** Users pull `registry.opsmill.io/opsmill/infrahub-sync:<version>` like the
  other OpsMill images. Before 3.0.0 they log in first.
- **Size.** The image machinery shrank from 3,195 lines to about 550.
- **Platforms.** A broken arm64 build fails a pull request, not a release.
- **No partial releases.** A platform that fails its build or smoke test never gets a tag.
  A publishing run is never cancelled mid-way, so a pushed tag cannot be left unsigned.
- **What was given up.** The qualification evidence the old pipeline recorded is gone: the
  byte-identity proof of the loaded archive, the in-image vulnerability gate, and the
  build-environment canary check. Harbor's scanner and the attached SBOM replace the
  vulnerability gate.
- **Push address.** Tags are pushed to the `HARBOR_HOST` name. A push to
  `registry.opsmill.io` returns 401, because `docker/login-action` authenticates only the
  `HARBOR_HOST` name. Pulls go through `registry.opsmill.io`.

## Alternatives Considered

- **Keep `workflow-image.yml` and add a push.** Rejected: it is built around the OCI-layout
  handoff that was removed.
- **Emulate arm64 with QEMU on one runner.** Rejected as slower; native runners match the
  siblings.
- **Infrahub's `preview`, `stable` and `<major.minor>` channel tags.** Deferred. Each is a
  small change to `docker_meta` if needed later.
- **Decide `latest` from the version string alone.** Rejected: a stable 2.x backport would
  take `latest` from 3.x.
- **Rename the required check.** Rejected: it would need a coordinated branch-protection
  change.
