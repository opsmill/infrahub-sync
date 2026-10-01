# Data model: Publish the Sync image to the OpsMill registry

This feature stores no application data. Its "entities" are release artifacts and
the identifiers that tie them together.

## Release version

- **Source of truth**: `pyproject.toml` `version`, set by `uv version` in the
  `chore(release)` PR.
- **Format**: matches `^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$`, which
  `trigger-push-stable.yml` already validates. The git tag is the bare version, with
  no `v` prefix.
- **Derived**: `is_prerelease = Version(v).is_prerelease or Version(v).is_devrelease`.
- **Rules**: one tag per version (`release-publish.yml` refuses a version that is
  already tagged). The GitHub release's `prerelease` flag must equal
  `is_prerelease`.

## Published image

| Field | Value |
|---|---|
| Repository | `${HARBOR_HOST}/opsmill/infrahub-sync`, which is `registry.opsmill.io/opsmill/infrahub-sync` |
| Identity | Manifest-list digest `sha256:…` covering linux/amd64 and linux/arm64 |
| Labels | `org.opencontainers.image.source`, `.version`, `.revision` (FR-007) |
| Signature | Keyless cosign signature on the manifest list and each platform manifest (`--recursive`) |
| Attestations | SPDX JSON and CycloneDX JSON SBOMs, attached with `cosign attest` |

**State transitions**:

```text
built (per platform, local) ──smoke pass──▶ pushed by digest (untagged)
        │ smoke fail                                │ all platforms pushed
        ▼                                            ▼
     run fails, nothing pushed        manifest list created + tags applied
                                                     │
                                                     ▼
                                          signed ──▶ SBOMs attested
```

A digest that is pushed but never tagged is garbage, and Harbor's GC collects it.
Signing and attestation happen after tagging, which matches the siblings. If either
fails, the run fails, and the release is reported as failed (edge case).

## Image tag

| Tag | Created by | Moves? | Rule |
|---|---|---|---|
| `<version>` | every release | re-points on a re-run of the same release | always |
| `latest` | stable release only | yes | `prerelease == false` **and** the tag equals GitHub `releases/latest` |
| any listed tag | manual dispatch | as requested | `publish: true` requires a non-empty tag list |

## `docker-compose.yml` (root)

- **Sync services**: `sync-bootstrap`, `sync-api`, `sync-worker`, and `cli` (profile
  `cli`). Their image is
  `${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-<pin>}`.
- **Pin invariant**: on every tagged commit, `<pin>` equals that commit's
  `pyproject.toml` version. `release.validate-docker-compose` checks it.
- **Inputs**: the operator environment contract in
  [contracts/docker-compose-env.md](contracts/docker-compose-env.md).

## Image workflow run

The inputs are in [contracts/ci-docker-image.md](contracts/ci-docker-image.md).
Three callers use it:

| Caller | publish | tags | platforms |
|---|---|---|---|
| `trigger-pr-develop.yml` (`image` job) | false | `infrahub-sync:pr` (local only) | both |
| `workflow-publish.yml` (release) | true | `<version>` (+ `latest` per rule) | both |
| manual `workflow_dispatch` | maintainer's choice | maintainer's list | maintainer's choice |
