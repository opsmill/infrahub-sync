# Implementation report: lineage of the data an import writes

- Status: INCOMPLETE. Two tasks are partial (T004 and T057). No local-pass evidence is missing.
- Spec directory: `dev/specs/014-import-lineage`
- Base commit: `896795ed`
- Head commit before this report: `57f2811a`
- Wall-clock time: about 5.5 hours (2026-10-22T19:50Z to 2026-10-23T01:15Z, including Setup)

## Chunk ledger

| Chunk | Tasks | Result | Commits | Flagged upward |
|-------|-------|--------|---------|----------------|
| 1a. Setup: research reads | T001-T003 (3) | 3 done | `f4831f82` | T001 found a read: the plan's `client.all` with `include` returns edge metadata when `include_metadata=True` is passed, and adds no request. T002: an update reads current peers once when an owner is named and sends the owner only on edges it adds. T003: the checkpoint copies a fixed file list. |
| 1b. Setup: live checks | T004-T005 (2) | 1 done, 1 partial | `1ae09643` | T005 found the smallest permission set on Infrahub 1.10.6 (research R-7); a rerun on 1.11.9 found the same set. T004 is partial: `tasks/bench.py` could not be used, so the read time was measured with a direct SDK read. |
| 2. Foundational | T006-T008 (3) | 3 done | `336b55c4` | The CoreAccount lookup by the source system's name was removed, together with both fallbacks. With no owner named, the import now sends no owner. |
| 3a. US1: write path and errors | T009-T011, T014-T016 (6) | 6 done | `fc049cf7` | New module `infrahub_sync/infrahub_errors.py`. A 403 on the schema read now gives `forbidden` instead of `unauthorized`. |
| 3b. US1: manifest and apply refusal | T012, T013, T017, T018 (4) | 4 done | `11b946fd` | Uses stdlib `logging` with `extra=`, not structlog (see decisions). `owner` was added to the manifest early. |
| 3c. US1: integration | T019 (1) | 1 done | `64f1e611` | Product bug fixed: the import account was recorded by its display label. It is now recorded by its name, read with the adapter's own `AccountProfile` query. |
| 4a. US2: validation and lookup | T020-T022, T025, T026 (5) | 5 done | `66cb116b` | The named-node lookup now selects the name only, so it needs `view` on the named kind and nothing more (research R-7). |
| 4b. US2: owner payload | T023, T024, T027-T029 (5) | 5 done | `b54a9d24` | T028 needed only a check, because chunk 3b had already added the field. |
| 4c. US2: integration | T030 (1) | 1 done | `838d9d23` | Confirmed live that `view` on CoreAccountGroup is enough for the owner lookup. |
| 5a. US3: metadata read | T031, T034-T036 (4) | 4 done | `cb1e8ed2` | While metadata is read, the destination always loads in full, so no value Infrahub holds can show "not recorded". |
| 5b. US3: last-change file | T032, T037-T039 (4) | 4 done | `e7a000df` | Before Infrahub 1.7.0, every record has null values and `metadata_read: false`. |
| 5c. US3: service and CLI | T033, T040-T042 (4) | 4 done | `d9bfc8ec` | Last-change lines show only with `runs plan --detail`. `compatibility.py` needed no change. |
| 5d. US3: integration | T043 (1) | 1 done | `ec4446c2` | On 1.10.6, and again on 1.11.9, an attribute that was never set still holds `updated_by` from the create, so only a relationship with no edge shows "not recorded". |
| 6. US4: protect | T044-T048 (5) | 5 done | `5b911dea` | No product code was needed. Infrahub 1.10.6 accepts an import's change to a protected value, and so does 1.11.9 (the protection tests passed on it). On an update, every value sent again gets the import account as source. |
| 7a. Polish: guards, docs, changelog | T049-T054 (6) | 6 done | `91b39b6f` | The page does not say that a value last changed before the upgrade shows "not recorded", because nothing confirms it. |
| 7b. Polish: checks, benchmark, quickstart | T055-T057 (3) | 2 done, 1 partial | `99b579eb`, `c34bb799`, `7c45ed98` | The quickstart run found two gaps, fixed in review (findings A and B). It also raised the branch question in the product-owner section. |
| Review fixes | n/a | done | `0783d3c7`, `57f2811a` | See review findings. |

Totals: 57 tasks, 55 done, 2 partial, 0 blocked.

## Tasks not completed

- T004, partial. `tasks/bench.py` pins Infrahub 1.11.3, needs a NetBox dump that does not exist, has no branch option and reports the time of the whole sync. The read time was measured instead with a direct SDK read of 10,014 throwaway nodes on 1.10.6. Real NetBox kinds were not measured. T056 used the same method, so the two sets of numbers can be compared. The result is information only and does not block the merge.
- T057, partial. Every quickstart scenario ran through the CLI, the Sync API and the worker, on a copy of the development stack that this run created, against Infrahub 1.10.6. The source was the Infrahub adapter reading a branch, not NetBox. The results are in the quickstart.md section "Quickstart results, for the pull request description".

## Local-pass evidence

All runs were local on macOS arm64 with Python 3.11.8 and infrahub-sdk 1.23.2. Each live Compose project ran with anonymous access off, and was torn down after the run. The live rows up to and including the CLI end-to-end row ran on Infrahub 1.10.6. The rows marked "Infrahub 1.11.9" are the reruns after the product owner moved this feature to 1.11.9 on 2026-10-23.

| Test id | Type | Run command | Passed at (ISO 8601) | Environment context | Verbatim pass line |
|---------|------|-------------|----------------------|---------------------|--------------------|
| Full unit tier (final) | unit | `uv run invoke tests.tests-unit` | 2026-10-23T01:09:32Z | offline | `5891 passed, 4 skipped, 219 deselected` |
| `tests/configuration/test_infrahub_lineage_settings.py` (24 tests, plus 2 whitespace cases) | unit | `uv run pytest -q <6 files> -rA` | 2026-10-23T00:40Z | offline | `227 passed in 1.58s` |
| `tests/adapters/test_infrahub_lineage.py` (all tests) | unit | `uv run pytest tests/configuration/test_infrahub_lineage_validation.py tests/adapters/test_infrahub_lineage.py tests/adapters/test_infrahub_lineage_apply.py ... -v` | 2026-10-22T21:39:54Z, and in the final unit tier | offline | `52 passed in 0.18s` |
| `tests/adapters/test_infrahub_planned_write.py` (new and changed: create, update, owner, protect, pool, lineage-not-resolved tests) | unit | `uv run invoke tests.tests-unit` | 2026-10-23T01:09:32Z | offline | `...::test_a_write_before_the_lineage_is_resolved_is_refused_with_no_request PASSED` |
| `tests/adapters/test_infrahub_error_classification.py` (17 tests) | unit | `uv run pytest -v -p no:cacheprovider ...` | 2026-10-22T20:55:49Z, chain tests at 2026-10-23T00:40Z | offline | `34 passed in 0.38s` |
| `tests/plan/test_lineage_manifest.py` (all tests) | unit | `uv run pytest tests/plan/test_lineage_manifest.py -v` | 2026-10-22T23:17Z | offline | `23 passed, 90 deselected in 0.27s` (protect subset); full file in the unit tier |
| `tests/adapters/test_infrahub_lineage_apply.py` (all tests) | unit | `uv run pytest ... -v` | 2026-10-22T21:39:54Z | offline | `52 passed in 0.18s` |
| `tests/configuration/test_infrahub_lineage_validation.py` (all tests) | unit | `uv run pytest -q -rA -p no:randomly ... -k protect` | 2026-10-22T23:17Z | offline | `23 passed, 90 deselected in 0.27s` |
| `tests/adapters/test_infrahub_metadata_read.py` (23 tests) | unit | `uv run pytest -p no:cacheprovider tests/adapters/test_infrahub_metadata_read.py ... -q -rA` | 2026-10-22T22:23:13Z, version-query tests at 2026-10-23T00:40Z | offline | `92 passed in 0.66s` |
| `tests/plan/test_last_changes.py` (20 tests) | unit | `uv run pytest tests/plan/test_last_changes.py ... -v` | 2026-10-22T22:40:30Z | offline | `128 passed in 0.56s` |
| `tests/service/test_plan_checkpoint.py::test_the_plan_checkpoint_carries_the_last_change_file_the_manifest_names` | unit | same as above | 2026-10-22T22:40:30Z | offline | `128 passed in 0.56s` |
| `tests/service/test_plan_last_changes.py` (3 tests), `tests/service/test_compatibility.py` (2 new tests) | unit | `uv run pytest tests/service/test_plan_last_changes.py tests/service/test_compatibility.py ... -v` | 2026-10-22T22:48:54Z | offline | `28 passed in 0.66s` |
| `tests/cli/test_client_commands.py` (4 last-change tests, 5 failure-cause tests) | unit | `uv run pytest ... -v` | 2026-10-22T22:48:54Z and 2026-10-23T00:40Z | offline | `28 passed in 0.66s` / `227 passed in 1.58s` |
| `tests/service/test_flow_and_prefect.py` (6 new failure-cause tests) | unit | `uv run pytest -q <6 files> -rA` | 2026-10-23T00:40Z | offline | `227 passed in 1.58s` |
| `tests/adapters/test_infrahub_write_payload_guard.py` (8 cases) | unit | `uv run pytest tests/adapters/test_infrahub_write_payload_guard.py ... -v` | 2026-10-22T23:27:45Z | offline | `11 passed in 0.19s` |
| Modified existing tests: `test_infrahub_incremental.py`, `test_infrahub_loader_fields.py`, `test_registered_execution.py`, `test_apply_conformance.py`, `test_models.py`, `test_schema_validation.py` | unit | `uv run invoke tests.tests-unit` | 2026-10-23T01:09:32Z | offline | `5891 passed, 4 skipped, 219 deselected` |
| `tests/integration/test_infrahub_lineage_integration.py`, all 14 functions, 16 test ids with parameters (rerun after the helper `import_adapter` took its lineage settings as one `LineageSettings` value) | integration | `uv run pytest -m integration -p no:cacheprovider --no-cov -rA tests/integration/test_infrahub_lineage_integration.py` | 2026-10-23T15:13:17Z | Compose project `lineage014-fmt`, Infrahub 1.10.6, port 18072, anonymous access off, opt-ins `INFRAHUB_SYNC_LINEAGE_WRITE_DEFAULT_BRANCH=1` and `INFRAHUB_SYNC_LINEAGE_REPOSITORY_LOCATION=file:///tmp/lineage-repo.git` set | `16 passed in 68.32s (0:01:08)` |
| `tests/integration/test_infrahub_keyed_write_integration.py` (fixture changed, 11 tests) | integration | `uv run pytest -m integration --no-cov tests/integration/test_infrahub_lineage_integration.py tests/integration/test_infrahub_keyed_write_integration.py` | 2026-10-23T01:01:44Z | `lineage014-h3` | `27 passed in 142.43s` |
| `tests/integration/test_infrahub_replace_set_shrink_integration.py` (fixture changed) | integration | `uv run pytest -m integration tests/integration/test_infrahub_replace_set_shrink_integration.py -v` | 2026-10-22T20:49:21Z, and again after the account-name fix | `lineage014-us1`, port 18014 | `2 passed in 25.12s` |
| CLI end-to-end: plan with a 403, account mismatch, missing group | e2e (manual) | `infrahub-sync diff` / `apply` / `runs results` against a dev-stack copy | 2026-10-23T00:34Z to 00:38Z | image `infrahub-sync:lineage014-rf`, project `lineage014-rf-sync` | `cause: a missing permission: the import account needs the permission ...` |
| Full unit tier after the version stubs in `test_infrahub_lineage.py`, `test_infrahub_metadata_read.py` and `test_infrahub_planned_write.py` changed to 1.11.9 | unit | `uv run invoke tests.tests-unit` | 2026-10-23T18:34:26Z | offline | `5891 passed, 4 skipped, 219 deselected, 4 warnings in 144.90s (0:02:24)` |
| Infrahub 1.11.9: `tests/integration/test_infrahub_lineage_integration.py`, 16 test ids | integration | `uv run pytest -m integration -p no:cacheprovider --no-cov -rA tests/integration/test_infrahub_lineage_integration.py` | 2026-10-23T18:21:26Z, and 2026-10-23T18:32:22Z after the version wording in the file changed | Compose project `lineage014-v1119`, Infrahub 1.11.9 (digest `sha256:bc9a36f0…65f`), port 18119, anonymous access off, opt-ins `INFRAHUB_SYNC_LINEAGE_WRITE_DEFAULT_BRANCH=1` and `INFRAHUB_SYNC_LINEAGE_REPOSITORY_LOCATION=file:///tmp/lineage-repo.git` set | `16 passed in 67.56s (0:01:07)`; `16 passed in 49.73s` |
| Infrahub 1.11.9: `tests/integration/test_infrahub_keyed_write_integration.py` (11 tests) and `tests/integration/test_infrahub_replace_set_shrink_integration.py` (2 tests) | integration | `uv run pytest -m integration -p no:cacheprovider --no-cov -rA tests/integration/test_infrahub_keyed_write_integration.py tests/integration/test_infrahub_replace_set_shrink_integration.py` | 2026-10-23T18:22:41Z | same project as above | `13 passed in 139.01s (0:02:19)` |
| Infrahub 1.11.9: T005 permission probe, default branch and `lineage-probe` | live probe (scratch scripts) | `setup.py`, then `drive.py main`, `drive.py branch`, `verify.py`, `named.py` | 2026-10-23T18:27:14Z to 18:28:06Z | new `lineage014-v1119` project | Same seven permissions on `main` and six on the branch as on 1.10.6; `non-named failures: none` on both (research R-7) |
| Infrahub 1.11.9: rename and deletion of a branch-agnostic `CoreAccountGroup`, and the "not recorded" read | live probe (scratch scripts) | `probe.py`, `probe_delete.py`, `notrec.py` | 2026-10-23T18:28:14Z to 18:28:45Z | same project | Output identical to 1.10.6 after the version line; an empty attribute holds `updated_by`, an empty relationship has no edge (research R-2, R-7) |

## Review findings

| Severity | File | Summary | Handling |
|----------|------|---------|----------|
| High | `infrahub_sync/service/flow.py`, `potenda/__init__.py` | A 403 during a plan, or during lineage resolution, was recorded only as a class name, so the missing permission reached only the worker log (FR-008, SC-006) | Fixed in `0783d3c7` |
| High | `service/flow.py`, `cli.py` | The causes of a refused lineage, such as an account mismatch, a plan with no recorded lineage or a missing named node, did not reach `runs results` or the CLI (FR-011, FR-012, SC-007) | Fixed in `0783d3c7` |
| High | `tests/integration/test_infrahub_lineage_integration.py` | No live test covered a named `source` | Fixed in `57f2811a`. CoreAccount, CoreAccountGroup and both repository kinds are now tested live. |
| High | same | No live test covered the permission set for the default branch | Fixed in `57f2811a`, as an opt-in test |
| Medium | `adapters/infrahub.py` | When no lineage was resolved, a write sent no lineage and raised nothing | Fixed: `LineageNotResolvedError` |
| Medium | code comments | Comments named research, decision and clarification IDs from the spec | Fixed |
| Medium | `potenda/__init__.py` | `# pylint: disable=too-many-lines` was added instead of splitting the file | Deferred |
| Medium | `plan/last_changes.py`, `adapters/infrahub.py` | The collector reads adapter types through `Any` and `getattr` | Deferred |
| Medium | tests | No engine-level test covers a plan with `full_extract=False` and metadata on | Deferred |
| Medium | `adapters/infrahub.py` | The metadata branch of `list_changed_since` cannot be reached while metadata is read | Deferred |
| Low | `adapters/infrahub.py` | `_read_metadata_support` logged GraphQL text and stopped the plan on a 403 | Fixed |
| Low | `adapters/infrahub.py` | A named kind missing from the schema gave a raw SDK error | Fixed |
| Low | `configuration/capabilities.py` | A name with leading or trailing spaces passed registration | Fixed |
| Low | docs | Protection claims were not scoped to 1.10.6, and the `metadata_read` wording was incomplete | Fixed |
| Low | various | The same shapes are defined in the client, plan and adapter types; `kind: str` is used instead of the Literal; `LineageSettingsParse` does not check its own invariant; `updated_at` is not validated; SC-004 is tested with a single update | Deferred |

## Autonomous decisions

- Chunks: phases with more than about 6 tasks were split into 2 to 4 chunks, each with its tests next to their implementation. Integration tasks (T019, T030, T043) were separate chunks.
- Logging: AGENTS.md and the constitution ask for `structlog`, but no module in `infrahub_sync/` uses it. The new code follows the adapter's stdlib `logging` with `extra=`.
- T004 and T057 are accepted as partial, for the reasons above.
- The product owner's psutil workaround was passed to every chunk. No chunk reported needing it.
- `docs.generate` rewrites `docs/docs/reference/cli.mdx` and drops a hand-written paragraph about `INFRAHUB_SYNC_API_TOKEN`. That drift existed before this branch, so the change was reverted each time and not committed.
- Medium and low review findings were deferred where they are refactors with no change in behaviour.
- Infrahub version (product owner, 2026-10-23): this feature now uses Infrahub 1.11.9, not 1.10.6. The integration files, the T005 permission probe, the rename reproduction and the "not recorded" read ran again on 1.11.9, with no difference in results; the only change seen is the order of the permissions in some refusal messages (research R-7). Earlier 1.10.6 results stay recorded with their version. The spec, plan, quickstart, research, unit-test version stubs and `docs/docs/adapters/infrahub.mdx` now name 1.11.9 where the 1.11.9 run confirmed them. The repository-wide pins of 1.10.6 (Compose file, preview settings, compose and preview tests, other docs pages) predate this feature and were not changed.

## Product-owner decisions (2026-10-23)

1. Branch of a named source or owner (FR-011): the plan and apply look up a named source or owner on the destination branch the run uses. On Infrahub 1.10.6 the four allowed kinds are `branch: agnostic`, so a rename or deletion on any branch applies to every branch. No code change.
   - Recorded in `spec.md` (FR-011, and Clarifications, session 2026-10-23), `plan.md` ("Items left open") and `docs/docs/adapters/infrahub.mdx` ("Name a source or an owner").
   - A reproduction on 2026-10-23 on Infrahub 1.10.6 confirmed the quickstart finding. After a rename on `main`, a filter by the earlier name on a branch created before the rename still matches the group, which reports its new name. On a branch created after the rename, and on `main`, the filter matches nothing. A deletion on `main` applies to every branch. The two earlier runs differ because the review-fix run used a branch created after the rename.
   - The same reproduction on Infrahub 1.11.9, on 2026-10-23 at 18:28 UTC, gave the same results: the stale match by the earlier name still reproduces. The draft issue now names both versions.
   - A draft upstream bug report is at `/private/tmp/claude-503/-Users-bkohler-automation-opsmill-infrahub-sync/98127f2a-4ebb-4d16-a202-bb38be574daa/scratchpad/draft-infrahub-issue-branch-agnostic-rename.md`. It is not filed.
2. Update payloads: an update should send only the fields the plan changes. Not implemented in this feature. Recorded as an open follow-up item in `plan.md` ("Items left open").
3. Logging: keep the standard `logging` module. Moving to `structlog`, or amending the constitution's logging line to match the code, is recorded as an open follow-up item in `plan.md` ("Items left open"). The constitution is not changed.

## Suggested next steps

1. Decide whether to file the draft upstream issue at `/private/tmp/claude-503/-Users-bkohler-automation-opsmill-infrahub-sync/98127f2a-4ebb-4d16-a202-bb38be574daa/scratchpad/draft-infrahub-issue-branch-agnostic-rename.md`.
2. Open a pull request with the label `changes/minor`. Use the quickstart.md section "Quickstart results, for the pull request description".
3. Address the deferred medium review findings, starting with the split of `potenda/__init__.py`.
4. Follow-up work: make an update send only the fields the plan changes, so unchanged values keep their source.
5. Follow-up work: move the package to `structlog`, or amend the constitution's logging line to match the code.
