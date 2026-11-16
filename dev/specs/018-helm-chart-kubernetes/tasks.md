# Tasks: A Helm chart to run Infrahub Sync 3.x on Kubernetes

**Input**: Design documents from `dev/specs/018-helm-chart-kubernetes/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/values.md](contracts/values.md),
[contracts/rendered-release.md](contracts/rendered-release.md), [quickstart.md](quickstart.md)

**Tests**: requested. FR-029 asks for a lint job and an end-to-end test, and the plan adds render
tests for each property of [contracts/rendered-release.md](contracts/rendered-release.md).

**Organization**: tasks are grouped by user story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story the task belongs to (US1 to US4)

## Path conventions

Two repositories. Every path in a task starts with one of these prefixes:

- `H/` is `/Users/bkohler/automation/opsmill/infrahub-helm-sync-chart`, a worktree of
  `opsmill/infrahub-helm` on branch `feat/infrahub-sync-chart`, created from `origin/stable`.
- `S/` is `/Users/bkohler/automation/opsmill/infrahub-sync/.worktrees/018-helm-chart-kubernetes`,
  this repository on branch `018-helm-chart-kubernetes`.

`C/` is `H/charts/infrahub-sync`.

Rules for every task:

- Read `H/AGENTS.md` before the first change in `H/`. Commits in `H/` use Conventional Commits with
  the chart as scope, for example `feat(infrahub-sync): ...`.
- Commit with explicit paths (`git commit -- <paths>`). Do not push, do not open a pull request,
  do not use `git stash`.
- The source of truth for what the chart deploys is `S/docker-compose.yml`. Copy its digests,
  commands, variable names and comments' intent; do not invent settings.

---

## Phase 1: Setup

**Purpose**: chart skeleton, registration in `infrahub-helm`, and the test image.

- [X] T001 Create the chart skeleton in `C/`:
  - `Chart.yaml`, modelled on `H/charts/infrahub-mcp/Chart.yaml`, with `name: infrahub-sync`,
    `version: 0.1.0` and `appVersion: "3.0.0a5"` (the `version` of `S/pyproject.toml`). Use the
    description "A Helm chart to deploy Infrahub Sync on Kubernetes", `sources` pointing at
    `https://github.com/opsmill/infrahub-sync`, and keywords `infrahub`, `sync`.
  - `.helmignore`, copied from infrahub-mcp, with a `ci/` line added.
  - `towncrier.toml`, copied from infrahub-mcp, with `name = "Infrahub Sync"` and the title format
    `infrahub-sync-{version}`.
  - `changelog/.gitignore`, copied.
  - `CHANGELOG.md`, holding a `# Changelog` heading and the `<!-- towncrier release notes start -->`
    line that `towncrier.toml` sets as `start_string`.
- [X] T002 [P] Register the chart everywhere `infrahub-helm` lists charts by hand:
  - Add the marker `"sync: tests for the infrahub-sync chart"` to `H/pyproject.toml`
    `[tool.pytest.ini_options].markers`.
  - Add a row for `infrahub-sync` and `sync` to the chart table in `H/tests/README.md`.
  - In `H/AGENTS.md`, add `infrahub-sync` to the "Charts" list as a standalone chart (not a
    dependency of `infrahub`) and `sync` to the marker list.
  - Add `infrahub-sync` to `LOCAL_CHART_NAMES` in `H/tests/e2e/conftest.py` only if the harness
    needs it to install a local chart; read how `infrahub-mcp` is installed first.
- [X] T003 [P] Build the Sync image from this repository for local verification:
  `docker build -t infrahub-sync:chart-test S/`, run in the background. Then check it:
  `docker run --rm infrahub-sync:chart-test python -c "import infrahub_sync.service.prefect_server, infrahub_sync.service.trust_store"`.
  Record the image ID in the task notes.

---

## Phase 2: Foundational

**Purpose**: values, helpers, validation, Secrets and the render test harness. Every user story
depends on this phase.

- [X] T004 Write `C/values.yaml` exactly as [contracts/values.md](contracts/values.md) specifies.
  - Describe every value with a `# --` comment, the helm-docs format of
    `H/charts/infrahub-mcp/values.yaml`.
  - Take the PostgreSQL image from `S/docker-compose.yml:76` and the Silo image from
    `S/docker-compose.yml:120-121`, each split into `repository`, `tag` and `digest`.
  - Default every credential to `""`.
  - Make the `ingress` and `gatewayApi` blocks copies of the infrahub-mcp blocks, with
    `enabled: false`.
  - Set `podSecurityContext` to `runAsNonRoot: true`, `runAsUser: 10001`, `runAsGroup: 10001` and
    `seccompProfile: RuntimeDefault`.
  - Set `securityContext` to `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false` and
    `capabilities.drop: [ALL]`.
- [X] T005 Write `C/templates/_helpers.tpl`.
  - **Naming, labels and image**: `infrahub-sync.name`, `.fullname`, `.chart`, `.labels`,
    `.selectorLabels` and `.serviceAccountName`, as in infrahub-mcp. Add a component argument;
    the label is `app.kubernetes.io/component`. The `.image` helper gives
    `repository:tag`, and the tag defaults to `.Chart.AppVersion`. Add a `.digestImage` helper
    for `repository:tag@digest`.
  - **`infrahub-sync.secretName`**: the existing Secret, or `<fullname>`.
  - **`infrahub-sync.env.storage`**: the `x-sync-storage` anchor of `S/docker-compose.yml:42-66`
    as a list.
    - With the bundled PostgreSQL, list `INFRAHUB_SYNC_PRODUCT_PASSWORD` from `secretKeyRef`
      first. Then build `INFRAHUB_SYNC_DATABASE_URL` from it with `$(INFRAHUB_SYNC_PRODUCT_PASSWORD)`,
      `@<fullname>-postgresql:5432/<productDatabase>` and the role.
    - With an external PostgreSQL, read `INFRAHUB_SYNC_DATABASE_URL` from `secretKeyRef`.
    - S3: the bucket, the prefix and the region. The endpoint is
      `http://<fullname>-object-store:9000` with the bundled store, or `s3.endpointUrl` otherwise.
      The addressing style is `path` with the bundled store, or `s3.addressingStyle` when set.
      Leave out any optional variable that is not set (RR-22).
    - `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from the Secret keys
      `INFRAHUB_SYNC_S3_ACCESS_KEY` and `INFRAHUB_SYNC_S3_SECRET_KEY`.
    - `INFRAHUB_SYNC_SERVICE_WORK_POOL`.
    - `PREFECT_API_URL=http://<fullname>-prefect-server:4200/api`, and `PREFECT_API_AUTH_STRING`
      from the Secret.
  - **`infrahub-sync.env.ca`, `.volumes.ca` and `.mounts.ca`**: `INFRAHUB_SYNC_CA_DIRECTORY=/etc/infrahub-sync/ca`
    and a read-only ConfigMap mount, only when `caCertificates.existingConfigMap` or
    `caCertificates.certificates` is set (RR-21).
  - **`infrahub-sync.volumes.scratch` and `.mounts.scratch`**: three `emptyDir` volumes with
    `medium: Memory`, at `/var/lib/infrahub-sync`, `/var/lib/infrahub-sync/prefect` and
    `/tmp/infrahub-sync` (RR-06).
  - **`infrahub-sync.initContainer.waitForBootstrap`**: the gate of plan design point 3, a
    `python -c` script in the Sync image.
    - It loops on `get_client().read_deployment_by_name("infrahub-sync-service/run")` until the
      deployment's `work_pool_name` equals the pool, or until `waitTimeoutSeconds`.
    - It prints the pool name and the elapsed time, never a credential.
    - It receives only `PREFECT_API_URL`, `PREFECT_API_AUTH_STRING`, the pool and the scratch
      mounts, and it has the same security context as the Sync containers.
  - **`infrahub-sync.initContainer.waitForPrefect`**: polls `/api/health` with `httpx` in the Sync
    image.
  - **`infrahub-sync.initContainer.waitForPostgresql`**: `pg_isready` in the PostgreSQL image,
    against `<fullname>-postgresql:5432`.
  - **`infrahub-sync.checksums`**: sha256 of the rendered Secret, worker credentials Secret and CA
    ConfigMap that the chart creates. Pods use it as an annotation.
  - **`infrahub-sync.bootstrapHash`**: the first 8 characters of a sha256 over the image
    reference, the bootstrap's environment values that are not credentials, and the checksums
    (research R-2, RR-34).
- [X] T006 Write `C/templates/_validation.tpl` with one `fail` per refusal of
  [data-model.md](data-model.md) "Validation rules" and RR-40 to RR-45.
  - Each message names the value.
  - Without an existing Secret, use `required` for each credential of FR-012. With the bundled
    PostgreSQL that is the three PostgreSQL passwords; with an external one, the two connection
    strings.
  - Include the template from every workload template, so `helm template` runs it.
- [X] T007 [P] Write the Secret, ConfigMap and service account templates:
  - `C/templates/secret.yaml`, rendered when `credentials.existingSecret` is empty. Its keys are
    the names in [data-model.md](data-model.md) "Keys of the credentials Secret", and it holds
    only the keys that are set.
  - `C/templates/worker-credentials-secret.yaml`, rendered when `worker.credentials` is not empty.
  - `C/templates/ca-configmap.yaml`, rendered when `caCertificates.certificates` is not empty.
  - `C/templates/serviceaccount.yaml`, copied from infrahub-mcp, with
    `automountServiceAccountToken: false`.
- [X] T008 [P] Create the render test harness and the lint values:
  - `H/tests/unit/test_infrahub_sync_chart.py` with a helper `render(values: dict, *, expect_failure: bool = False) -> list[dict]`.
    It writes the values to a temporary file, runs `helm template sync charts/infrahub-sync -f <file>`,
    and parses the documents with `yaml.safe_load_all`. On failure it returns the error text.
    It also has a fixture of minimal valid values with test credentials such as `"test-" * 4`,
    and a fixture with `credentials.existingSecret` set. Use only libraries already in
    `H/pyproject.toml`; check it first.
  - `C/ci/lint-values.yaml`, which sets `credentials.existingSecret: infrahub-sync`.

**Checkpoint**: `helm template sync C/ -f C/ci/lint-values.yaml` renders, though without
workloads yet.

---

## Phase 3: User Story 1, install Infrahub Sync on a cluster (Priority: P1) 🎯 MVP

**Goal**: a default install with only the required credentials gives a ready release that
answers `/version` and `whoami`.

**Independent test**: [quickstart.md](quickstart.md) steps 1 to 3.

### Tests for User Story 1

- [X] T009 [US1] Add render tests to `H/tests/unit/test_infrahub_sync_chart.py`: one test per
  property RR-01 to RR-17, RR-19 to RR-26, RR-40, RR-43 and RR-45 of
  [contracts/rendered-release.md](contracts/rendered-release.md). Parametrize where the property
  is the same for several pods. Write them first; they fail until T010 to T015 are done.

### Implementation for User Story 1

- [X] T010 [P] [US1] Write `C/templates/postgresql.yaml`, rendered when `postgresql.enabled`.
  - **ConfigMap**: holds `databases.sh`, the script in `S/docker-compose.yml:392-424` with every
    `$$` written `$`.
  - **Service**: `<fullname>-postgresql`, port 5432.
  - **StatefulSet**:
    - one replica, the digest image, `fsGroup: 70` and `runAsUser: 70`
    - `POSTGRES_USER` and `POSTGRES_DB` set to `postgresql.adminRole`, and `PGUSER` and
      `PGDATABASE` set to the same role, with no `PGHOST` (RR-25)
    - `POSTGRES_PASSWORD` from the Secret, and the role, database and password variables that the
      script reads, as in `S/docker-compose.yml:103-108`
    - `PGDATA=/var/lib/postgresql/data/pgdata`
    - the script mounted at `/docker-entrypoint-initdb.d/databases.sh` with `defaultMode: 0555`
    - a `pg_isready` readiness probe
    - a `volumeClaimTemplate` of `postgresql.persistence.size` and `storageClass`
    - `enableServiceLinks: false` and `automountServiceAccountToken: false`
- [X] T011 [P] [US1] Write `C/templates/object-store.yaml`, rendered when `objectStore.enabled`.
  - **Service**: `<fullname>-object-store`, port 9000.
  - **StatefulSet**:
    - one replica, the digest Silo image, `runAsUser` and `runAsGroup` 65532, and
      `fsGroup: 65532`
    - command `server /data`
    - `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` from the Secret's S3 keys, and
      `MINIO_BROWSER=off`
    - a readiness probe on HTTP `/minio/health/ready`
    - a `volumeClaimTemplate`
    - `enableServiceLinks: false` and `automountServiceAccountToken: false`
- [X] T012 [P] [US1] Write `C/templates/prefect-server.yaml`.
  - **Deployment**:
    - one replica and strategy `Recreate`
    - the command of `S/docker-compose.yml:173-180`
    - the `waitForPostgresql` init container, only with the bundled PostgreSQL
    - `PREFECT_SERVER_DATABASE_CONNECTION_URL`, built as in `S/docker-compose.yml:188-191` from
      `$(INFRAHUB_SYNC_PREFECT_PASSWORD)` defined first, or read from the Secret key
      `INFRAHUB_SYNC_PREFECT_DATABASE_URL`
    - `PREFECT_SERVER_API_HOST=0.0.0.0` and `PREFECT_SERVER_API_PORT=4200`
    - `PREFECT_UI_ENABLED` from `prefectServer.uiEnabled`
    - `PREFECT_SERVER_UI_API_URL=http://127.0.0.1:4200/api`
    - `PREFECT_SERVER_ANALYTICS_ENABLED=false`
    - `PREFECT_SERVER_API_AUTH_STRING` from the Secret
    - readiness and liveness probes on HTTP `/api/health`, port 4200
    - the scratch volumes and the Sync security context
  - **Service**: `<fullname>-prefect-server`, port 4200.
- [X] T013 [US1] Write `C/templates/bootstrap-job.yaml`.
  - The Job is named `<fullname>-bootstrap-<bootstrapHash>` and has the label
    `app.kubernetes.io/component: bootstrap`.
  - `backoffLimit` from `bootstrap.backoffLimit`, and `restartPolicy: OnFailure`.
  - The `waitForPrefect` init container.
  - Command `python -m infrahub_sync.service.bootstrap`.
  - The storage and CA environment, the scratch and CA volumes, and the Sync security context.
  - No Helm hook annotation. Depends on T005.
- [X] T014 [P] [US1] Write the Sync API in `C/templates/api.yaml`.
  - **Deployment**:
    - `api.replicaCount` and strategy `RollingUpdate`
    - the `waitForBootstrap` init container
    - the image's default command
    - the storage and CA environment, then `INFRAHUB_SYNC_SERVICE_HOST=0.0.0.0`
    - `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` from the Secret
    - `INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS`, `INFRAHUB_SYNC_REQUIRE_APPROVAL` and
      `PREFECT_WORKER_QUERY_SECONDS`
    - the optional `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` from the Secret, with
      `optional: true`
    - `INFRAHUB_SYNC_PREFECT_UI_URL` only when it is set
    - container port 8000, and readiness and liveness probes on HTTP `/version`
    - the checksum annotation
  - **Service**: `<fullname>-api`, of `api.service.type`, port `api.service.port` to 8000.
- [X] T015 [P] [US1] Write `C/templates/worker.yaml`.
  - **Deployment**:
    - `worker.replicaCount` and strategy `Recreate`
    - the `waitForBootstrap` init container
    - command `python -m infrahub_sync.service.worker --pool <settings.workPool>`
    - the storage and CA environment
    - `INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS`, `PREFECT_WORKER_QUERY_SECONDS` and
      `PREFECT_WORKER_HEARTBEAT_SECONDS`
    - the optional destination credential
    - `envFrom` for the worker credentials Secret and for each of
      `worker.credentialsExistingSecrets`
    - no probe, as in Compose
    - the checksum annotation
- [X] T016 [US1] Write `C/templates/NOTES.txt`. It prints the port-forward command to the API
  Service. It also prints a `kubectl run --rm -it` command that runs `infrahub-sync whoami` from
  the Sync image with only `INFRAHUB_SYNC_API_URL` and `INFRAHUB_SYNC_API_TOKEN`, and a token
  placeholder, never a value. Last, it prints the command to wait for the bootstrap Job.
- [X] T017 [US1] Make every test of T009 pass: `cd H && uv run pytest -v tests/unit/test_infrahub_sync_chart.py`.
  Then run `helm lint C/ -f C/ci/lint-values.yaml`.
- [X] T018 [US1] Verify the install on a local cluster with [quickstart.md](quickstart.md)
  steps 2 and 3. Use the image of T003 (`minikube image load infrahub-sync:chart-test`,
  `--set image.repository=infrahub-sync --set image.tag=chart-test --set image.pullPolicy=Never`).
  Record in `S/dev/specs/018-helm-chart-kubernetes/verification.md` the commands, the pod list, the
  Job status, and the output of `/version`, `/status` and `whoami`. Redact the token. Fix the
  templates until every check passes. If the image cannot be built, record that and stop this
  task.

**Checkpoint**: User Story 1 works on a local cluster.

---

## Phase 4: User Story 2, upgrade (Priority: P1)

**Goal**: `helm upgrade` completes with no manual step. It runs the bootstrap again when the image
or one of the bootstrap's settings changes, and it keeps the data.

**Independent test**: [quickstart.md](quickstart.md) step 4.

- [X] T019 [US2] Add the render tests of RR-34 to `H/tests/unit/test_infrahub_sync_chart.py`.
  - The Job name changes with `image.tag`, with `s3.prefix` and with a credential value.
  - It does not change with `api.resources` or with `--set` of a revision. Use
    `helm template --revision` if it is available; otherwise compare two renders, because the
    revision does not enter the hash.
- [X] T020 [US2] Write the end-to-end test `H/tests/e2e/test_infrahub_sync.py` with marker
  `sync`. Follow the structure and fixtures of `H/tests/e2e/test_infrahub_mcp.py`.
  1. Create the credentials Secret, with a generated token and passwords.
  2. When `INFRAHUB_SYNC_REGISTRY_USERNAME` and `INFRAHUB_SYNC_REGISTRY_PASSWORD` are set, create
     an image pull Secret.
  3. Resolve the image from `INFRAHUB_SYNC_E2E_IMAGE`, or from the chart's `appVersion`.
  4. Skip with a message when the image cannot be resolved and no credential is given.
  5. Install, then wait for the bootstrap Job with `kubectl wait --for=condition=complete`.
  6. Assert `/version`, `/whoami` with the token, and `worker.state == "ready"` from `/status`.
  7. Upgrade with a changed `s3.prefix`, wait for the new Job, and assert again.

  After the image resolves, a pod that is not ready fails the test (research R-7).
- [X] T021 [US2] Verify the upgrade on the local cluster with [quickstart.md](quickstart.md)
  step 4: both upgrades, and the rollout restart that creates no Job.
  - Before the upgrades, register a configuration package with `infrahub-sync configs register`.
    Use a small package from `S/examples/` and read `S/docs/docs/compose-deployment.mdx`
    "Registering a package" for the command.
  - Check that the package is still listed afterwards.
  - Record the result in `verification.md`.
- [X] T022 [P] [US2] Add `H/.github/workflows/update-infrahub-sync.yml`, a copy of
  `H/.github/workflows/update-infrahub-mcp.yml`:
  - Change the event to `infrahub-sync-released`, and the source repository and image to
    `opsmill/infrahub-sync`.
  - The release tag has no `v` prefix (`3.0.0`). Read how `H/scripts/bump_chart.py` parses
    versions, and change the version check to match.
  - Keep the header comment, with its note that the chart is published by "Publish Helm Chart"
    after the merge.

**Checkpoint**: User Stories 1 and 2 work.

---

## Phase 5: User Story 3, external PostgreSQL and object store (Priority: P2)

**Goal**: with either bundled service turned off, the release uses the team's own service.

**Independent test**: the render tests below and [quickstart.md](quickstart.md) step 5.

- [X] T023 [US3] Add the render tests RR-21, RR-30, RR-31, RR-32, RR-41, RR-42 and RR-44 to
  `H/tests/unit/test_infrahub_sync_chart.py`, and make them pass.
- [X] T024 [US3] Run [quickstart.md](quickstart.md) step 5 and record the output in
  `verification.md`.
  - Then install the variant `objectStore.enabled=false` on the local cluster. Point it at a Silo
    StatefulSet from a second release of the chart, in another namespace, with only
    `objectStore.enabled` set. Pass the in-cluster address of that release as `s3.endpointUrl`.
  - Check that the bootstrap completes and that the release has no object store pod.
  - If that takes more than a short attempt, record the render-level evidence only and say so.

**Checkpoint**: User Stories 1 to 3 work.

---

## Phase 6: User Story 4, reach the Sync API from outside the cluster (Priority: P3)

**Goal**: an optional Ingress or Gateway API route to the Sync API, and no route to anything
else.

**Independent test**: render tests RR-18 and RR-33.

- [X] T025 [P] [US4] Copy `ingress.yaml`, `httproute.yaml` and `gateway.yaml` from
  `H/charts/infrahub-mcp/templates/` to `C/templates/`. Rename the helpers, and point every
  backend at `<fullname>-api` and `api.service.port`.
- [X] T026 [US4] Add the render tests RR-18 and RR-33 to `H/tests/unit/test_infrahub_sync_chart.py`,
  and make them pass.

---

## Phase 7: Documentation, CI and release files

**Purpose**: FR-028 to FR-033.

- [X] T027 [P] Write `C/README.md.gotmpl`, modelled on infrahub-mcp. Cover:
  - what the chart deploys
  - the required Secret, with its keys
  - an install example with `credentials.existingSecret`
  - a link to the Infrahub Sync documentation page of T030
  - the note that the image is restricted before 3.0.0

  Then generate `C/README.md` with `helm-docs --chart-search-root=charts/infrahub-sync`. Run it
  in `H/`.
- [X] T028 [P] In `H/`, add the chart to the root `README.md` list, in the format of the existing
  entries. Add the fragment `C/changelog/+infrahub-sync-chart.added.md`, created with
  `uvx towncrier create --config charts/infrahub-sync/towncrier.toml +infrahub-sync-chart.added.md`.
  Its text: "Add the `infrahub-sync` chart, which deploys Infrahub Sync 3.x with its Prefect
  server, PostgreSQL and object store."
- [X] T029 [P] CI in `H/`.
  - In `.github/workflows/ci.yml`:
    - Add a `sync` output and path filter (`charts/infrahub-sync/**`) to the `changes` job.
    - Add a `helm-lint-sync` job that runs
      `helm lint charts/infrahub-sync -f charts/infrahub-sync/ci/lint-values.yaml`.
    - Add an `e2e-infrahub-sync` job that runs `uv run pytest -v -m sync tests/e2e`.
    - Copy the structure of the mcp jobs.
    - The existing `unit` job already runs `tests/unit`.
  - In `Makefile`, make the `lint` loop pass `-f "$$chart/ci/lint-values.yaml"` when that file
    exists.
  - Run `make lint`.
- [X] T030 [P] Write `S/docs/docs/kubernetes-deployment.mdx`, titled "Kubernetes deployment".
  - Match the style of `S/docs/docs/compose-deployment.mdx`: frontmatter `title` only, and the
    same admonition syntax.
  - Sections:
    - What the release contains
    - Prerequisites, including the image access before 3.0.0 and an image pull Secret
    - Create the credentials Secret, with the table of keys from
      [data-model.md](data-model.md); `--from-env-file` reuses a Compose `.env`
    - Install
    - Call the Sync API: a port-forward and the CLI on a workstation, or `kubectl run` in the
      cluster
    - Settings
    - Use your own PostgreSQL: the two databases and roles the operator creates, and the two
      connection strings
    - Use your own S3-compatible store
    - Trust a private certificate authority
    - Reach the Sync API from outside the cluster
    - Upgrade: chart version to Sync version through `helm show chart`; check the runs in
      progress before an upgrade; link to "When the outcome of a write is uncertain"; state
      persistence across Sync versions as in the spec's User Story 2; how `helm --wait` treats
      the Job, verified in T018 and T021
    - Uninstall, and what happens to the volume claims
    - What this deployment does not do: the web interface (issue #421); more than one replica,
      not tested; OpenShift, not tested; backups
  - Follow `S/docs/docs/development/` writing guidance if it exists, and the organization writing
    rules: plain words and the most specific term.
- [X] T031 [P] Update the existing documentation in `S/`:
  - In `docs/sidebars.ts`, add `'kubernetes-deployment'` after `'compose-deployment'`.
  - In `docs/docs/compose-deployment.mdx`, link to the new page from "Related operator pages" and
    from "What this topology does not do".
- [X] T032 [P] Write the decision record `S/dev/adr/<next number>-the-kubernetes-deployment-orders-its-start-with-a-hashed-job-and-gates.md`.
  - Pick the next free number when you commit; another branch may take 0043.
  - Use the format of `S/dev/adr/0016-the-compose-deployment-is-one-tag-pinned-file.md`, and take
    the content from research R-2 and R-3: the context, the decision, the consequences including
    the upgrade gap, and the alternatives.
  - Cite no internal source: no Slack, Jira or Claude links.
  - Add it to the index in `S/dev/adr/README.md`.
- [X] T033 [P] Add `S/changelog/422.added.md`, created with
  `uv run --extra dev towncrier create -c "..." 422.added.md`. Use one sentence that says
  operators can deploy Infrahub Sync on Kubernetes with the `infrahub-sync` Helm chart, and links
  to the new page by its path.

---

## Phase 9: Bootstrap record and start check (clarification of 2026-11-16)

These tasks implement FR-007b and the FR-029 change from the clarification session of
2026-11-16 in [spec.md](spec.md). They come before Phase 8, which runs last.

### Chunk 9a: Infrahub Sync (`S/`)

- [X] T038 [US2] In the product store (`S/infrahub_sync/product_store/store.py` and its models),
  add a single-row table for the bootstrap record: the Sync version, the work pool name and the
  completion time. Add a write that replaces the row, and a read that works on a plain
  connection and does not run the schema initialization or the column migration. A read on a
  database where the table does not exist returns "no record", not an error. Unit tests in
  `S/tests/` next to the existing product store tests.
- [X] T039 [US2] In `S/infrahub_sync/service/bootstrap.py`, write the record as the last step of
  `_converge()`, after the product schema step, with `installed_server_version()` from
  `S/infrahub_sync/service/compatibility.py` and the work pool name. A failure to write it is a
  bootstrap failure with a fixed family. Unit tests in `S/tests/service/test_compose_bootstrap.py`.
- [X] T040 [US2] Add the start check, in one function shared by the Sync API and the worker:
  - In `build_app` in `S/infrahub_sync/service/serve.py`, run it before `projection_factory()`,
    so a refused start changes nothing in the database. It must refuse both through `main()` and
    through `uvicorn --factory`.
  - In `main` in `S/infrahub_sync/service/worker.py`, run it before the worker is built, with the
    pool from `_pool_argument`.
  - On a missing or different record: log the version and pool found and the ones expected,
    never a credential or the connection string, and exit non-zero.
  - Make the check injectable (a parameter with a default), so the existing `S/tests/service`
    tests keep passing without a database.
  - Unit tests: record matches, record missing, table missing, other version, other pool, and
    the database cannot be reached.
- [X] T041 [US2] Run one Compose lifecycle test from `S/tests/compose` locally to show that the
  Compose deployment still starts with the check (its `depends_on` order runs the bootstrap
  first). If it cannot run locally, say why and give the command.
- [X] T042 [P] [US2] Add the changelog fragment `S/changelog/+bootstrap-record.added.md`: the
  bootstrap records the version and work pool it set up, and the Sync API and the worker refuse
  to start when the record is missing or different.

### Chunk 9b: the chart (`H/`)

- [X] T043 [US2] Remove the `waitForBootstrap` init container from the Sync API and the worker
  in `C/templates/` and `_helpers.tpl`. Keep `waitForPostgresql` and `waitForPrefect`. Remove any
  value that only the bootstrap wait reads from `C/values.yaml`, regenerate `C/README.md` with
  helm-docs, and add a chart changelog fragment.
- [X] T044 [US2] Update the render tests in `H/tests/unit/test_infrahub_sync_chart.py` and
  RR-19 in [contracts/rendered-release.md](contracts/rendered-release.md) (and
  [contracts/values.md](contracts/values.md) for removed values): the Sync API and the worker
  have no init container that waits for the bootstrap, and the bootstrap is not an init
  container of any pod.
- [X] T045 [P] [US2] In `H/.github/workflows/ci.yml`, when the `e2e-infrahub-sync` job has no
  registry login, write a warning annotation (`::warning::`) and a line to
  `$GITHUB_STEP_SUMMARY` that say the end-to-end test did not run because the Sync image needs a
  registry login until release 3.0.0 (FR-029).

### Chunk 9c: verification on the cluster

- [ ] T046 [US2] Rebuild the image from `S/` as `infrahub-sync:chart-test-r1` and load it into
  minikube profile `sync-chart`. Then build a second image whose package reports another version
  (a throwaway version change that is not committed) as `infrahub-sync:chart-test-r2`, and load
  it. Use new tags; do not reuse a tag that minikube already holds.
- [ ] T047 [US2] On the cluster, with the chart of T043:
  - Install a fresh release with `chart-test-r1`. Record the restart count of the Sync API and the
    worker and the time from the Job's completion to their readiness.
  - Upgrade to `chart-test-r2`. Show that the new Sync API pod logs the version mismatch and exits
    non-zero, that the previous pod keeps serving `/version` meanwhile, and that the new pod
    becomes ready after the new Job completes.
  - Run the end-to-end test `H/tests/e2e/test_infrahub_sync.py` again in its vcluster, and check
    that nothing in it or its harness asserts zero restarts.
  - Record everything in `verification.md`.

### Chunk 9d: documentation

- [ ] T048 [US2] Update the design and operator documents for the record and the removed gate:
  - [research.md](research.md) R-2, [data-model.md](data-model.md) and
    [quickstart.md](quickstart.md) step 4
  - ADR `S/dev/adr/0043-the-kubernetes-deployment-orders-its-start-with-a-hashed-job-and-gates.md`
    (rename it if "gates" no longer fits, and update the index in `S/dev/adr/README.md`). State
    that the record covers the version and the work pool name, and not other bootstrap settings.
  - `S/docs/docs/kubernetes-deployment.mdx`: the start order, the restarts an operator sees during
    an install or an upgrade, and the minimum Sync version.
  - `S/docs/docs/compose-deployment.mdx`, if it describes the start order.
  - `S/docs/docs/operations/supported-platforms-and-limits.mdx:42`, which still says that the
    deployment runs on one host.

---

## Phase 8: Verification and finish

- [ ] T034 Run the checks in `H/`:
  - `make lint`
  - `uv run pytest -v tests/unit`
  - `helm-docs --chart-search-root=charts/infrahub-sync`, then `git status`: no change may remain
  - `yamllint` on the new workflow and the changed `ci.yml`, if the repository configures it

  Fix every finding.
- [ ] T035 Run the checks in `S/`:
  - `uv run invoke format`, `uv run invoke lint` and `uv run ruff check .`
  - `uv run invoke tests.tests-unit`, which includes any test that reads `docs/` or `dev/adr/`
  - `uv run invoke docs.docusaurus`; first run `cd docs && pnpm install --frozen-lockfile` if
    `node_modules` is missing

  Fix every finding.
- [ ] T036 Run [quickstart.md](quickstart.md) step 6 (uninstall), record it in
  `verification.md`, and delete the minikube profile.
- [ ] T037 Commit, with explicit paths.
  - In `H/`: one commit per coherent change, scoped `infrahub-sync` (chart, tests, CI, workflow).
  - In `S/`: the documentation, the decision record, the fragment and `verification.md`.
  - End each message with the attribution line in use for this session.
  - Do not push.

---

## Dependencies and execution order

- **Phase 1** has no dependency. T003 runs in the background from the start.
- **Phase 2** depends on T001. T004 comes before T005 and T006. T007 and T008 can run alongside
  T005.
- **US1 (Phase 3)** depends on Phase 2.
  - T010, T011, T012, T014 and T015 are separate files and can run in parallel.
  - T013 depends on T005.
  - T017 depends on T009 to T016.
  - T018 depends on T017 and T003.
- **US2 (Phase 4)** depends on US1. T021 depends on T018. T022 has no dependency.
- **US3 (Phase 5)** depends on Phase 3's templates. **US4 (Phase 6)** depends only on Phase 2 and
  T014.
- **Phase 7** can start after Phase 3. T030 needs the results of T018 and T021 for the upgrade
  section.
- **Phase 8** comes last.

## Parallel examples

- After Phase 2: T010 (`postgresql.yaml`), T011 (`object-store.yaml`), T012
  (`prefect-server.yaml`), T014 (`api.yaml`), T015 (`worker.yaml`) and T025 (route templates).
- During the local cluster runs: T027, T028, T029, T031, T032 and T033.

## Implementation strategy

1. **MVP**: Phases 1 to 3, a default install that answers on a local cluster.
2. Then US2, so that the chart can follow releases.
3. Then US3 and US4, then the documentation, then the final checks.

The chart is not published by this work (plan, "Release order").
