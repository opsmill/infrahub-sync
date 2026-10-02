---
title: "Image publishing"
---

## Image publishing

> Part of: Develop > Knowledge | Related: [Publishing an image](../guides/publishing-an-image.md)

<!-- Extracted from specs/007-harbor-image-publishing on 2026-10-01 -->

How the Sync container image is built, checked and published to the OpsMill Harbor registry,
and what each workflow is responsible for. The decisions behind this layout are recorded in
ADR 15 and ADR 16 (see [Decision records](../adr-index.mdx)).

### Where the image lives

| | Address |
|---|---|
| Push (CI) | `${HARBOR_HOST}/opsmill/infrahub-sync`, from the repository variable `HARBOR_HOST` |
| Pull (users) | `registry.opsmill.io/opsmill/infrahub-sync` |

Both addresses reach the same Harbor project. Pushes must use the `HARBOR_HOST` name, because
`docker/login-action` authenticates only that host, so a push addressed to
`registry.opsmill.io` gets `401 Unauthorized`. The credentials are the repository secrets
`HARBOR_USERNAME` and `HARBOR_PASSWORD`. The project is private until 3.0.0.

### The reusable workflow

`.github/workflows/ci-docker-image.yml` takes six inputs: `publish`, `version`, `ref`, `tags`,
`labels` and `platforms`. Its jobs run in this order:

1. **`build`**: a matrix with linux/amd64 on `ubuntu-24.04` and linux/arm64 on
   `ubuntu-24.04-arm`. Each leg builds and loads its image, then runs
   `pytest -m docker tests/image/test_image_artifact.py` with `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION` set. The
   smoke test checks the CLI, `GET /version` from the default API command, the runtime
   identity, the OCI labels, and that no injected secret reaches the logs. The image's
   `revision` label must equal the commit built from `ref`. With `publish` set, the leg then
   pushes by digest.
2. **`merge`**: only runs with `publish`, after every leg has passed. It runs
   `imagetools create` with every tag over every platform digest, and validates the resulting
   `sha256:` digest.
3. **`sign`** and **`sbom`**: `cosign sign --recursive` with a GitHub OIDC identity (no stored key) and `cosign attest` for SPDX
   and CycloneDX, with bounded retries.

A publishing run fails before building if `tags` is empty or `HARBOR_HOST` is unset. An
unset `HARBOR_HOST` would otherwise send the login to Docker Hub. Newer runs on the same
`ref` cancel build-only runs, but a publishing run is never cancelled.

### Callers

| Caller | `publish` | Tags | Secrets |
|---|---|---|---|
| `trigger-pr-develop.yml` (`image` job, PRs that change image inputs) | false | `infrahub-sync:pr` | none |
| `workflow-publish.yml` (from `trigger-release.yml` on `release: published`) | true | `<version>`, plus `latest` per the rule below | inherited |
| Manual `workflow_dispatch` | chosen at dispatch | listed at dispatch | inherited |

The required check **Full qualification** passes when the PR `image` job succeeds, or when it
is skipped because no file in the `image_inputs` filter changed.

### Release path and `latest`

- `trigger-push-stable.yml` opens the release PR. It validates the version as canonical PEP 440
  (`str(Version(v)) == v`), bumps `pyproject.toml` and `uv.lock`, and runs
  `invoke release.update-docker-compose` so the root `docker-compose.yml` pins the release.
- `release-publish.yml` runs on the merge to `main`. It runs
  `invoke release.validate-docker-compose`, then creates the tag and the GitHub release with one
  of three flag sets:
    - `--prerelease --latest=false` for an alpha, beta, RC or dev version;
    - `--latest` for a stable release that is the newest stable release tag;
    - `--latest=false` for a stable release older than the newest stable tag.
- `workflow-publish.yml` moves the image's `latest` tag only for a stable release that is
  GitHub's `releases/latest`. Only an HTTP 404 from that lookup counts as "no release yet";
  any other failure fails the job.

### Known residual scan findings

Harbor scans every pushed image. After the dependency bumps in this branch, no Python package
in the image has a known vulnerability. Two groups of findings remain:

- **Debian 12 base packages** (`perl-base`, `util-linux`, `libsqlite3-0`, `zlib1g`, `libncursesw6`, `libsystemd0`,
  `openssl`, and others). Most have no fix in bookworm. Moving the runtime base to
  `python:3.13-slim-trixie` (Debian 13) removes every critical finding (scan comparison: 5 critical and
  58 high on Debian 12, 0 and 51 on Debian 13). Infrahub and infrahub-mcp already use the
  plain `python:X-slim` tag, which is Debian 13.
- **`pcre2 10.32-3.el8_6`**, inside `psycopg_binary.libs/`. The `manylinux` `psycopg-binary`
  wheel is built on AlmaLinux 8 and bundles it. Harbor reports it, but a local `trivy` scan does not. Removing it
  means building `psycopg[c]` against Debian's `libpq`.

The base image's own pip is uninstalled in the Dockerfile. It carried vendored copies of
`setuptools`, `msgpack` and `urllib3` that nothing in the image runs.
