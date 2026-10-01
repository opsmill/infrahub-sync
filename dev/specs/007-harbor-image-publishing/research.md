# Research: Publish the Sync image to the OpsMill registry

All findings are against `007-harbor-image-publishing`, which is based on
`feature/v3-develop` at `8a0d1875`. Sibling references are
`../infrahub-mcp` and `../infrahub`.

## R1. Shape of the reusable image workflow

- **Decision**: Replace `.github/workflows/workflow-image.yml` with a reusable
  `ci-docker-image.yml` copied from infrahub-mcp's. It has the same inputs
  (`publish`, `version`, `ref`, `tags`, `labels`, `platforms`) and the same jobs:
  - `build`, a matrix that puts linux/amd64 on `ubuntu-24.04` and linux/arm64 on
    `ubuntu-24.04-arm`;
  - `merge`, which runs `imagetools create` from the per-platform digests;
  - `sign`, with keyless `cosign sign --recursive`;
  - `sbom`, which runs syft to produce SPDX and CycloneDX, attaches both with
    `cosign attest`, and keeps them as a 90-day artifact.

  It differs from infrahub-mcp in three ways:
  1. **Smoke before push.** Each `build` job builds its platform with `load: true`,
     runs the smoke test (R4), and only then pushes by digest. That second
     `build-push-action` call hits the warm local cache.
  2. **Retries.** `cosign` calls retry like infrahub's do (bounded, for
     transparency-log hiccups).
  3. **Pinned actions.** Actions are pinned by SHA, as in infrahub's
     `ci-docker-image.yml`.
- **Rationale**: This is the closest match to the siblings (SC-004). `merge` only runs
  after every `build` job succeeds, so a platform that fails its build or smoke test
  never gets a tag (FR-016, edge case "one platform fails"). The digests it pushed
  stay untagged, and Harbor's garbage collection removes them.
- **Alternatives considered**:
  - Keep `workflow-image.yml` and bolt a push onto it. Rejected: it is built around
    the OCI-layout handoff that is being removed.
  - Emulate arm64 with QEMU on one runner. Rejected: slower, and Q3 chose native
    runners.

## R2. Where publishing hooks into the release flow

Findings:
- `trigger-push-stable.yml` runs only on `main`, by manual dispatch. It bumps
  `pyproject.toml` with `uv version`, runs `uv lock` and towncrier, and opens a
  `chore(release)` PR.
- `release-publish.yml` runs on push to `main`, only for the merge of a `release/*`
  PR, and calls `gh release create` **without `--prerelease`**.
- `trigger-release.yml` runs on `release: published` and calls `workflow-publish.yml`.
  That workflow runs `uv publish` unconditionally, ignoring its own `publish` and
  `version` inputs.

v3 is not released from `feature/v3-develop`. Its releases happen once v3 has merged
to `main`, so this feature has to wire the `main` release path.

- **Decision**:
  1. **Mark pre-releases.** `release-publish.yml` computes
     `packaging.Version(v).is_prerelease or .is_devrelease` and passes
     `--prerelease` (with `--latest=false`) for alpha, beta, RC and dev versions. The
     GitHub release flag then tells the truth, and everything downstream keys off it.
  2. **Image metadata.** `workflow-publish.yml` gains a `prerelease` input, honours
     `publish` for PyPI (bug fix), and adds infrahub-mcp's `docker_meta` and
     `publish_docker_image` jobs, which call `ci-docker-image.yml` with
     `publish: true`. The tags are the version, plus `latest` only when
     `prerelease == false` and the tag equals the GitHub `releases/latest` tag
     (infrahub-mcp's rule). That keeps `latest` off a stable backport for an older
     line.
  3. **Pass the flag through.** `trigger-release.yml` passes
     `prerelease: ${{ github.event.release.prerelease }}`.
- **Rationale**: This satisfies FR-004 and the Q2 answer without inventing a new
  channel scheme. Infrahub's `preview` and `stable` channel tags were deliberately
  left out (Q2), and adding them later is a two-line change to `docker_meta`.
- **Alternatives considered**: Deciding `latest` from the version string alone.
  Rejected because a stable 2.x patch shipped after 3.0 would steal `latest`.

## R3. Version pin in `docker-compose.yml`

- **Decision**: Use a new, small `tasks/release.py`. The current 1012-line module is
  removed, and the new one reuses its name the way infrahub does. It has two tasks,
  modelled on infrahub's `tasks/release.py`:
  - `release.update-docker-compose --version X` rewrites the `${VERSION:-…}` default
    on every Sync service image line, using a regex anchored on
    `registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-`, and leaves YAML
    comments untouched.
  - `release.validate-docker-compose --version X` fails unless every Sync image line
    pins exactly X.

  Unlike infrahub, the pin is bumped for pre-releases too. A `3.0.0a6` tag has to
  name an image that exists, and no stable v3 image exists yet. Two workflows call
  the tasks:
  - `trigger-push-stable.yml` runs `update` right after `uv lock`, and adds
    `docker-compose.yml` to the release PR's `git add` list.
  - `release-publish.yml` runs `validate` before `gh release create`, which
    satisfies FR-017's "refuse to publish".
- **Rationale**: This is infrahub's proven mechanism, and the pin always matches the
  tagged commit.
- **Alternatives considered**: A `sed` one-liner in the workflow. Rejected: it can't
  be unit-tested, and a quoting slip would ship a broken file.

## R4. Smoke test before tagging (FR-016)

Findings:
- The image's default command is `python -m infrahub_sync.service.serve`.
- The API exposes no `/health` endpoint. `GET /version` is static and needs no
  authentication.
- Startup requires these settings:
  - `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`, a non-empty JSON object;
  - `INFRAHUB_SYNC_DATABASE_URL`, pointing at a real PostgreSQL;
  - `INFRAHUB_SYNC_S3_BUCKET`, though S3 is never called at startup;
  - Prefect may be unreachable.
- `tests/image/conftest.py` (`api_environment`, `started_container`, `wait_for_api`)
  and `tests/image/test_image_artifact.py` already do exactly this. They run the
  container `--read-only` with tmpfs mounts, against a throwaway Postgres.

**Decision**: Keep those two files plus the fixtures they need, retargeted to an image
reference passed in `INFRAHUB_SYNC_IMAGE_REF`. Then each `build` job runs
`uv run pytest -m docker tests/image/test_image_artifact.py` against its locally
loaded image. The suite asserts three things:
- `infrahub-sync --help` exits 0;
- the API answers `GET /version` with the expected version within 60 seconds;
- the OCI labels for source, version and revision are present (FR-007).

The rest of `tests/image/` serves the removed layout, archive and canary machinery
and goes.

- **Rationale**: It reuses tested code (Principle VII), resolves the spec's
  health-check assumption with the smallest standalone configuration (a disposable
  Postgres), and runs the same check for pull requests and for publishing.
- **Alternatives considered**:
  - Adding a `/health` endpoint. Rejected: it is a product change outside this
    scope, and `/version` already works as a liveness signal.
  - A Dockerfile `HEALTHCHECK`. Rejected: it can't pass without a database.

## R5. Required "Full qualification" check (FR-012)

Findings:
- `trigger-pr-develop.yml` has a `qualification` job that decides the tier from the
  `qualify` label or the `qualify_all` path filter.
- Its `image` job calls `workflow-image.yml`.
- Its `qualification-required` job ("Full qualification", `if: always()`) fails
  unless the tier ran and `image` succeeded or was skipped.
- `tests/test_workflow_contracts.py` pins `REQUIRED_JOB = "qualification-required"`.

**Decision**: Keep the job id `qualification-required` and the name
`Full qualification`, so branch protection needs no change. Its logic changes to
this:
- `needs: [image-changes, image]`;
- pass when `image` is `success`, or `skipped` because no image input changed
  (User Story 3, scenario 4);
- fail on any other result.

The `qualify` label and the tier-decision job go, because nothing heavy is left to
opt into. A path filter `image_inputs` replaces `image_all` and `qualify_all`. It
matches `Dockerfile`, `.dockerignore`, `pyproject.toml`, `uv.lock`,
`infrahub_sync/**`, `opsmill_prefect_extras/**`, `docker-compose.yml`,
`tests/image/**`, `ci-docker-image.yml`, `trigger-pr-develop.yml` and
`.github/file-filters.yml`. The `image` job calls `ci-docker-image.yml` with
`publish: false` and needs no `actions: write` (no handoff to clean up) and no
secrets, so forks behave the same as internal branches.

- **Rationale**: This satisfies FR-006, FR-012 and SC-005 with the fewest moving
  parts. The required check stays green on documentation-only PRs.
- **Alternatives considered**: Renaming the check. Rejected: it would need a
  branch-protection change, coordinated at merge time.

## R6. Self-contained root `docker-compose.yml` (FR-011, FR-019)

Findings: today's `deploy/compose/compose.yaml` (313 lines) needs:
- a mounted `bootstrap/databases.sh`;
- an admin password supplied as a Docker secret file;
- `defaults.conf`;
- `INFRAHUB_SYNC_INSTANCE`, set by the wrapper;
- a sha256 `INFRAHUB_SYNC_IMAGE` with `pull_policy: missing`.

**Decision**:
- **Bootstrap script**: inlined into the file as a top-level Compose `configs:`
  entry with `content:` (Compose ≥ 2.23.1; Docker Compose 2.24 or later becomes the
  documented minimum) and mounted into `db-bootstrap`. It is still idempotent and
  still binds every value through `psql` variables.
- **Admin password**: comes from `INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD` in the
  environment, not a secret file.
- **Required credentials**: keep their `${VAR:?message}` guards (Principle VI: no
  committed defaults for secrets). An operator provides them in a `.env` beside the
  file, which Compose auto-loads. A sanitized `docs`-hosted example lists every
  variable.
- **Non-secret defaults**: move inline as `${VAR:-default}`.
- **Instance label**: dropped. It existed so the wrapper could verify ownership.
- **Sync image**: services use
  `${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-<pin>}`
  with the default pull policy.
- **The rest of `deploy/compose/`**: the wrapper, `OPERATING.md`, `defaults.conf`,
  `configuration/qualification.yaml` and `skills/` are all removed. The two
  operator skills move their durable content into the docs pages (R9).

- **Rationale**: This is the sibling pattern the user chose: fetch one file, add a
  `.env`, run `docker compose up -d`.
- **Alternatives considered**:
  - Shipping insecure default passwords like infrahub's file does. Rejected under
    Principle VI and because the v3 constitution forbids committed credentials.
  - Keeping a sidecar `.env.example` in the repo root. Allowed as docs-only, and not
    required to run.

## R7. Dev stack relocation (FR-020)

- **Decision**:
  - Move the root `compose.yaml` to `development/docker-compose.dev.yml`. Set the
    build context to `..` and keep the `name: infrahub-sync-dev` project name.
  - Change `tasks/dev.py` and `tasks/netbox.py` to pass `-f
    development/docker-compose.dev.yml`.
  - Update `tests/dev_stack/*`, `README.md`, `docs/docs/development-stack.mdx` and
    `custom-certificates.mdx`.
- **Rationale**: Compose picks `compose.yaml` over `docker-compose.yml` when both
  exist, so the dev file can't stay at the root.
- **Alternatives considered**: A root `docker-compose.override.yml`. Rejected: an
  operator who downloads only `docker-compose.yml` is fine, but a developer running
  `docker compose up` in a checkout would silently merge dev credentials into the
  operator file.

## R8. Fate of the Compose qualification test suite

Findings:
- `tests/compose/` holds the lifecycle suite (cold start, durable state, redaction,
  source credentials, configuration-free startup and minimum Compose version), plus
  the clean-host suite: about 20 files, and a 1945-line `clean-host.sh` that runs
  `docker load` on the release archive.
- The lifecycle fixtures need only an image Docker already holds. The blockers are
  `init`, the binding and the sha256-only interpolation.

**Decision**:
- **Remove**:
  - `tests/compose/clean_host/**`;
  - `tests/compose/test_clean_host_*.py`;
  - `tests/compose/test_qualification.py`;
  - `tests/compose/test_preflight.py` and the wrapper tests (the wrapper is gone);
  - all of `tests/release/`;
  - the workflow-contract tests for the candidate, packet, handoff, tier guard and
    kit.
- **Rewrite** to drive `docker compose -f docker-compose.yml` with a generated `.env`
  and `INFRAHUB_SYNC_DOCKER_IMAGE` / `VERSION` pointing at the image under test:
  - the lifecycle suite (`conftest.py`, `lifecycle.py`, `test_lifecycle.py`,
    `test_bundle_contract.py`, now named `test_compose_contract.py`);
  - `test_redaction.py`, `test_source_credentials.py`,
    `test_configuration_free_startup.py`, `test_compose_minimum.py`,
    `test_container_cli.py` and `test_documentation.py`.
- **Keep it opt-in** (`-m compose`), like today. It runs nightly, or by
  `workflow_dispatch` on `ci-docker-image.yml` callers, and not on every PR. The PR
  gate is the image build plus smoke test (R5).
- **Rationale**: The deployment's real behaviour (bootstrap idempotence, durable
  volumes, no secrets in logs) still has coverage (Principle V). Only the machinery
  that proved an archive's identity goes.
- **Alternatives considered**: Deleting the whole suite. Rejected because it is the
  only end-to-end coverage of the shipped deployment.

## R9. Documentation and agent material

- **Decision**:
  - **Delete** `develop/guides/building-a-tester-packet.md` and
    `develop/guides/qualifying-an-internal-candidate.md`, along with their
    `docs/sidebars.ts` entries.
  - **Rewrite against the registry and `docker-compose.yml` flow**:
    - `container-image.mdx`, `compose-deployment.mdx`, `quickstart-compose.mdx`,
      `installation.mdx` (registry login while the project is private, per FR-015);
    - `operations/compose-troubleshooting.mdx`, `day-2-operations.mdx` (a wrapper →
      `docker compose` command table) and `supported-platforms-and-limits.mdx`;
    - the three tutorials, `development-stack.mdx`, `contributing.mdx` (the
      required-check description), `testing-tiers.md`, `guides/index.md`,
      `repository-tour.md`, `custom-certificates.mdx` and
      `adapters/choosing-an-adapter.mdx`.
  - **Reword** the candidate prose in `AGENTS.md` and
    `.github/copilot-instructions.md`.
  - **Add a news fragment** `changelog/+harbor-image-publishing.changed.md`.
- **Rationale**: This satisfies FR-013 and FR-014, and the constitution's "docs in the
  same change" rule.

## R10. Registry project visibility (FR-015)

- **Decision**: No code. Visibility is a Harbor project setting. The plan records two
  administrator actions:
  1. Create the `opsmill/infrahub-sync` project as private, with a robot or pull
     account for testers, before the first publish.
  2. Flip it to public at 3.0.0.

  The docs carry a `docker login registry.opsmill.io` step, marked "until 3.0.0".
  The publishing workflow uses the org-level `HARBOR_HOST`, `HARBOR_USERNAME` and
  `HARBOR_PASSWORD` that infrahub-mcp uses. Whether the org shares these with this
  repository must be confirmed before the first publish, since it is a
  repository-settings action.

## R11. Removal sizing (SC-004)

The image-specific code today is 3195 lines:

| File | Lines |
|---|---|
| `workflow-image.yml` | 516 |
| `workflow-candidate.yml` | 680 |
| `tasks/image.py` | 860 |
| `tasks/release.py` | 1012 |
| `tasks/compose.py` | 127 |

The replacement is `ci-docker-image.yml` (about 280 lines) plus the new
`tasks/release.py` (about 80 lines), roughly 360 lines in all. That is about an 89%
reduction, so the ≥ 75% target holds.

## Implementation order

The constitution asks for small commits that don't mix refactors with behaviour
changes. The planned order:
1. Add `ci-docker-image.yml` and the smoke test. Wire the PR gate and keep the old
   files in place.
2. Wire release publishing: release-publish pre-release flag, workflow-publish,
   compose pin tasks.
3. Add the root `docker-compose.yml`, relocate the dev stack, and retarget the
   Compose suite.
4. Remove the old workflows, tasks, tests, `deploy/compose/` and tester packet.
5. Update the docs, agent material and changelog.

## Baseline

Recorded for T001 on 2026-10-01 at commit `53e50ec7`, before any change in this
feature. SC-004 is measured against this total. The command:

```bash
wc -l .github/workflows/workflow-image.yml .github/workflows/workflow-candidate.yml \
  tasks/image.py tasks/release.py tasks/compose.py
```

| File | Lines |
|---|---|
| `.github/workflows/workflow-image.yml` | 516 |
| `.github/workflows/workflow-candidate.yml` | 680 |
| `tasks/image.py` | 860 |
| `tasks/release.py` | 1012 |
| `tasks/compose.py` | 127 |
| **Total** | **3195** |

### After (T052)

Recorded on 2026-10-01 at commit `056c1379`, with the command
`wc -l .github/workflows/ci-docker-image.yml tasks/release.py`:

| File | Lines |
|---|---|
| `.github/workflows/ci-docker-image.yml` | 372 |
| `tasks/release.py` | 118 |
| **Total** | **490** |

3195 lines became 490, a reduction of 2705 lines or 84.7%. SC-004 asks for at
least 75%, so it holds. The total is above the 360-line estimate in R11: the
workflow grew the smoke test, the signing and the SBOM steps.
