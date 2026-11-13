# Implementation report: spec 021, an external object store, every adapter's credentials and a private CA

- **Status**: INCOMPLETE. 57 of 61 tasks are done. T033 is partly done and T040 to T042 need CI
  runs on a pushed branch.
- **Spec directory**: `dev/specs/021-compose-store-credentials-ca/`
- **Branch**: `021-compose-store-credentials-ca`, on top of `010-prefect-pr2-preview-removal`
  (spec 010 "PR 2", implemented in the same session as the prerequisite of decision 1). Neither
  branch is pushed.
- **Base commit**: `d442d27b` (the clarification commit, after rebasing onto the PR 2 branch)
- **Head commit**: `c174e98c` before this report
- **How it ran**: in one conversation, one chunk at a time. The skill's clean-context sub-agents
  could not start, because the session's auto mode refuses the Agent call after a plugin hook
  rewrites it. The review phase was done in the same conversation for the same reason.

## Chunk ledger

| Chunk | Tasks | Done / partly / blocked | Commits | Notes |
| --- | --- | --- | --- | --- |
| Phase 1, setup | T001, T002 | 2 / 0 / 0 | `da98ece2` | Compose 2.24.0 honors an interpolated `scale: 0` and `required: false`; the minimum version does not change. PR 2 was not merged, so it was implemented first on its own branch. |
| Phase 2, foundational | T003, T004 | 2 / 0 / 0 | `bd6b9c13` | `build_s3_client` and `s3_client_arguments` in `storage.py`. |
| US1, external store | T005 to T018 | 14 / 0 / 0 | `5b0a80b2` | Found and fixed: `sync-bootstrap` printed none of its INFO lines in a container, already the case on `feature/v3-develop`. Found a PR 2 regression in the compose tier's Infrahub fixture, fixed on the PR 2 branch (`a96076a3`). |
| US2, adapter credentials | T019 to T025 | 7 / 0 / 0 | `a86e2fd3` | Five adapter pages said the worker receives only three tokens; corrected. |
| US3, private CA | T026 to T035 | 9 / 1 / 0 | `e760e14e`, `c174e98c` | T033 is partly done: the object store over TLS from a test CA is checked live; Infrahub and a source over TLS, and each adapter client, are not. |
| US5, released image | T044 to T049 | 6 / 0 / 0 | `bcdd7016` | The `record_image` step is tested against a stub `gh`, not on a release. |
| Phase 8, image pins | T050 to T053 | 4 / 0 / 0 | `77f40b38` | T053 does not apply: the preview file is gone on this branch. |
| US4, lifecycle tier | T036 to T043 | 5 / 0 / 3 | `ce26d1d6` | Leak checks pass live. A docs page told operators to run `docker compose config prefect-server`, which prints credentials; changed to `--no-interpolate`. T040 to T042 need the workflow dispatched. |
| Phase 9, records | T054 to T061 | 8 / 0 / 0 | `ea150601` | ADR 0026, news fragment, quickstart results. |
| Review fixes | — | — | `c174e98c` | See "Review findings". |

## Tasks not completed

- **T033** (partly): the lifecycle test covers the external store over TLS from a test CA. Not
  done: Infrahub and a NetBox stand-in served over the test CA, and a check that every bundled
  adapter's HTTP client honors the bundle.
- **T040**: dispatch the nightly workflow with 10 repeats on each architecture before any
  reliability change. Needs the branch pushed. Whether the workflow can be dispatched from a
  branch other than `feature/v3-develop` is not known.
- **T041**: fix each cause found by T040. Depends on T040.
- **T042**: dispatch again with 10 repeats on each architecture. Depends on T041.

## Local-pass evidence

All runs on 2026-11-13, on a macOS arm64 host with Docker 29.8.2 and Compose 5.5.0. Live tests
used `infrahub-sync:compose-test` built from this branch (`docker build -t infrahub-sync:compose-test .`).
Exact per-test times were not recorded; the date is the run date.

| Test id | Type | Run command | Passed at | Environment context | Pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/service/test_storage.py` (5 new cases, 1 updated) | unit | `uv run pytest tests/service/test_storage.py` | 2026-11-13 | n/a | `62 passed` |
| `tests/service/test_compose_bootstrap.py` (15 new cases) | unit | `uv run pytest tests/service/test_compose_bootstrap.py` | 2026-11-13 | n/a | `33 passed` |
| `tests/service/test_trust_store.py` (9 cases, new file) | unit | `uv run pytest tests/service/test_trust_store.py` | 2026-11-13 | n/a | `9 passed` |
| `tests/compose/test_compose_contract.py` (store settings, bundled store, credential list, receivers, CA mount, narrowed mount tests, development digests) | unit (Compose CLI, no daemon) | `uv run pytest tests/compose/test_compose_contract.py -m "not compose"` | 2026-11-13 | Docker Compose 5.5.0 CLI | part of `1871 passed` (service, compose, dev stack, testcontainers) |
| `tests/compose/test_documentation.py::test_no_page_runs_a_command_that_prints_credentials` and the new variable cases | unit | `uv run pytest tests/compose/test_documentation.py` | 2026-11-13 | n/a | `138 passed` |
| `tests/test_workflow_contracts.py` (record_image, trigger permission, cosign identity, compose matrix) | unit | `uv run pytest tests/test_workflow_contracts.py` | 2026-11-13 | bash, stub `gh` | `218 passed` |
| `tests/compose/test_external_store.py` (7 cases: path, virtual, proxy) | e2e | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose/test_external_store.py` | 2026-11-13 | Silo container on its own network; Infrahub 1.11.9 fixture | `7 passed in 171.88s` |
| `tests/compose/test_external_store.py::test_a_store_behind_a_private_ca_is_trusted_with_checks_on` | e2e | same, with `::test_a_store_behind_a_private_ca_is_trusted_with_checks_on` | 2026-11-13 | Silo over TLS from a generated test CA | `1 passed in 122.06s` |
| `tests/compose/test_credential_leaks.py` (6 cases) | e2e | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose/test_credential_leaks.py` | 2026-11-13 | one deployment, every credential a 24+ character value | `6 passed in 34.81s` |
| `tests/compose/test_source_credentials.py::test_the_worker_container_resolves_the_shipped_aci_package_credentials` | e2e (`docker`) | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m docker <id>` | 2026-11-13 | `compose run sync-worker` | `1 passed in 1.18s` |
| `tests/image/test_prefect_server_image.py::test_the_prefect_server_answers_its_health_check_under_a_read_only_root` | e2e (`docker`) | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m docker tests/image/test_prefect_server_image.py` | 2026-11-13 | read-only container, tmpfs scratch | `1 passed in 4.75s` |
| `tests/compose/test_fixture_prerequisites.py` (updated, PR 2 branch) | unit | `uv run pytest tests/compose/test_fixture_prerequisites.py -m "not compose"` | 2026-11-13 | n/a | `3 passed` |
| Full unit tier | unit | `uv run invoke tests.tests-unit` | 2026-11-13 | Python 3.13 | `7324 passed`, 4 failed (see below) |
| Nightly 10-run lifecycle measurement (T040, T042) | e2e | `gh workflow run workflow-nightly-e2e.yml -f sha=<sha> -f compose_repeat=10` | deferred — needs CI on a pushed branch | GitHub Actions, linux/amd64 and linux/arm64 | — |

The 4 unit-tier failures are SQLite migration tests in `tests/product_store/test_contract.py`.
They fail the same way on unchanged `feature/v3-develop` on this host: Python 3.13's SQLite
3.51.0 refuses changes to `sqlite_master`. This branch does not touch them.

## Review findings

| Severity | File | Finding | Action |
| --- | --- | --- | --- |
| High | `infrahub_sync/service/trust_store.py` | An unreadable CA directory or file raised a raw `OSError` traceback instead of an error that names it (spec edge case) | Fixed in `c174e98c`, with a test |
| Medium | `docs/docs/compose-deployment.mdx` | `docker compose config prefect-server` printed the Prefect database password and API credential | Fixed in `ce26d1d6`; a docs test now refuses such commands |
| Medium | `infrahub_sync/service/bootstrap.py` | INFO lines never reached the container log, so the job reported only its refusals (already the case on `feature/v3-develop`) | Fixed in `5b0a80b2`, with a test |
| Low | `tests/compose/test_external_store.py` | Bucket creation in the test store could retry until timeout after a partial first attempt | Fixed in `c174e98c` |
| Low | `tests/test_workflow_contracts.py` | `test_only_the_newest_stable_release_is_called_newest` failed several times while Docker builds ran, and passed on every rerun, also on unchanged code | Not changed; recorded in `research.md` |

Five review lenses (code, comments, tests, errors, types) were applied in one pass, not as
separate agents. The simplify pass was not run.

## Autonomous decisions

- Spec 010 "PR 2" was implemented as its own branch before spec 021, as you chose. Its
  `test_managed_write_guard_live.py` failed 2 of 4 legs on the development stack (worker
  `ServiceStorageStartupError` while the test holds advisory locks); no earlier run on the preview
  stack is recorded. Not investigated further.
- The `record_image` job runs in `workflow-publish.yml`, and `trigger-release.yml` grants
  `contents: write` to the called workflow; only `record_image` declares it.
- `trust_store` and `bootstrap` use the standard `logging` module, as the existing service code
  does, not `structlog`.
- The nightly compose matrix defaults to both architectures (T043), so a nightly run now starts
  an arm64 job. Whether `ubuntu-24.04-arm` can run the tier is not known.

## Suggested next steps

1. Push `010-prefect-pr2-preview-removal` and this branch (on your request), then dispatch the
   nightly workflow with `compose_repeat=10` for T040.
2. Investigate the live write guard test failure on the development stack before merging PR 2.
3. Finish T033: Infrahub and a source over TLS from the test CA.
4. Open the PR for PR 2 first, then this one.
