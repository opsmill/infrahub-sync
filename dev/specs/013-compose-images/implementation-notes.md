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

## Gates and tiers (T081 to T083)

Run on 2026-10-16 at commit `c808c052`, Docker Engine 29.4.0 (OrbStack, arm64), Docker Compose
v5.1.2, image `infrahub-sync:compose-test` (ID `79927274354b`) built from that commit.

- T081: `docs.docusaurus` built with no broken link or anchor warning. `docs.generate` rewrites
  `docs/docs/reference/cli.mdx` without the paragraph about `INFRAHUB_SYNC_API_TOKEN` that commit
  `b5b50af1` added by hand, because the paragraph is not in the CLI help text. This
  specification did not change that page, so the generated change was not kept.
- T082: rumdl, ruff, pylint (0 diagnostics) and ty pass on the files this specification
  changed. `invoke lint` stops at yamllint only because of untracked files under `.worktrees/`:
  all 391 paths it reports start with `./.worktrees/`, and yamllint on every tracked YAML file
  passes. ty reports 17 diagnostics, none in a file this specification changed.
- T083: the unit tier passes. The image tier fails only the two OCI label cases (`version`,
  `revision`) and the provenance case, which need the labels the publish workflow sets. In the
  compose tier, `test_managed_run.py::test_the_deployed_worker_container_mounts_nothing` failed:
  since T012 the worker mounts the read-only CA directory, so its mount list is not empty. The
  test is now `test_the_deployed_worker_container_mounts_only_the_read_only_ca_directory`. It
  checks that the only mount is `/etc/infrahub-sync/ca`, read-only, and that the worker and the
  API share no writable mount, as `test_compose_contract.py` already does for the resolved file.
  The compose tier then passes: 48 tests in 12 files; 6 files hold only offline tests, which
  `-m compose` deselects.

## Quickstart walk-through (T084)

The CLI sanity checks (`--help`, `configs --help`, `runs --help`) exit 0. The scenarios ran
against the root `docker-compose.yml` copied to a scratch directory, project `qs013`, with the
image above and a `.env` written by the generator in `docs/docs/compose-deployment.mdx`. Port
8000 was in use on the host, so `INFRAHUB_SYNC_API_PORT=18000` and
`INFRAHUB_SYNC_PREFECT_PORT=14200` were set. Every stack started here was removed afterwards.

| Scenario | Run | Result |
| --- | --- | --- |
| 1. Offline contract checks | Yes | Differs: the command collects the one `compose` case in `test_compose_minimum.py`, which fails when `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION` are not exported in the shell. The prerequisites put them in `.env`, which pytest does not read. With them exported, 319 pass; with `-m "not compose"`, 309 pass. |
| 2. Three images, one published port | Yes | Matches, with one note: `ps` shows `postgres` with `{ 5432 0 tcp}`, an exposed port with no host port. Only `sync-api` has a host port. |
| 3. One Prefect version | Yes | Matches: both commands print `3.8.6`. |
| 4. Each credential reaches only its receivers | Yes | Matches the receiver table in `contracts/environment-variables.md`. |
| 5. Plan and apply | Through the compose tier only | The manual NetBox sequence needs a NetBox with the `demo` dataset, which this machine does not run. `test_lifecycle.py` and `test_managed_run.py` pass. |
| 6. Prefect opt-in | Yes | Matches: health is 200 with `prefect-ui.yml` and the connection is refused without it. The browser step was replaced by API calls with the credential: work pool `infrahub-sync` and deployment `run` are listed, and the same call without the credential returns 401. |
| 7. Update keeps the bundled store's data | Through the compose tier only | `test_object_store_upgrade.py` passes. No deployment from the previous release ran on this machine. |
| 8. The team's own S3-compatible store | Negative check, and the compose tier | No external HTTPS store was available. `test_external_object_store.py` passes. Setting an endpoint without `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0` stops `sync-bootstrap` with `bundled-object-store-not-left-out`, as described. The `object-store-unsupported` check was not run. |
| 9. Credentials and custom CA | Yes | Matches: `True`, and one `custom CA bundle installed` line with `certificates=1` and the bundle path, with no certificate content. Differs in one case: a private key added to the directory of a running deployment stops only `sync-bootstrap` on `up -d --wait`, because Compose does not recreate `sync-api` and `sync-worker` when only the directory's content changes. After `docker compose restart`, both stop and name the file. The plan against an HTTPS source is covered by `test_custom_ca.py`, which passes. |
| 10. No credential in output | Yes | Matches: 1 passed. As in scenario 1, the command needs `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION` exported. |
| 11. Development and preview stacks | Yes | Matches: 4230 and 4210 return `true`, and `PREVIEW_PREFECT_IMAGE` occurs 0 times. Port 8080 was in use on the host, so `development/preview.local.env` set `PREVIEW_INFRAHUB_PORT=8085` for the run and was removed afterwards. |

One more observation: after the CA directory replaced the placeholder volume in `.env`,
`docker compose down -v` left the volume `qs013_sync-ca-certificates`, created by the first
start, because the resolved file no longer referenced it. It was removed by hand.
