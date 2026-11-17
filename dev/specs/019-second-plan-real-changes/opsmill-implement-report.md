# Implementation report: a second plan shows only real changes, and the plan step is measured by part

**Status**: INCOMPLETE. Tasks T054 to T057 need the benchmark machine and are open. Every other
task is done, and every test added or changed has local-pass evidence.

- Spec directory: `dev/specs/019-second-plan-real-changes`
- Branch: `019-second-plan-real-changes` (not pushed)
- Base commit: `d3e4ae08`
- Head commit: `4c0c2012`
- Date: 2026-11-16, from about 14:50 to 19:25 UTC
- Unit tier at head: 8772 passed, 4 skipped, 2 xfailed (8517 passed at the base)

## Chunk ledger

| Chunk | Tasks | Outcome | Commits | Flagged by the chunk |
| --- | --- | --- | --- | --- |
| 1. Live probes | T001-T003 (3) | 3 done | `b12c400d` | Infrahub 1.11.9 marks a node it creates itself with the creator `__system__`; the only such node in a new branch is the `IpamNamespace` named `default`. An Upsert with `<field>: null` removes a one-peer edge. The probe reads a running stack instead of starting it. |
| 2. Foundational | T004-T007 (4) | 4 done | `edcd1f55` | The LAG relationship of the fixture is named `bundle`, as in the NetBox example. `FixtureSource` in `tests/plan/problem_plan.py` accepts lists of peers. |
| 3. Built-in deletes | T008-T016 (9) | 9 done | `25a67795` | #167 does not reproduce on the current code; no fix was needed. On Infrahub earlier than 1.7.0, node metadata is not read and the delete of `default` comes back. |
| 4. Name-based acceptances | T017-T020 (4) | 4 done | `90fefbd1` | ADR 0047 written. Found two more places that assumed the `default` delete: the nightly `from-netbox` check and `docs/docs/v2-to-v3.mdx`. |
| 4b. Follow-up | none (from chunk 4) | done | `a3ce4db0` | The nightly check expects 1,687 operations and adds the `default` namespace to the branch count. Three tutorial pages corrected. |
| 5. Format 5 and readers | T021, T022, T028, T029 (4) | 4 done | `2c51a9a6` | Earlier releases already refuse an unknown plan format; the readers did not change. |
| 6. Deriving an emptied relationship | T023, T030, T031, T031a (4) | 4 done | `c1cb0c0f`, `580a2990` | Before this feature, a required one-peer field missing from NetBox data failed the whole plan; a field set to `None` was left out as `wrong_value_type`. Both now give `required_relationship_empty_in_source`. |
| 7. Apply, verify, CLI detail | T024, T025, T027, T032, T033 (5) | 5 done | `a817392e` | Verify needed no change. The pre-write checks now render an emptied relationship. The task named `tests/plan/test_destination_check.py`; the file is `tests/test_destination_check.py`. |
| 8. Change tests and docs | T026, T034-T036 (4) | 4 done | `4e80a8b7`, `617452d7` | ADR 0046 written. `compose-deployment.mdx` and `planned-write-and-apply.md` described the old behavior and were updated. |
| 9. Timings: store and observer | T037-T039, T042-T044 (6) | 6 done | `fd52064f` | A plan step makes five store writes, not four as `plan.md` states: the first is an empty document at stage start. Comparing starts when `Potenda.diff()` is called. A failed part keeps no end; the response gives it `stopped_at` equal to the verdict time. |
| 10. Timings: API, CLI, docs | T040, T041, T045-T048 (6) | 6 done | `ee46e604` | `runs show` prints `plan_<part>: <s>s`, `running`, `stopped after <s>s` or `stopped`. The web interface types were not changed (out of scope). |
| 11. Benchmark fields | T049-T053 (5) | 5 done | `293742a3`, `c797d998` | Only a `warm` cell can pass after a stop for records left out; `cold` and `changed` cells fail because nothing was written. |
| 12. Polish | T058-T062 (5) | 5 done | `2b72953b`, `c6be32a9` | Live `from-netbox` check: the first plan held 1,687 creates and no delete; after apply, the second plan held 0 operations (SC-001). `testing-tiers.md` corrected. |
| Review fixes | none | 9 fixed | `4c0c2012` | See "Review findings". |

## Tasks not completed

- **T054**: the measurement of tiers S and M needs the benchmark machine (8 CPUs, 16 GB, no other
  work). Not run in this session.
- **T055**: the results page is written from the 18 records of T054.
- **T056**: the verdict on FR-034 needs the results of T054.
- **T057**: a change of the plan step, or a GitHub issue, depends on T056.

Under the clarification of 2026-11-16, the feature is complete without these tasks, and a GitHub
issue tracks FR-031 to FR-036. That issue is not opened; its draft is under "Suggested next steps".

## Local-pass evidence

All runs were on macOS, Python 3.11.8, in the worktree, with `uv run pytest`. "Environment" is
`n/a` unless stated.

| Test id | Type | Run command | Passed at | Environment | Pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/integration/test_infrahub_builtin_nodes_integration.py::test_list_nodes_that_infrahub_creates_itself` | integration | `uv run pytest -m integration -v <file>` with `INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN` | 2026-11-16T15:00:58Z | Compose project `infrahub-sync-dev`, Infrahub 1.11.9 at `http://127.0.0.1:8000` | `PASSED` |
| `tests/integration/test_infrahub_one_peer_clear_integration.py::test_upsert_with_null_one_peer_relationship_removes_the_edge` | integration | same run | 2026-11-16T15:00:58Z | same | `2 passed in 14.97s` |
| `tests/plan/test_digest.py` (4 tests) | unit | `uv run pytest tests/plan/test_digest.py tests/plan/test_applying_destination.py -q -rA` | 2026-11-16T15:20:10Z | n/a | `9 passed in 1.07s` |
| `tests/plan/test_applying_destination.py` (6 tests at head) | unit | `uv run pytest <8 files of chunk 3> -q` | 2026-11-16T15:35:29Z | n/a | `46 passed in 4.44s` |
| `tests/plan/test_builtin_deletes.py` (11 tests) | unit | `uv run pytest tests/plan/test_builtin_deletes.py -q --log-level=INFO` | 2026-11-16T15:47:20Z | n/a | `11 passed` |
| `tests/adapters/test_infrahub_builtin_nodes.py` (8), `tests/adapters/test_infrahub_lag_second_plan.py` (3), `tests/adapters/test_{netbox,nautobot,genericrestapi}_peer_order.py` (3 each) | unit | chunk 3 run of 8 files | 2026-11-16T15:35:29Z | n/a | `46 passed in 4.44s` |
| `tests/plan/test_second_plan.py` unchanged-data tests (9) | unit | chunk 3 run of 8 files | 2026-11-16T15:35:29Z | n/a | `46 passed in 4.44s` |
| `tests/plan/test_second_plan.py` change tests (3) | unit | `uv run pytest tests/plan/test_second_plan.py -k "changed_attribute or emptied or replaced" -rA -q` | 2026-11-16T17:42:56Z | n/a | `3 passed, 9 deselected in 0.22s` |
| `tests/test_benchmark_runner.py`, `tests/test_nightly_v2_gate.py` (chunk 4 changes) | unit | `uv run pytest tests/test_benchmark_runner.py tests/test_nightly_v2_gate.py -q` | 2026-11-16T16:05:40Z | n/a | `194 passed in 16.15s` |
| `tests/test_nightly_e2e.py` (3 changed) | unit | `uv run pytest tests/test_nightly_e2e.py -v` | 2026-11-16T16:08:25Z | n/a | `11 passed in 0.03s` |
| `tests/plan/test_format_5.py` (28 cases at chunk 5) | unit | `uv run pytest tests/plan/test_format_5.py -q` | 2026-11-16T16:36:44Z | n/a | `28 passed in 0.36s` |
| Format pins in `test_models`, `test_format_4`, `test_last_changes`, `test_lineage_manifest`, `test_plan_format_3`, `test_problem_pass`, `test_problems`, `test_potenda_plan_artifact` | unit | `uv run pytest <file> -q`, one per file | 2026-11-16T16:36:46Z to 16:37:02Z | n/a | `108`, `16`, `38`, `15`, `35`, `16`, `10`, `1 passed` |
| `tests/plan/test_emptied_relationship.py` (11), `test_destination_id_capture.py::test_a_null_cardinality_one_peer_on_an_update_empties_the_relationship`, `test_report_models.py::test_the_ten_problem_type_names_are_stable` | unit | `uv run pytest <ids> -q -p no:cacheprovider` | 2026-11-16T17:15:22Z | n/a | `13 passed in 0.13s` |
| `tests/plan/test_apply_conformance.py` (12 new cases) | unit | `uv run pytest tests/plan/test_apply_conformance.py -q -k "rendered_before or emptied or pre_write_checks"` | 2026-11-16T17:26:30Z | n/a | `14 passed, 15 deselected in 0.27s` |
| `tests/test_destination_check.py` (4 cases) | unit | `uv run pytest tests/test_destination_check.py -q -k "emptied or empty_one_peer"` | 2026-11-16T17:26:30Z | n/a | `4 passed, 30 deselected in 0.43s` |
| `tests/cli/test_client_commands.py` emptied-relationship tests (4) | unit | `uv run pytest tests/cli/test_client_commands.py -q -k "becomes_empty or emptied_relationship"` | 2026-11-16T17:26:30Z | n/a | `4 passed, 194 deselected in 0.26s` |
| `tests/product_store/test_plan_step_column.py` (33 tests, 2 profiles) | unit | `uv run pytest tests/product_store/test_plan_step_column.py -q` | 2026-11-16T18:10:42Z | local and postgresql-emulated profiles | `66 passed in 1.50s` |
| `tests/service/test_plan_step.py` (39) | unit | `uv run pytest tests/service/test_plan_step.py -q` | 2026-11-16T18:10:45Z | n/a | `39 passed in 0.93s` |
| `tests/test_execution_surface.py::test_the_plan_step_observer_seam_reaches_the_engine_without_changing_the_factory_call` | unit | `uv run pytest <id> -q` | 2026-11-16T18:10:47Z | n/a | `1 passed in 1.91s` |
| Plan-step tests in `tests/cli/test_client_commands.py` (5) and `tests/service/test_http_api.py` (4) | unit | `uv run pytest tests/cli/test_client_commands.py tests/service/test_http_api.py -v -k "plan_step or how_a_part_that_did_not_end or keeps_a_plan_step_part"` | 2026-11-16T18:24:38Z | n/a | `9 passed, 331 deselected, 1 warning in 0.84s` |
| `tests/test_benchmark_runner.py` (14 new, 4 changed cases) | unit | `uv run pytest tests/test_benchmark_runner.py -q -p no:randomly -k "<chunk 11 filter>"` | 2026-11-16T18:39:05Z | n/a | `18 passed, 153 deselected in 0.17s` |
| Review-fix tests (16 ids, listed in commit `4c0c2012`) | unit | `uv run pytest -q -rp -p no:cacheprovider <ids>` | 2026-11-16T19:22:19Z | n/a | `16 passed in 1.13s` |
| `from-netbox` example check (T062, not a pytest test) | live check | `quickstart.md` section 3: `netbox.seed --dataset demo`, `dev.start`, schema load, `netbox.demo-package --dev-stack`, `configs register`, `plan`, `apply`, `plan` | 2026-11-16T18:55:02Z | Compose project `infrahub-sync-dev`, Infrahub 1.11.9, local NetBox `demo` dataset | second plan: `operations: 0`, outcome `no-change` |

The full unit tier at head ran green in the review-fix chunk: 8772 passed, 4 skipped, 2 xfailed.
The Python 3.10 legs (`tests.check-310`) were not run in this session.

## Review findings

Four read-only reviews ran in parallel on `d3e4ae08..HEAD`: code, tests, error handling, and types
with comments. The simplification pass was not run, because it only gives advice.

| Severity | File | Finding | Status |
| --- | --- | --- | --- |
| High | `infrahub_sync/plan/derive.py`, `tests/plan/test_emptied_relationship.py` | A field that the source model marks optional and the destination schema requires was skipped with no problem, against FR-013a. | Fixed in `4c0c2012`: a problem is reported when the destination node holds a peer. A create with that field empty still gets no problem. |
| Medium | `infrahub_sync/adapters/ipfabricsync.py` | A peer that the source names but does not return was set to `None`, so a plan emptied an edge that the source still names. | Fixed: the case is recorded as a filtered peer. Other adapters checked; none had the same pattern. |
| Medium | `infrahub_sync/adapters/infrahub.py` | The pre-write check reported a schema fault when the SDK rendered an emptied relationship wrongly. | Fixed: its own message. |
| Medium | `infrahub_sync/product_store/store.py` | The link insert path skipped the check of the timing document. | Fixed. |
| Medium | `tests/service/test_plan_step.py` | The log test did not check the log. | Fixed. |
| Medium | `tests/plan/test_format_5.py` | No test approved a format 4 plan (FR-012). | Fixed. |
| Medium | `docs/docs/reference/sync-http-api.mdx`, `docs/docs/running-a-sync.mdx` | Placed the plan report in `comparing`; it is built in `checking`. | Fixed. |
| Low | `infrahub_sync/plan/derive.py` | A missing owner record emptied a relationship without a log line. | Fixed: the relationship is left alone and one warning names the kind and the field. |
| Low | `tasks/bench.py` | A failed or cancelled run after a published plan could be recorded as `ok`. | Fixed: checks phase `planned` and outcome `left-out-review-required`. |
| Low | `plan-artifact.md`, ADR 0046 | The refusal list left out deletes. | Fixed. |
| Low | `infrahub_sync/__init__.py` | A misspelled `mapping` on a required relationship now leaves every record of the kind out as `required_relationship_empty_in_source` instead of failing the read. | Deferred. |
| Low | `tests/plan/problem_plan.py` | The stand-in destination records the built-in mark without the `metadata_read` condition. The real path is covered by `tests/adapters/test_infrahub_builtin_nodes.py`. | Deferred. |
| Low | `infrahub_sync/adapters/infrahub.py` | When the server version query is refused on Infrahub 1.11.9, metadata is not read and the `default` delete comes back. No test covers this. | Deferred. |
| Low | `infrahub_sync/plan/models.py` | Two readers of the validation-context `format_version` treat a `bool` differently. | Deferred. |
| Low | `infrahub_sync/service/plan_step.py`, `infrahub_sync/client/models.py` | Nothing checks that `ended_at` and `stopped_at` exclude each other or follow `started_at`. A part with a verdict earlier than its start shows `stopped`, which the docs do not state. | Deferred. |
| Low | `infrahub_sync/plan/derive.py` | A docstring says "declared as a list"; the check reads the default value. | Deferred. |

## Autonomous decisions

- **FR-013a design before the run**: I amended research R6 and task T031a (commit `d3e4ae08`) to
  raise the new problem where `required_peer_excluded_by_filter` is raised.
- **Chunk splits**: the User Story 2 tasks were grouped by module (format, derivation, apply, docs)
  so that each test is committed with its code. Chunk 4b was added for the two places that chunk 4
  found.
- **Live runs**: the chunk subagents started and removed the Compose project `infrahub-sync-dev`
  and the local NetBox twice. No other container was touched.
- **Review-fix scope**: the high finding and the medium and low findings that can remove an edge
  or record a wrong benchmark result were fixed. The other low findings are deferred.
- **FR-013a condition**: the destination-schema case reports a problem only when the destination
  node holds a peer.
- **Plan deviations recorded**: five store writes per plan step instead of four; comparing starts
  at `Potenda.diff()`.
- **GitHub issue for T054 to T057**: not opened. Opening it is an outward action for the user.

## Suggested next steps

1. Run the Python 3.10 legs: `uv run invoke tests.check-310`.
2. Review the branch and open a pull request into `feature/v3-develop` when ready.
3. Open the GitHub issue for the measurement, or run T054 to T057 on the benchmark machine. Draft:

   > **Measure the plan step at tiers S and M and check the plan time of a second plan**
   >
   > Spec `019-second-plan-real-changes` adds the plan-step timings and the benchmark fields, but
   > the measurement needs the benchmark machine (8 CPUs, 16 GB, no other work). Remaining work:
   >
   > - Run tiers S and M, scenarios `cold`, `warm` and `changed`, three times each, with
   >   Infrahub 1.11.9, before any speed change (FR-031, FR-033).
   > - Write `docs/docs/development/guides/netbox-benchmark-results.md` from the 18 records
   >   (FR-032).
   > - Compare the median plan time of `warm` and `changed` with `cold` at tier M (FR-034). If one
   >   part accounts for the difference, change it and check that the operations digests do not
   >   change (FR-035). Otherwise record the result (FR-036).
   >
   > Not known: when the benchmark machine is available.

4. Decide on the deferred low findings, in particular the misspelled `mapping` case.
