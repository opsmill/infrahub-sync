# Implementation Plan: Publish the Sync image to the OpsMill registry

**Branch**: `007-harbor-image-publishing` (from `feature/v3-develop`) | **Date**: 2026-10-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/007-harbor-image-publishing/spec.md`

## Summary

Replace v3's image packaging, which moves the image around as GitHub Actions
artifacts, with the pipeline infrahub-mcp uses: one reusable
`ci-docker-image.yml`. That workflow builds linux/amd64 and linux/arm64 on native
runners and smoke-tests each image. With `publish` set, it then pushes by digest to
`registry.opsmill.io/opsmill/infrahub-sync`, merges a manifest list, signs it with
cosign, and attaches SPDX and CycloneDX SBOMs.

Three callers drive it:
- **Pull requests** build without pushing, behind the unchanged required check
  **Full qualification**.
- **Releases** push the version tag, plus `latest` for stable releases only.
  Pre-releases are now flagged correctly at release time.
- **Manual dispatch** pushes whatever tags the maintainer lists.

The operator deployment becomes a self-contained root `docker-compose.yml`. It pulls
`${INFRAHUB_SYNC_DOCKER_IMAGE:-…}:${VERSION:-<pin>}`, and the release PR bumps that
pin. The `infrahub-sync-compose` wrapper, the `image.bind` binding, the bundle
archive, the candidate workflow, the tester packet and roughly 3,200 lines of
image-specific workflow and task code are removed. The dev stack moves to
`development/docker-compose.dev.yml`. Design detail is in [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.10–3.13 for the invoke tasks and tests. GitHub
Actions YAML. POSIX sh for the inline database bootstrap.

**Primary Dependencies**:
- Docker actions: `docker/setup-buildx-action`, `docker/build-push-action`,
  `docker/login-action`, `docker/metadata-action`.
- Supply-chain tools: `sigstore/cosign-installer`, `anchore/sbom-action/download-syft`.
- Version parsing: `packaging`, already a transitive dependency.
- Task runner: Invoke.

No new Python runtime dependency.

**Storage**: N/A, apart from Harbor (registry) and GitHub release metadata.

**Testing**: pytest.
- Unit tests for the pin tasks and the workflow contracts.
- `-m docker` for the image smoke test.
- `-m compose` (opt-in) for the deployment lifecycle.

**Target Platform**: GitHub-hosted runners `ubuntu-24.04` and `ubuntu-24.04-arm`. The
published image targets linux/amd64 and linux/arm64. The deployment needs Docker
Compose 2.24 or later.

**Project Type**: CLI and service distribution (build, release and deployment tooling).

**Performance Goals**: The PR image gate's wall-clock time is no worse than the
current amd64 build plus smoke, because the platforms build in parallel. No
hard target (low impact, per the clarify coverage).

**Constraints**:
- Secrets are only ever read by steps guarded by `publish`, and never echoed.
- Forks get no secrets and need none.
- The required check name `Full qualification` (job `qualification-required`) is
  unchanged.

**Scale/Scope**:

| | Count |
|---|---|
| Workflows rewritten or added | about 6 |
| Workflows removed | 2 |
| Task modules removed | 3 |
| Task module added | 1, small |
| Test files removed | about 60 |
| Test files rewritten | about 15 |
| Docs pages rewritten | about 20 |
| Docs pages removed | 2 |
| `deploy/compose/` | removed |

## Constitution Check

*GATE: must pass before Phase 0 research, and again after Phase 1 design.*

| Principle | Assessment | Status |
|---|---|---|
| I. Read-only and dry-run by default | No sync/apply behaviour changes. Publishing defaults to off (`publish: false`). PR runs never push. | Pass |
| II. Sync idempotency and safety | Release re-runs re-point the version tag without failing. The pin task is idempotent. The inline DB bootstrap stays idempotent. | Pass |
| III. Adapter symmetry and pattern consistency | Not touched. | N/A |
| IV. Type safety | The new `tasks/release.py` is fully typed and `ty`-clean. No overrides. | Pass |
| V. Test discipline | The pin tasks get parametrized unit tests. The workflow contracts are rewritten. The smoke test is reused. The Compose suite is retargeted rather than deleted. | Pass |
| VI. Secrets and input boundaries | Harbor credentials come from org secrets and only reach publish-guarded steps. Compose credentials are required with `:?` and never defaulted. Bootstrap SQL keeps bound variables. The smoke test checks logs for secrets. | Pass |
| VII. Simplicity | One sibling-shaped workflow replaces two bespoke ones. Net code shrinks by about 89% (research R11). No new abstraction, no new dependency. | Pass |
| Docs in the same change | Every affected page is rewritten or removed (R9). A changelog fragment is included. | Pass |
| Small, scoped commits | Five-step order (research, "Implementation order"). Removals are kept apart from additions. | Pass |

**Post-design re-check (after Phase 1)**: still a pass.
- The contracts add no committed secrets.
- The dropped `qualify` label lowers process complexity.
- The one behaviour trade-off, tag pinning instead of digest pinning for Compose, was
  the user's choice (Q5) and is recorded in the spec's Assumptions.

## Project Structure

### Documentation (this feature)

```text
specs/007-harbor-image-publishing/      # (specs → dev/specs symlink)
├── spec.md
├── plan.md                 # this file
├── research.md             # R1–R11 decisions
├── data-model.md           # version, image, tag, compose-file invariants
├── quickstart.md           # validation scenarios
├── contracts/
│   ├── ci-docker-image.md
│   ├── docker-compose-env.md
│   └── release-pin-tasks.md
├── checklists/requirements.md
└── tasks.md                # /speckit-tasks (not created here)
```

### Source code (repository root)

```text
.github/
├── file-filters.yml                 # REWRITE: image_inputs replaces image_files/compose_files/image_all/qualify_all
└── workflows/
    ├── ci-docker-image.yml          # ADD: reusable build → smoke → push → merge → sign → sbom
    ├── trigger-pr-develop.yml       # REWRITE: image job calls ci-docker-image (publish false); Full qualification keeps its name
    ├── workflow-publish.yml         # REWRITE: honour publish; add docker_meta + publish_docker_image
    ├── trigger-release.yml          # REWRITE: pass prerelease
    ├── release-publish.yml          # REWRITE: --prerelease for pre/dev versions; validate compose pin
    ├── trigger-push-stable.yml      # REWRITE: bump compose pin; git add docker-compose.yml
    ├── workflow-nightly-e2e.yml     # REWRITE (small): optionally run -m compose against a local build
    ├── workflow-image.yml           # REMOVE
    └── workflow-candidate.yml       # REMOVE

docker-compose.yml                   # ADD: operator deployment (from deploy/compose/compose.yaml, self-contained)
compose.yaml                         # MOVE → development/docker-compose.dev.yml
development/docker-compose.dev.yml   # dev stack, build context ..
deploy/compose/                      # REMOVE (wrapper, OPERATING.md, defaults.conf, bootstrap/, configuration/, skills/)
examples/tester_packet/              # REMOVE
Dockerfile, .dockerignore            # KEEP

tasks/
├── __init__.py                      # REWRITE: drop image/compose collections, register new release
├── release.py                       # REPLACE: update-/validate-docker-compose only
├── image.py, compose.py             # REMOVE
├── dev.py, netbox.py                # REWRITE: -f development/docker-compose.dev.yml
└── preview.py                       # KEEP

tests/
├── test_release_pin.py              # ADD
├── test_workflow_contracts.py       # REWRITE: drop candidate/packet/handoff/tier/kit contracts; add ci-docker-image + required-check contracts
├── image/                           # KEEP conftest.py (smoke fixtures) + test_image_artifact.py; REMOVE the rest
├── release/                         # REMOVE
├── compose/                         # REWRITE lifecycle suite against docker-compose.yml; REMOVE clean_host/, test_clean_host_*, test_qualification, test_preflight
└── dev_stack/                       # REWRITE paths

docs/docs/...                        # REWRITE ~20 pages, REMOVE 2 develop guides (+ sidebars.ts)
AGENTS.md, .github/copilot-instructions.md, README.md   # REWRITE candidate/compose prose
.gitignore, pyproject.toml           # drop .image/ .release/ entries and stale marker help text
changelog/+harbor-image-publishing.changed.md          # ADD
```

**Structure decision**: The existing single-project layout is kept. The work touches
CI, invoke tasks, the deployment file, tests and docs. Python under `infrahub_sync/`
does not change (the inventory confirmed that nothing there reads `image.bind` or the
release bundle).

## Rollout and external actions

These are outside the code change, and owned by maintainers or the registry
administrator:
1. Create the private Harbor project `opsmill/infrahub-sync`, and issue tester pull
   credentials (FR-015).
2. Confirm that the org variable `HARBOR_HOST` and the secrets `HARBOR_USERNAME` and
   `HARBOR_PASSWORD` are shared with this repository.
3. Branch protection needs no change: the job id and name of "Full qualification"
   are preserved.
4. At stable 3.0.0, make the Harbor project public, and remove the "log in until
   3.0.0" docs step.

## Risks

| Risk | Mitigation |
|---|---|
| The arm64 runner pool is unavailable to the org | Same runner label as infrahub-mcp. If it is unavailable, fall back to a QEMU build on amd64 for arm64, behind the same matrix entry. |
| Compose `configs.content` is unsupported on old operator hosts | The minimum is documented as Compose 2.24, and `test_compose_minimum.py` asserts it. |
| The lifecycle suite rewrite is large | It stays opt-in (`-m compose`), so the PR gate does not depend on it landing first (implementation-order step 3). |
| A release PR without the pin bump | `release-publish.yml` validates the pin and refuses to tag. |

## Complexity Tracking

No constitution violations to justify.
