# Quickstart: validate Harbor image publishing

Each scenario maps to a user story or requirement in [spec.md](spec.md). The
interfaces are specified in [contracts/](contracts/).

## Prerequisites

- A checkout of this branch, plus `uv sync --extra dev --extra prefect --extra service`.
- Docker Engine with Compose 2.24 or later.
- For publish scenarios: maintainer rights to dispatch workflows, and the org Harbor
  variables and secrets available to this repository.
- Until 3.0.0: tester credentials for `registry.opsmill.io`.

## 1. Local unit checks (no Docker)

```bash
uv run pytest -q tests/test_release_pin.py tests/test_workflow_contracts.py
uv run invoke release.validate-docker-compose --version "$(uv version --short)"
```

Expected: everything passes, and the pin matches `pyproject.toml` (FR-017).

## 2. Smoke test against a local build (FR-016)

```bash
docker build -t infrahub-sync:smoke .
INFRAHUB_SYNC_IMAGE_REF=infrahub-sync:smoke uv run pytest -m docker tests/image/test_image_artifact.py
```

Expected: `--help` exits 0, `GET /version` answers within 60 seconds, and the OCI
labels are present. To prove the gate bites, break the `CMD` in a scratch Dockerfile
and confirm the test fails.

## 3. Pull-request gate (User Story 3, FR-006, FR-012)

1. Open a PR that touches `Dockerfile`. Expect `image / build (linux/amd64)` and
   `image / build (linux/arm64)` to run in parallel, push nothing, and report
   **Full qualification** as passing.
2. Open a PR that only touches `docs/`. Expect the image job to be skipped and
   **Full qualification** to pass.
3. Open a PR that breaks only arm64 (for example, an arch-specific wheel pin). Expect
   the arm64 build to fail and **Full qualification** to fail.

## 4. Manual dispatch (User Story 2)

Run **Actions → Build And Push Docker image → Run workflow** with:
- `ref` = a commit SHA;
- `tags` = `registry.opsmill.io/opsmill/infrahub-sync:dispatch-test`;
- `publish` = true.

Then:

```bash
docker login registry.opsmill.io            # until 3.0.0
docker pull registry.opsmill.io/opsmill/infrahub-sync:dispatch-test
docker buildx imagetools inspect registry.opsmill.io/opsmill/infrahub-sync:dispatch-test
cosign verify registry.opsmill.io/opsmill/infrahub-sync:dispatch-test \
  --certificate-identity-regexp 'https://github.com/opsmill/infrahub-sync/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
cosign verify-attestation --type spdxjson   <same identity flags> registry.opsmill.io/opsmill/infrahub-sync:dispatch-test
cosign verify-attestation --type cyclonedx  <same identity flags> registry.opsmill.io/opsmill/infrahub-sync:dispatch-test
```

Expected:
- the manifest list holds amd64 and arm64;
- the signature and both attestations verify (SC-002);
- `latest` has not moved.

Dispatch with `publish=true` and an empty `tags`: the run must fail before building.

## 5. Release (User Story 1, FR-004)

From `main`, dispatch the stable release workflow to open the release PR.

1. **Check the release PR.** It must change `pyproject.toml`, `uv.lock`, the
   changelog **and** `docker-compose.yml`'s `${VERSION:-…}` pins.
2. **Merge it.** `release-publish.yml` validates the pin, tags the release, and
   creates the GitHub release. A pre-release version is marked pre-release.
3. **Check the published image.** `trigger-release.yml` pushes the image.
   - For a pre-release P: tag P exists, and `latest` still resolves to the previous
     stable digest.
   - For a stable release S: tags S and `latest` share one digest.

Then, on an amd64 host and an arm64 host:

```bash
docker run --rm registry.opsmill.io/opsmill/infrahub-sync:<version> infrahub-sync --help
```

## 6. Compose deployment from a release (User Story 4)

On a clean host with no images:

```bash
curl -fsSLO https://raw.githubusercontent.com/opsmill/infrahub-sync/<version>/docker-compose.yml
cp <docs example> .env   # fill the required credentials
docker compose up -d --wait
docker compose ps
docker compose logs sync-api | grep -c -i -E 'password|secret' # expect 0
```

Expected: every service is healthy, and the Sync services run `<version>`.

Then check the failure paths:
- Unset one required variable: Compose refuses to start and names that variable.
- Set `VERSION=<other>`: the services run that tag instead.
- Block the registry: the pull error names the image.

## 7. Compose suite (opt-in)

```bash
docker build -t infrahub-sync:compose-test .
INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose
```

## 8. Removal check (User Story 5, SC-006)

```bash
git grep -n -E 'workflow-candidate|tester.packet|image\.bind|image-linux-|infrahub-sync-compose|deploy/compose' \
  -- ':!changelog' ':!CHANGELOG.md' ':!dev/specs' ':!docs/docs/release-notes' ':!docs/versioned_docs'
test ! -e compose.yaml && test ! -d deploy/compose && test ! -e .github/workflows/workflow-candidate.yml
```

Expected: no matches, and every `test` succeeds.
