# Implementation Plan: A Helm chart to run Infrahub Sync 3.x on Kubernetes

**Branch**: `018-helm-chart-kubernetes` | **Date**: 2026-11-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/018-helm-chart-kubernetes/spec.md`

## Summary

Add a chart `infrahub-sync` to `opsmill/infrahub-helm`. It deploys what the root
`docker-compose.yml` deploys: the Sync API, one worker, the Prefect server (all from the Sync
image), PostgreSQL and the Silo object store. PostgreSQL and the object store can each be turned
off for a service the team already runs.

The chart orders the start with a PostgreSQL init script, a bootstrap Job named after the release
revision, and init containers that wait ([research.md](research.md), R-2). Credentials live in one
Secret whose keys are the names of the Compose `.env` file (R-4).

This repository gets the operator page for the Kubernetes deployment, a decision record and a
changelog fragment.

## Technical Context

**Language/Version**: Helm chart (Go templates), chart API `v2`. Python 3.14 for the tests of
`infrahub-helm` (its CI version). MDX for the documentation page.

**Primary Dependencies**: Helm 3.8 or later (OCI registries) and Helm 4; the chart has no chart
dependency. Images: the Sync image, `postgres:16-alpine` and `pgsty/silo`, the last two at the
digests of `docker-compose.yml`.

**Storage**: PostgreSQL (product database and Prefect database) and an S3-compatible object
store, each on a `volumeClaimTemplate`, or external.

**Testing**: in `infrahub-helm`: `helm lint`; pytest render tests under `tests/unit` (`helm
template`); a pytest end-to-end test under `tests/e2e` with marker `sync`, in a vcluster. In this
repository: `uv run invoke lint`, `uv run invoke docs.docusaurus`, and the unit tier for any test
that reads the documentation.

**Target Platform**: Kubernetes, the versions that the `infrahub-helm` end-to-end harness runs.
`linux/amd64` and `linux/arm64`, the platforms of the Sync image.

**Project Type**: deployment artifact (Helm chart) and operator documentation, in two
repositories.

**Performance Goals**: a default install is ready within 10 minutes on a single-node test
cluster (SC-001).

**Constraints**: no credential in the default values; no Kubernetes API access from the pods; one
change to the Sync image, the bootstrap record and its start check (FR-007b, Phase 9 of
[tasks.md](tasks.md)); the properties of
[contracts/rendered-release.md](contracts/rendered-release.md).

**Scale/Scope**: one chart of about 15 templates, about 40 render tests, one end-to-end test, one
documentation page.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
| --- | --- |
| I. Read-only and dry-run by default | Pass. The chart starts the service; it registers no configuration and writes to no destination. The bootstrap reaches no destination. |
| II. Sync idempotency and safety | Pass. The bootstrap Job is safe to repeat (R-1); each upgrade runs it once more. |
| III. Adapter symmetry | Not applicable. No adapter changes. |
| IV. Type safety and explicit contracts | Pass. The values and the rendered release each have a written contract, and render-time validation names the value in each refusal. |
| V. Test discipline | Pass. Render tests cover each contract property; one end-to-end test covers install and upgrade. The end-to-end test is opt-in by marker. |
| VI. Security, secrets and input boundaries | Pass. No default credential. Credentials reach containers only through Secret references. Source credentials reach the worker only. |
| VII. Simplicity and maintainability | Pass with one note. The chart writes its own PostgreSQL and object store StatefulSets where a dependency chart exists; R-5 gives the reason. |

Post-design re-check: no change. No violation needs the Complexity Tracking table.

## Project Structure

### Documentation (this feature)

```text
dev/specs/018-helm-chart-kubernetes/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── values.md
│   └── rendered-release.md
└── tasks.md
```

### Source Code

Two repositories. `infrahub-helm` is checked out at
`/Users/bkohler/automation/opsmill/infrahub-helm-sync-chart`, a worktree on branch
`feat/infrahub-sync-chart`, created from `origin/stable`.

```text
infrahub-helm/
├── charts/infrahub-sync/
│   ├── Chart.yaml                     # version 0.1.0, appVersion "3.0.0a5"
│   ├── values.yaml                    # every value with a `# --` comment
│   ├── README.md.gotmpl, README.md    # README.md generated with helm-docs
│   ├── CHANGELOG.md, towncrier.toml, changelog/
│   ├── .helmignore                    # excludes ci/
│   ├── ci/lint-values.yaml            # values for helm lint
│   └── templates/
│       ├── _helpers.tpl               # names, labels, image, env blocks, init containers
│       ├── _validation.tpl            # the render-time refusals
│       ├── NOTES.txt
│       ├── serviceaccount.yaml
│       ├── secret.yaml                # credentials, when no existing Secret
│       ├── worker-credentials-secret.yaml
│       ├── ca-configmap.yaml
│       ├── api.yaml                   # Deployment and Service
│       ├── worker.yaml
│       ├── prefect-server.yaml        # Deployment and Service
│       ├── bootstrap-job.yaml
│       ├── postgresql.yaml            # StatefulSet, Service, init script ConfigMap
│       ├── object-store.yaml          # StatefulSet and Service
│       ├── ingress.yaml, httproute.yaml, gateway.yaml
├── tests/unit/test_infrahub_sync_chart.py   # render tests
├── tests/e2e/test_infrahub_sync.py          # marker `sync`
├── tests/e2e/conftest.py, tests/README.md, pyproject.toml   # register the chart and the marker
├── .github/workflows/ci.yml                 # filter, lint job, e2e job
├── Makefile                                 # lint loop passes ci/lint-values.yaml
├── .github/workflows/update-infrahub-sync.yml
└── AGENTS.md, README.md                     # name the new chart

infrahub-sync/  (this repository)
├── docs/docs/kubernetes-deployment.mdx
├── docs/sidebars.ts                         # entry after 'compose-deployment'
├── docs/docs/compose-deployment.mdx         # one link to the new page; "What this topology does not do"
├── dev/adr/0043-…md                         # the start order of the Kubernetes deployment
└── changelog/422.added.md
```

**Structure Decision**: the chart follows `charts/infrahub-mcp` for naming helpers, labels, the
service account, the Ingress and the Gateway API templates, and the release files. One template
file per component, because each component is one or two resources.

## Design

The design is in the Phase 0 and Phase 1 artifacts:

- Start order, credentials, PostgreSQL and object store, versions, tests:
  [research.md](research.md).
- Resources, Secret keys and validation rules: [data-model.md](data-model.md).
- Values: [contracts/values.md](contracts/values.md).
- Properties that the render tests assert:
  [contracts/rendered-release.md](contracts/rendered-release.md).
- How to validate on a cluster: [quickstart.md](quickstart.md).

Points an implementer must not miss:

1. **Environment, per container.** Build the environment from named helper blocks (storage,
   Prefect client, S3) so that the Sync API, the worker and the bootstrap Job get the same
   endpoints, as the `x-sync-storage` anchor does in Compose. Define the password variable before
   the connection string that expands it with `$(VAR)`.
2. **Sync API**: `INFRAHUB_SYNC_SERVICE_HOST=0.0.0.0`; container port 8000.
3. **The bootstrap record** (FR-007b, clarification of 2026-11-16) replaces the gate init
   container. The bootstrap writes the Sync version and the work pool name to the product
   database as its last step. The Sync API and the worker read it first at start, before the
   product schema initialization, and exit non-zero on a missing or different record, so
   Kubernetes restarts them until the Job has written it. The `waitForPostgresql` and
   `waitForPrefect` init containers stay, so the restart backoff only starts once those
   services answer.
4. **PostgreSQL**: `PGDATA` is a subdirectory of the mount, because a new volume can hold
   `lost+found`. The init script is the one in `docker-compose.yml:390-424` with `$$` written
   `$`. It reads its values from the container environment and binds each one as a `psql`
   variable. It is mounted with mode `0555`, and the container sets `PGUSER` and `PGDATABASE` but
   not `PGHOST` (research R-2, "Details of the init script").
5. **Object store**: `MINIO_BROWSER=off`, command `server /data`, user 65532 with `fsGroup`
   65532, readiness on the Silo health endpoint `/minio/health/ready`.
6. **The Job name** ends with the hash of research R-2. The Secret and ConfigMap checksums used
   in the hash are the same values as the checksum annotations of point 7.
7. **Checksum annotations** on the pod templates for the Secret and ConfigMaps the chart
   creates, so that a changed value restarts the pods.
8. **Every pod** sets `enableServiceLinks: false` and `automountServiceAccountToken: false`. The
   Prefect server and the worker use the `Recreate` strategy (research R-2).
9. **Lint**: `charts/infrahub-sync/ci/lint-values.yaml` names an existing Secret. `.helmignore`
   excludes `ci/`. The Makefile `lint` loop and the new CI lint job pass the file when it exists
   (research R-7).
10. **NOTES.txt** prints how to reach the Sync API with a port-forward and how to run the CLI in a
    pod, and nothing that is a credential.

## Verification

1. `helm lint charts/infrahub-sync`, and the render tests.
2. `helm-docs` regenerates the README with no difference left.
3. Install on a local cluster as in [quickstart.md](quickstart.md): the Job completes, `/version`
   answers, `infrahub-sync whoami` reports the principal, one live worker is listed. Then
   `helm upgrade`, and the same checks. Then the external variant render, and an uninstall.
4. In this repository: `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`,
   `uv run invoke docs.docusaurus`, and the unit tier.

The image for step 3 is built from this repository's `Dockerfile` and loaded into the cluster.
The default `appVersion` image was not in the registry on 2026-11-16 (research R-6), so pulling
it is not part of this verification. If the image cannot be built, step 3 is reported as not
done.

## Release order

1. Merge the documentation and the decision record of this repository.
2. Merge the chart into `infrahub-helm`. It is not published yet.
3. After the first Sync release that contains the Prefect server in the image and the bootstrap
   record check (FR-007b) is in the registry, a maintainer sets `appVersion` to it and runs the
   publish workflow for `infrahub-sync`. The chart has no start ordering of its own for an image
   without that check, so the chart documents that release as the minimum Sync version.

## Complexity Tracking

No violation to justify.
