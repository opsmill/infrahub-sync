# Implementation report: Publish the Sync image to the OpsMill registry

## 1. Header

| | |
|---|---|
| Feature | 007-harbor-image-publishing |
| Spec dir | `dev/specs/007-harbor-image-publishing` (also `specs/007-harbor-image-publishing`) |
| Branch | `007-harbor-image-publishing` (from `feature/v3-develop` @ `8a0d1875`) |
| Base commit | `53e50ec7` (baseline: spec-kit restore + spec docs) |
| Head commit | `95df9f09` (before this report's commit) |
| Wall clock | ≈ 2h20m (2026-10-01 12:55 → 15:15 +02:00) |
| Diff | 142 files, +4985 / −27688 |
| Result | **DONE**: 55/55 tasks complete, local-pass evidence for every new or modified test |

## 2. Chunk ledger

| # | Chunk (tasks.md phase) | Tasks | ✅ / ⚠️ / ❌ | Commits | Flagged upward |
|---|---|---|---|---|---|
| 1 | Phase 1 Setup (T001–T002) | 2 | 2/0/0 | `5070ffde` | Baseline measured at 3195 lines |
| 2 | Phase 2 Foundational (T003–T008) | 6 | 6/0/0 | `a12a1dbe` | SHA pins reused from infrahub. The smoke test requires the revision label to equal HEAD. `test_image_containment.py` deletion pulled forward from T025 |
| 3 | Phase 3 US1 releases (T009–T014) | 6 | 6/0/0 | `71fde760` | The explicit `--latest` could let a backport steal `latest` (fixed in review). Legacy install anchor kept until chunk 9. Two doc tests pulled forward from T025/T026 |
| 4 | Phase 4 US2 dispatch (T015–T017) | 3 | 3/0/0 | `20c9a26a` | The Actions form can't carry multi-line labels, so the guide uses `gh workflow run --json`. Cancel-in-progress risk (fixed in review) |
| 5 | Phase 5 US3 PR gate (T018–T021) | 4 | 4/0/0 | `4dea6c05` | GitHub checks every called job's permissions, so the PR caller grants `id-token: write`. `packages: write` dropped from the reusable workflow |
| 6 | Phase 6 US5 removal (T022–T032) | 11 | 11/0/0 | `d1c19854`, `08e39840` | Two planned chunks merged so no commit has a broken import. `write_candidate_binding` kept until T041 |
| 7 | Phase 7 US4 part 1 (T033–T039) | 7 | 7/0/0 | `1462f2a3` | `$$` escaping verified inside the live container. Bearer token must be ≥ 16 characters. `sync-cli` renamed to `cli`. Live `up --wait` healthy |
| 8 | Phase 7 US4 part 2 (T040–T044) | 5 | 5/0/0 | `637e928b`, `9209941b`, `744fa85d` | Redaction regex fix. Shared test port moved from 8021 to 8061, because of a host reset on 8021. Operator guidance handoff note written |
| 9 | Phase 7 US4 docs (T045–T048) | 4 | 3/1/0 | `056c1379` | T047 ⚠️: `netbox-demo-to-infrahub.mdx` needed no change. Anchors renamed. Docs build ran clean |
| 10 | Phase 8 Polish (T049–T055) | 7 | 7/0/0 | `92b70b1e` | SC-006 grep refined with 5 intentional exclusions. `docs.generate` drift on `reference/cli.mdx` was reverted (it predates this branch) |
| R | Review fixes (Phase 6) | 12 findings + wording | 12/0/0 | `d77a5aaf`, `41396564`, `95df9f09` | See §5 |

Orchestrator fixup outside the chunks: I deleted a stray ignored `scripts/__pycache__/release_body.cpython-311.pyc` left over from another branch. It had been failing `tests/cli/test_parity_and_closure.py` for every chunk. No commit, because the file was gitignored.

## 3. Tasks not completed

None. All 55 tasks are ticked. T047 was ticked with a ⚠️ note: one of its three pages needed no change.

## 4. Local-pass evidence

Environment for every row: macOS arm64 (Darwin 25.6.0), Python 3.11.8, uv venv with the `dev`, `prefect` and `service` extras. Docker 29.4.0 (OrbStack) and Compose 5.1.2 where Docker is used.

| Test id | Type | Run command | Passed at (UTC) | Environment context | Verbatim pass line |
|---|---|---|---|---|---|
| `tests/test_workflow_contracts.py` (all sections, final) | unit (contract) | `uv run pytest -q tests/test_workflow_contracts.py tests/test_release_pin.py tests/image -m "not docker"` | 2026-10-01T13:12:34Z | n/a | `227 passed, 29 deselected in 11.43s` |
| `tests/test_release_pin.py` (incl. repo-pin and unpinned-line cases) | unit | same as above | 2026-10-01T13:12:34Z | n/a | (included above) |
| `tests/image/test_version_policy.py` (restored Dockerfile uv-pin and `--frozen` checks) | unit | same as above | 2026-10-01T13:12:34Z | n/a | (included above) |
| `tests/image/test_image_ref_fixture.py` | unit | same as above | 2026-10-01T13:12:34Z | n/a | (included above) |
| `tests/image/test_image_artifact.py` (28 items, incl. OCI labels, no secrets in logs) | integration (docker) | `INFRAHUB_SYNC_IMAGE_REF=infrahub-sync:smoke uv run pytest -m docker tests/image/test_image_artifact.py` | 2026-10-01T13:14:30Z | image `infrahub-sync:smoke` built at HEAD with source, version and revision labels | `28 passed in 19.22s` |
| `tests/compose/**` (retargeted lifecycle suite, `test_compose_contract.py`, minimum, CLI, redaction, source credentials, configuration-free startup) | e2e (compose) | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose -x` | 2026-10-01T12:29:24Z | image `infrahub-sync:compose-test`; Docker 29.4.0 / Compose 5.1.2 | `37 passed, 142 deselected in 517.18s` |
| `tests/compose` docker-but-not-compose cases | integration | `… uv run pytest -m "docker and not compose" tests/compose` | ≈2026-10-01T12:31Z | same image | `5 passed, 174 deselected in 4.00s` |
| `tests/compose/test_documentation.py` and the other doc-asserting tests | unit | `uv run pytest -q tests/compose/test_documentation.py tests/test_developer_pages_sidebar.py tests/test_development_stack_docs.py tests/cli/test_parity_and_closure.py tests/preview/test_preview_configuration.py` | 2026-10-01T12:45:51Z | n/a | `117 passed in 0.65s` |
| `tests/dev_stack/*` | unit | `uv run pytest -q tests/dev_stack` | 2026-10-01T13:12:46Z | n/a | `18 passed in 0.33s` |
| Full non-Docker suite | unit | `uv run pytest -q -m "not docker and not compose"` | 2026-10-01T13:12:46Z | n/a | `4987 passed, 117 skipped, 71 deselected, 4 warnings in 89.15s (0:01:29)` |
| GitHub-only workflows (release, dispatch, PR gate on real runners) | e2e | CI: quickstart §3, §4 and §5 | deferred: these run only on GitHub | needs the Harbor project and org secrets | n/a |

The Compose suite was not re-run after the review fixes. In `tests/compose` those fixes changed only docstrings, one skip message and the deletion of an unused helper. The workflow change that adds `--compose-zero-skip` to the nightly job is covered by its contract test.

## 5. Review findings

Five review passes ran: code, tests, errors, comments and simplify. Types was skipped because the only new class is the test helper `tests/compose/lifecycle.py:Deployment`.

| Severity | File | Summary | Status |
|---|---|---|---|
| High | `trigger-push-stable.yml`, `release-publish.yml` | The version regex rejected PEP 440 pre-releases (`3.0.0a6`, `rc1`) | Fixed `d77a5aaf` |
| High | `release-publish.yml` | Explicit `--latest` let a stable backport move GitHub latest and the image `latest` | Fixed `d77a5aaf` |
| High | `workflow-nightly-e2e.yml`, `tests/image/conftest.py` | All-skipped Compose or smoke runs reported green | Fixed `d77a5aaf`, `41396564` |
| High | `ci-docker-image.yml` merge job | No `pipefail`, no digest validation, empty-digest and zero-digest paths | Fixed `d77a5aaf` |
| High | `ci-docker-image.yml` login | An empty `HARBOR_HOST` would send the Harbor credentials to Docker Hub | Fixed `d77a5aaf` |
| Medium | `ci-docker-image.yml` | `cancel-in-progress` could leave unsigned tags | Fixed (`!inputs.publish`) |
| Medium | `workflow-publish.yml` | A failed `gh api` call was read as "no latest release" | Fixed (only HTTP 404 counts) |
| Medium | `trigger-release.yml`, `workflow-publish.yml` | Unused `packages: write` grants | Fixed |
| Medium | `tasks/release.py` | Sync image lines outside the pinned form were silently ignored | Fixed |
| Medium | tests | Missing checks: pin equals pyproject version, Dockerfile uv pin and `--frozen`, merge tag parsing; publish guard was a substring match | Fixed `41396564` |
| Medium | `tests/compose/conftest.py` | Dead `--compose-zero-skip` docstrings | Fixed |
| Medium | `docker-compose.yml` bootstrap | An existing role's password is never rotated; a failed `CREATE ROLE` may log the password in Postgres | **Deferred**: behaviour carried over from the removed bundle, needs a design call |
| Medium | `tests/compose/lifecycle.py` | `await_phase` discards the underlying assertion text and retries a permanent 4xx until the deadline | **Deferred** |
| Low | various comments and docs | Leftover "bundle" and "candidate" wording; inaccurate image-default sentence | Fixed `95df9f09` |
| Low | `tests/image/conftest.py` | Startup-failure messages omit the container's stderr; `TimeoutExpired` not caught | **Deferred** |
| Low | `tasks/dev.py`, `tasks/netbox.py` | Docstring wrap width | **Deferred** (cosmetic) |
| Advisory | simplify | About 50 lines are safe to cut, and about 270 more only if deliberate guards (cosign retry, backport-safe `latest`, path-filtered PR gate) are dropped | **Deferred**: maintainer call |
| Pre-existing | `docs/docs/reference/cli.mdx` | `invoke docs.generate` deletes a hand-written `INFRAHUB_SYNC_API_TOKEN` paragraph | **Deferred**: separate fix, predates this branch |

## 6. Autonomous decisions

- **Baseline commit**: on the user's choice, one commit `53e50ec7` restored spec-kit (the reverse of `4a725c84`) and added the spec docs. The untracked `.bug-analysis-release-body-from-notes.md` stays out of every commit.
- **US5 before US4**: the order changed because the new `tasks/release.py` reuses the old module's name.
- **Chunks 6 and 7 merged**: the planned split would have left a commit where the tests fail to import.
- **Work pulled forward**: chunks 2 and 3 deleted test files scheduled for T025 and T026. T025 and T026 were still ticked in chunk 6.
- **PR gate permissions**: the PR caller grants `id-token: write`. No token is minted, because signing is skipped when `publish` is false.
- **Compose test port**: the shared deployment moved from 8021 to 8061 because this host resets loopback 8021. CI hosts are not known to need this.
- **`notes/operator-guidance.md`**: kept in the spec dir as the source the docs rewrite drew on.
- **High-severity fixes**: the five above all came from the review pass, and all were fixed before this report.
- **E2E**: the GitHub-only paths can't run locally (§4, last row). Their CI-side checks are quickstart §3, §4 and §5.

## 7. Suggested next steps

1. Registry admin: create the private Harbor project `opsmill/infrahub-sync` with tester pull credentials, and confirm that `HARBOR_HOST`, `HARBOR_USERNAME` and `HARBOR_PASSWORD` are shared with this repo (see `pr-notes.md`).
2. Review `pr-notes.md`, then open the PR into `feature/v3-develop` when you're ready. Opening the PR, pushing and labelling are left to you.
3. After it merges, run quickstart §4 (a dispatch with a throwaway tag) to prove push, signing, SBOMs and that `latest` doesn't move.
4. Decide on the deferred items: bootstrap password rotation, the lifecycle error message, and the simplify cuts.
5. Fix the `docs.generate` drift on `reference/cli.mdx` separately.
6. Optional: two Docker volumes, `infrahub-sync_postgres-data` and `infrahub-sync_object-store-data`, exist locally, created around 12:08Z by something other than the test projects. Remove them if they're not yours.
