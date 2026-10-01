# Contract: `.github/workflows/ci-docker-image.yml`

This is a reusable workflow (`workflow_call`) that can also be dispatched by hand
(`workflow_dispatch`). The input names and meanings match infrahub-mcp's file of
the same name, so callers read the same in both repositories.

## Inputs

| Input | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `publish` | boolean | no | `false` | Push to `${{ vars.HARBOR_HOST }}/${{ github.repository }}`, tag, sign, and attach SBOMs. When `false`, it only builds and runs the smoke test. |
| `version` | string | no | `''` | Version recorded in the SBOM artifact name. |
| `ref` | string | yes | — | Git ref or SHA to build. |
| `tags` | string | yes | — | Newline-separated full image references. Must be non-empty when `publish` is true, otherwise the run fails before building. |
| `labels` | string | yes | — | Newline-separated OCI labels (source, version, revision). |
| `platforms` | string | no | `linux/amd64,linux/arm64` | Comma-separated subset to build. Platforms not listed are skipped. |

## Secrets and variables

Callers pass `secrets: inherit`.

- `vars.HARBOR_HOST`, `secrets.HARBOR_USERNAME`, `secrets.HARBOR_PASSWORD`: read only
  by steps guarded by `publish`, and never echoed.
- Keyless signing uses `id-token: write`.

## Jobs and guarantees

1. **`build`** is a matrix with amd64 on `ubuntu-24.04` and arm64 on
   `ubuntu-24.04-arm`. For each platform it:
   1. builds the image and loads it locally;
   2. runs `uv run pytest -m docker tests/image/test_image_artifact.py` with
      `INFRAHUB_SYNC_IMAGE_REF` set to the loaded image. The test checks that the CLI
      answers `--help`, that the API answers `GET /version` within 60 seconds, and
      that the OCI labels are present;
   3. if `publish` is set, logs in and pushes by digest
      (`push-by-digest=true,name-canonical=true`, `provenance: false`), then uploads
      the digest artifact.

   A failed smoke test fails the job, and nothing for that platform is pushed.
2. **`merge`** (only with `publish`) needs every `build` job. It runs
   `docker buildx imagetools create` with all tags, then outputs the manifest-list
   digest. No tag is ever created while any platform has failed.
3. **`sign`** (only with `publish`) runs `cosign sign --yes --recursive <repo>@<digest>`
   with bounded retries.
4. **`sbom`** (only with `publish`) runs syft to produce SPDX and CycloneDX, attaches
   both with `cosign attest --type spdxjson|cyclonedx`, and uploads the
   `sbom-<version>` artifact (90 days).

## Outputs

- `digest`, from `merge`: the published manifest-list digest. Empty when not
  publishing.
