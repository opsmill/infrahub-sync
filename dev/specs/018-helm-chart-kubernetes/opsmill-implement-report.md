# Implementation report: spec 018, a Helm chart to run Infrahub Sync 3.x on Kubernetes

## 1. Header

- **Feature**: the `infrahub-sync` Helm chart (issue
  [#422](https://github.com/opsmill/infrahub-sync/issues/422)), and the bootstrap record with its
  start check in Infrahub Sync.
- **Spec directory**:
  `/Users/bkohler/automation/opsmill/infrahub-sync/.worktrees/018-helm-chart-kubernetes/dev/specs/018-helm-chart-kubernetes`
- **Repositories, base and head commits**:
  - Infrahub Sync, branch `018-helm-chart-kubernetes`: base `b3fb75e2`, head `a791c0df`, 34 commits.
  - `infrahub-helm`, branch `feat/infrahub-sync-chart`: base `25d87a8`, head `b17405d`, 20 commits.
- **Status**: DONE. All 48 tasks are ticked, and no test is missing local-pass evidence.
- **Wall-clock time**: from 2026-11-16 12:07 (base commit, UTC+1) to 2026-11-16 17:00 UTC, across
  two sessions with a context compaction between them. The exact time is not known.
- Nothing was pushed, and no pull request was opened.

## 2. Ledger by chunk

| Chunk | Tasks | Outcome | Commits |
| --- | --- | --- | --- |
| 1, setup | T001, T002 (T003 by the orchestrator) | 3 ✅ | H `46d494b`; S `5deb48f`, `d28e06df` |
| 2, foundational | T004 to T008 | 5 ✅ | H `6436b5d`; S `0e60afec` |
| 3, US1 install | T009 to T017 | 9 ✅ | H `3a0e458`, `1b10f55`, `1f6babb`, `e06ee6d`; S `7c6fd3b9` |
| 4, US1 cluster install | T018 | 1 ✅ | S `904d76d0`, `c04670e0` |
| 5, US2 upgrade | T019 to T022 | 4 ✅ | H `202d232`, `e96806b`, `5ec7e69`; S `377303c8`, `602a3624` |
| 6, US3 external stores | T023, T024 | 2 ✅ | H `8702d11`, `6f88cf6`; S `5b5b05d9`, `1f05a823` |
| 7, US4 routes | T025, T026 | 2 ✅ | H `66a9920`, `9d6cc40`; S `4e46c336` |
| 8a, chart docs and CI | T027 to T029 | 3 ✅ | H `6139876`, `27b941d`; S `2091b824` |
| 8b, Sync docs | T030 to T033 | 4 ✅ | S `2cc3c4bd`, `6fd65629`, `b98d2e57`, `c2bf3283`, `1e3cb9c5`, `aac9d496` |
| 9a, bootstrap record and start check | T038 to T042 | 5 ✅ | S `adfbeef9`, `03841afa`, `dfe60856` |
| 9b, remove the bootstrap wait from the chart | T043 to T045 | 3 ✅ | H `e2e19ed`, `61c968c`; S `74149fb7` |
| 9c, cluster verification of the start check | T046, T047 | 2 ✅ | S `968de2f0`, `03fae0a7` |
| 9d, documentation | T048 | 1 ✅ | S `da217f8d`, `6217ac13`, `6ee923a2`, `f4eb0d60` |
| Review fixes | 10 findings | 10 ✅ | H `b41fa3a`, `3f8b53b`, `b17405d`; S `61636798`, `0f959fef`, `ac0a4267` |
| 8, final checks | T034 to T037 | 4 ✅ | S `a791c0df` |

### What each chunk reported upward

**Chunk 9a**
- A repeated bootstrap for the same version and work pool writes nothing, to satisfy FR-010.
- The worker already received the product database URL in all three Compose files.
- No version difference is expected in the development stack's deps mode. If the version changes
  after `dev.deps` has run, `dev.api` and `dev.worker` refuse to start until `dev.deps` runs again.

**Chunk 9b**
- The CI job reads two repository secrets that the subagent named:
  `INFRAHUB_SYNC_REGISTRY_USERNAME` and `INFRAHUB_SYNC_REGISTRY_PASSWORD`. Whether they exist is not
  known.
- The warning is written when either secret is empty, not when pytest skips the test.

**Chunk 9c**
- On a fresh install, the Sync API restarted 3 times and was ready 23 s after the Job completed.
  The restarts include "product database cannot be reached" while PostgreSQL starts, because the
  Sync API and the worker have no init container.
- On an upgrade, the new Sync API pod refused to start with a line that names both versions and no
  credential. `/version` answered throughout and changed from the old version to the new one.
- The old worker did not stop on SIGTERM and was killed after its 30 s grace period. This was not
  investigated.

**Chunk 9d**
- ADR 0043 was renamed to
  `0043-the-kubernetes-deployment-orders-its-start-with-a-hashed-job-and-a-bootstrap-record.md`.
- Branch `017-serve-web-interface` also adds an ADR numbered 0043. Neither was renumbered.

## 3. Tasks not completed

None. All 48 tasks (T001 to T048) are `[X]`.

## 4. Local-pass evidence

Each row is the most recent passing run of a suite that holds the tests added or changed by this
work. Earlier passing runs of each chunk are recorded in [verification.md](verification.md).

| Test ID | Type | Run command | Passed at | Environment context | Verbatim pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/e2e/test_infrahub_sync.py::test_sync_installs_and_upgrades` (H) | e2e | `INFRAHUB_SYNC_E2E_IMAGE=infrahub-sync:chart-test-r1 uv run pytest -v -rs -s -m sync tests/e2e` | 2026-11-16T16:46:38Z | vcluster 0.36.1, docker driver, Kubernetes v1.36.0 | `1 passed, 28 deselected, 2 warnings in 128.01s (0:02:08)` |
| The same test with an image that has no start check (negative run) | e2e | the same command with `infrahub-sync:chart-test` | 2026-11-16T16:48:55Z | the same | `1 failed, 28 deselected, 1 warning in 107.20s`: the test fails as intended |
| `tests/unit/test_infrahub_sync_chart.py` (H), every render test, RR-01 to RR-45 and the review additions | unit | `uv run pytest -v tests/unit` | 2026-11-16T16:53:27Z | n/a | `139 passed in 5.47s` |
| `tests/product_store/test_bootstrap_record.py`, `tests/service/test_start_check.py`, `tests/service/test_compose_bootstrap.py` (S) | unit and integration | `PRODUCT_STORE_TEST_POSTGRESQL_DSN=… uv run pytest -m "integration or not integration" -p no:randomly -v <those files>` | 2026-11-16T15:19:08Z | temporary `postgres:16-alpine` on 127.0.0.1:55438 | `95 passed in 6.87s` |
| Existing S tests adapted to the injectable start check (`test_config_routes.py`, `test_environment_names.py`, `test_installed_flow_identity.py`, `test_trust_store_entry_points.py`, `test_validation_secret_containment.py`, `tests/product_store/test_contract.py`) | unit | `uv run pytest -m "integration or not integration" -p no:randomly -v <those files>` | 2026-11-16T15:14:45Z | the same temporary PostgreSQL | `208 passed, 1 warning in 12.09s` |
| `tests/compose/test_configuration_free_startup.py::test_a_deployment_with_no_configuration_reaches_ready` (S) | integration (Compose) | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=t041-record uv run pytest -m compose <test ID> -v -p no:randomly` | 2026-11-16T15:13:38Z | Docker Desktop, image built from `adfbeef9` | `1 passed in 44.35s` |
| Documentation tests (S, seven files, including `tests/test_no_pinned_candidate_version.py`) | unit | `uv run pytest -q <the seven files>` | 2026-11-16T16:52:51Z | n/a | `303 passed in 21.34s` |
| Full unit suite of S at HEAD | unit | `uv run invoke tests.tests-unit` | 2026-11-16T17:00:23Z | n/a | `8556 passed, 4 skipped, 377 deselected, 2 xfailed, 15 warnings in 345.33s` |
| `e2e-infrahub-sync` warning step in `H/.github/workflows/ci.yml` | CI | runs in GitHub Actions on a pull request | deferred: the step cannot run locally | its script was run locally, and `actionlint` exits 0 | the script printed the `::warning` line and the summary line |

The Compose run predates `dfe60856`, which makes a repeated bootstrap write nothing. The full unit
suite covers that commit; Compose was not run again for it.

## 5. Review findings

Four reviewers covered the Sync code, the chart and CI, the tests, and the documentation. No finding
was Critical.

| Severity | File | Finding | Outcome |
| --- | --- | --- | --- |
| High | H `tests/e2e/test_infrahub_sync.py` | The upgrade changed only the S3 prefix, so the start check's refusal never ran in the cluster, and a start-order regression would pass | Fixed: the upgrade changes the work pool and holds the Job; the negative run fails as intended |
| Medium | H `templates/_helpers.tpl:104,112` | A numeric image tag rendered `%!s(float64=17)` | Fixed: `toString`, and a tag that is not a string is refused |
| Medium | H `tests/unit/test_infrahub_sync_chart.py` (RR-34) | Only `s3.prefix` was tested as a bootstrap setting | Fixed: `s3.bucket` and `settings.workPool` added |
| Medium | H `NOTES.txt`, `README.md.gotmpl` | Said the pods restart until the Job completes, which the 3.0.0a5 image does not do | Fixed |
| Medium | S ADR 0043 | The count of mechanisms did not match the list | Fixed |
| Low | H `README.md.gotmpl` | The publication condition left out the start check | Fixed |
| Low | H `NOTES.txt`, S `kubernetes-deployment.mdx:238` | NOTES put the token in shell history and used another timeout | Fixed |
| Low | S `kubernetes-deployment.mdx:27-28` | The init script ConfigMap and the record write were missing from the table | Fixed |
| Low | H `templates/ingress.yaml` | An empty `ingress.hostname` rendered an invalid Ingress | Fixed: refused at render time |
| Low | H `tests/e2e/conftest.py` | A failed `docker login` read as a missing image | Fixed |
| Medium | H render tests | FR-008 is tested only through the manifest shape; no cluster test deletes a pod and counts Jobs | Deferred |
| Low | S `product_store/store.py:532-536` | A slower bootstrap of the previous version can overwrite the record, because the last write wins | Deferred: the old Job is deleted when its name changes |
| Low | S `service/serve.py:88` | The Sync API falls back to the work pool name `default` when the variable is not set, so the refusal names a pool mismatch instead of the missing setting | Deferred: every deployment sets the variable |
| Low | S `product_store/store.py:4561-4578` | `write_bootstrap_record` returns the requested record, not the stored one, on a repeat | Deferred: no caller reads the time |
| Low | H render tests (RR-40, RR-43, RR-19) | Three assertions are weaker than the contract | Deferred |
| Low | H `tests/e2e/test_infrahub_sync.py` | The test holds the bootstrap Job with a CPU request of 1000 cores, so that it stays Pending | Recorded: works on any cluster, but is unusual |

## 6. Autonomous decisions

**From the clarification of 2026-11-16 to Phase 9**
- The orchestrator added Phase 9 (T038 to T048) to `tasks.md` and adjusted `spec.md` and `plan.md`
  before dispatch. This skill normally does not change those documents.
- The record holds the work pool name as well as the version, because the removed wait had checked
  the work pool.

**The CI job**
- Its secret names were chosen without a source: `INFRAHUB_SYNC_REGISTRY_USERNAME` and
  `INFRAHUB_SYNC_REGISTRY_PASSWORD`. A maintainer must confirm them or rename them.
- Its warning step was not run in GitHub Actions; it is recorded as deferred in section 4.

**The chart**
- It now refuses an unquoted numeric image tag, so `--set image.tag=17` needs `--set-string`.

**Cluster and test data**
- The minikube release `sync`, its claims and the profile `sync-chart` were deleted. They held only
  test data.

**Review triage**
- Five Low findings and one Medium finding were deferred, as listed in section 5.

**Memory**
- The startup reads of the shared Hivemind memory overflowed and were not consumed, and its space
  rules were not read. The clarification decisions were written to the `opsmill` space.

## 7. Suggested next steps

1. Confirm or rename the two registry secrets of the `e2e-infrahub-sync` job, and add them to
   `infrahub-helm` if the end-to-end test should run in CI before release 3.0.0.
2. Resolve the ADR number 0043, which branch `017-serve-web-interface` also uses.
3. Investigate why the worker does not stop on SIGTERM during an upgrade.
4. Decide on the deferred findings in section 5.
5. Release an Infrahub Sync version that contains the start check. Then set the chart's
   `appVersion` to it and publish the chart, as described in the plan's "Release order".
6. Open the pull requests when ready: one in `infrahub-sync` into `feature/v3-develop` and one in
   `infrahub-helm` into `stable`.
