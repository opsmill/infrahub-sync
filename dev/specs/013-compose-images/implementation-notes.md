# Implementation notes: spec 013

## Baseline (T001, T002)

Starting commit: `bad4e0b3` on branch `013-compose-images`. No code or test changed for these
two tasks.

### T001: offline unit tier

- Environment: Python 3.11.8, full development profile
  (`uv sync --extra dev --extra prefect --extra service`).
- Command: `uv run invoke tests.tests-unit`
- Started: 2026-10-15T19:07:12Z
- Result: 5670 passed, 4 skipped, 203 deselected, 4 warnings in 138.49s. Exit code 0.

### T002: compose lifecycle tier

- Environment: Docker 29.4.0 on OrbStack (aarch64), Docker Compose 5.1.2.
- Image: `infrahub-sync:compose-test` (image ID `a5eb800a0a73`), built with
  `docker build -t infrahub-sync:compose-test .` at the starting commit.
- Command:
  `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose -n 0`
- Started: 2026-10-15T20:43:18Z
- Result: 37 passed, 0 failed, 0 skipped, 216 deselected in 519.42s (8 min 39 s). Exit code 0.
- Failing tests: none.
- Scope: one local run on arm64 only. It does not measure the failure rate that T004 measures
  with ten repeated runs on amd64 and arm64.

## T004 measurement run

This section records the dispatch only. The results and their classification against C1 to C5
are written later in `plan.md`, section "Lifecycle failure causes".

- Branch: `ci/compose-repeat-measure`, created from `origin/feature/v3-develop` at
  `8dc921b9bbf3d948b59bbf40c322d40255d738ab`. It holds one commit that changes only
  `.github/workflows/workflow-nightly-e2e.yml`, with the same content as the T003 commit
  `c422cede` on `013-compose-images`.
- Branch head: `9438b62f4b61bf0dd447e2be09c25fe06d6c85f2`.
- Commit under test: `8dc921b9bbf3d948b59bbf40c322d40255d738ab`, the head of
  `feature/v3-develop`. The workflow refuses a `sha` input that is not merged into
  `feature/v3-develop`, so the measurement branch head cannot be the `sha` input. The workflow
  definition comes from the measurement branch (`--ref`); the tests and the image come from the
  commit under test, which holds no reliability change.
- Dispatch command:
  `gh workflow run workflow-nightly-e2e.yml --ref ci/compose-repeat-measure -f sha=8dc921b9bbf3d948b59bbf40c322d40255d738ab -f compose_repeat=10`
- Run: 38806056523, <https://github.com/opsmill/infrahub-sync/actions/runs/38806056523>
- Dispatched: 2026-10-15T20:57:02Z. Run created: 2026-10-15T20:57:04Z.
- Legs: "Compose deployment suite against a locally built image (amd64)" on `ubuntu-22.04` and
  "Compose deployment suite against a locally built image (arm64)" on `ubuntu-24.04-arm`.
- The dispatch also runs the job "Integration, preview, saved plan, and local NetBox example".
  The workflow has no input to skip it, and its result is not part of this measurement.
- Status at hand-back (2026-10-15T20:58:00Z): run in progress; both legs had passed the commit
  check and the image build and were running the repeated suite step.

## User Story 1 (T014 to T024)

- T018: no release tag of version 3 or later ships a root `docker-compose.yml` (checked
  with `git tag` and `git ls-remote --tags origin` on 2026-10-15). Until one exists,
  `tests/compose/test_prefect_server_upgrade.py` reads the previous file from the parent of
  the newest commit that removed `prefecthq/prefect` from `docker-compose.yml`.
  `INFRAHUB_SYNC_PREVIOUS_COMPOSE_REF` overrides the reference. The test skips only when
  neither source exists; under `--compose-zero-skip` that skip fails the nightly job.
- T022: `tasks/release.py` counts no fixed number of Sync image lines.
  `uv run invoke release.validate-docker-compose --version 3.0.0a5` reports `sync_lines=5`.
- T024: with a local `docker build`, three image tests fail because the build passes no OCI
  version and revision labels: `test_the_image_carries_every_required_oci_label[...version]`,
  `[...revision]` and `test_the_image_carries_the_source_provenance_of_the_commit_it_was_built_from`.
  The publishing workflow passes these labels. No file of this phase changes them.

## User Story 3 (T034, T035, T040 to T042)

- T040 adds `INFRAHUB_SYNC_S3_ENDPOINT_URL`, `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` and
  `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE` to `docker-compose.yml`. So that
  `tests/compose/test_documentation.py::test_the_page_documents_every_variable_the_compose_file_reads`
  keeps passing, the same commit adds one short row for each of them to the optional settings
  table of `docs/docs/compose-deployment.mdx`. T043 extends these three rows; it does not add
  new ones.
- `docker compose config` renders `scale` as a number, `1` when
  `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE` is unset and `0` when it is `0`. Both the installed
  Compose and the pinned 2.24.0 release do so, and both render `required: false` on
  `sync-bootstrap`'s dependency on `object-store`.

## User Story 3 (T036, T037, T043 to T046)

- T036: no release tag of version 3 or later ships a root `docker-compose.yml`, so
  `tests/compose/test_object_store_upgrade.py` reads the previous file with
  `previous_compose_ref()` of `tests/compose/test_prefect_server_upgrade.py`, which honours
  `INFRAHUB_SYNC_PREVIOUS_COMPOSE_REF`. The test plans one run, moves the project to the working
  tree's file, and reads the run with `cli runs show` and its plan with `cli runs plan --detail`.
- T037: the external MinIO joins the deployment through a test-only override written in
  `tmp_path`, which adds a network to `sync-bootstrap`, `sync-api` and `sync-worker`. The store
  has network aliases for `<host>` and `<bucket>.<host>`, and its data is a `tmpfs`, so the
  finalizer removes only the container and the network. The endpoint host, the bucket and the
  credential are registered with `SECRETS`, so no retained output renders them.
- T046: `docs.docusaurus` built with no broken link or anchor warning.

## User Story 5 (T047 to T055)

- T047: `tests/compose/test_compose_contract.py` builds the 17 names from
  `BUILTIN_ADAPTER_CAPABILITIES` with `<prefix><ADAPTER>_<SETTING>` and the one exception
  `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`, and compares them with a literal copy of the
  contract's list. The receiver test plants a value per credential and searches each service's
  whole resolved definition with the `cli` profile named, because some credentials arrive inside
  another value, such as the product password inside `INFRAHUB_SYNC_DATABASE_URL`.
- T048: the case runs `docker compose run --rm --no-deps sync-worker` on the root file with the
  17 values given only through the `.env` file, rather than starting the whole deployment. Inside
  the container it parses one package per bundled adapter and resolves it with
  `resolve_runtime_instance`, the resolver runs use, and prints only identifiers and a verdict.
  It fails against the root file of the previous commit.
- T049: the HTTPS source is a small `http.server` started from the Sync image with a
  certificate from a test CA, on its own network, which only `sync-worker` joins through a
  test-only override. The package uses the `genericrestapi` adapter, reading `/api/v0/devices`
  into the fixture Infrahub's smoke branch. One deployment runs the cases in order: no setting,
  where the services start and the plan run fails; the setting, where the same package reaches
  `planned` and each of the three services logs `custom CA bundle installed` with one
  certificate; a directory that also holds a private key, where `docker compose run` of each
  service exits non-zero, names the file, and prints no line of the key. Spec 009 is not merged,
  so the failed run's type is not checked.
- T052 to T054: `docs.docusaurus` built with no broken link or anchor warning.
