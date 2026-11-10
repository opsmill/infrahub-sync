# Implementation report: Prefect run observability and the missing Sync API address

- **Status**: DONE
- **Spec directory**: `dev/specs/016-prefect-run-observability`
- **Branch**: `016-prefect-run-observability`. It is not pushed and has no pull request.
- **Base commit**: `8bc54ce9`, the last preparation commit, on top of `feature/v3-develop` at `3c617102`.
- **Head commit**: the commit that adds this report, after `b905422a`.
- **Wall-clock time**: about 1 hour, from 15:30 to 16:25 UTC on 2026-11-05.
- **Tasks**: 35 of 35 done.
- **Unit tier**:
  - before: `6422 passed, 7 skipped, 1 xfailed`;
  - after: `6529 passed, 4 skipped, 246 deselected, 1 xfailed`.

## Chunk ledger

| Chunk | Tasks | ✅ / ⚠️ / ❌ | Commit | Flagged by the chunk |
| --- | --- | --- | --- | --- |
| 0. Setup: baseline (run by the orchestrator) | T001 | 1 / 0 / 0 | `c4ee909a` | None |
| 1. US6: CLI hint | T002–T004 | 3 / 0 / 0 | `f0d6adc0` | The guard test uses `sync --wait-timeout 0`, because Typer rejects `runs list --limit 0` before the client runs. |
| 2. US5: terminal state | T005–T006 | 2 / 0 / 0 | `3117df11` | Three existing tests now expect the verdict (FR-019). |
| 3. US4: refused claim | T007–T009 | 3 / 0 / 0 | `d7d2b39a` | `flow.py` reached the Pylint 1,000-line limit, so the chunk added a module-level `too-many-lines` disable, as other large modules have. |
| 4. US2: last log line | T010–T014 | 5 / 0 / 0 | `e6bf0f40` | The unused `run_logger` parameter of `_execute_stage` was removed, and its two test callers were updated. |
| 5. US1: run summary artifact | T015–T020 | 6 / 0 / 0 | `6b59a232` | The configuration line is left out for an unregistered run. The docstring cited ADR 0021, which is not on this branch; `b2ef7728` fixed it. |
| 6. US3: Prefect UI link | T021–T029 | 9 / 0 / 0 | `defa1951` | One table row was added to `compose-deployment.mdx` early, because a documentation test requires every Compose variable to be documented. |
| 7. Polish | T030–T035 | 6 / 0 / 0 | `90d2168e` | The packaged copy of the Compose file had to be updated to match the root file. `towncrier create` reinstalls the environment without the Prefect and service extras. |
| Orchestrator fix | — | — | `b2ef7728` | Replaced the ADR 0021 citations. The quickstart now mentions Prefect's own final log line. |
| Review fixes | — | — | `b905422a` | See "Review fixes" below. |

## Tasks not completed

None.

## Local-pass evidence

Every test was run offline in the uv virtual environment, with the dev, Prefect and service extras installed, on
macOS with Python 3.11.8 and Prefect 3.8.6.

| Test | Type | Run command | Passed at (UTC) | Pass line |
| --- | --- | --- | --- | --- |
| `tests/cli/test_client_commands.py::test_a_missing_or_invalid_address_prints_how_to_set_it[url-unset\|url-not-http\|url-with-credentials\|token-unset]`, `::test_a_client_input_error_for_another_argument_prints_no_hint` | unit | `uv run pytest tests/cli/test_client_commands.py -q -p no:cacheprovider -k "how_to_set_it or prints_no_hint"` | 2026-11-05T15:36:11Z | `5 passed, 145 deselected in 0.25s` |
| `tests/service/test_status_observation_budget.py`: 2 new and 3 changed terminal-state tests | unit | `uv run pytest tests/service/test_status_observation_budget.py::<test> -q -p no:cacheprovider` | 2026-11-05T15:39:37Z – 15:39:42Z | `1 passed, 2 warnings` for each |
| `tests/service/test_worker_claim.py::test_expired_worker_claim_refusal_precedes_all_stage_runtime_construction[...]`, `::test_claim_refusal_prevents_registry_and_adapter_construction[...]`, `::test_claim_refusal_message_names_the_run_and_the_recovery_commands`, `::test_claim_refusal_is_logged_at_error_before_the_flow_fails` | unit | `uv run pytest tests/service/test_worker_claim.py -q -p no:cacheprovider -k refus -v` | 2026-11-05T15:44:12Z | `10 passed, 6 deselected in 0.65s` |
| `tests/service/test_run_log_next_step.py`: 15 tests for the last log line | unit | `uv run pytest tests/service/test_run_log_next_step.py -q -p no:cacheprovider -rA` | 2026-11-05T15:49:50Z | `18 passed in 0.66s` |
| `tests/service/test_run_artifact.py`: 12 tests, plus 5 artifact tests in `test_run_log_next_step.py` | unit | `uv run pytest tests/service/test_run_artifact.py tests/service/test_run_log_next_step.py -q -p no:cacheprovider` | 2026-11-05T15:56:04Z | `37 passed in 0.58s` |
| `tests/service/test_prefect_ui.py`, and the Prefect link tests in `test_status_observation_budget.py`, `test_config_routes.py`, `tests/cli/test_client_commands.py` and `tests/dev_stack/test_compose_valid.py` | unit | `uv run pytest <those files> -o addopts="" -v -p no:cacheprovider -k "<link tests>"` | 2026-11-05T16:02:18Z | `22 passed, 251 deselected, 2 warnings in 0.65s` |
| Review-fix tests in `test_run_artifact.py`, `test_status_observation_budget.py`, `test_run_log_next_step.py`, `test_worker_claim.py` and `test_prefect_ui.py` | unit | `uv run pytest <file> -q -p no:cacheprovider` | 2026-11-05T16:18:45Z – 16:21:02Z | `14 passed`, `29 passed`, `43 passed`, `15 passed` |
| Whole service and CLI tiers, after the review fixes | unit | `uv run pytest tests/service tests/cli -q -p no:cacheprovider` | 2026-11-05T16:22:17Z | `1088 passed, 1 skipped, 2 warnings in 31.73s` |
| Offline Compose and development-stack tests | unit | `uv run pytest tests/compose tests/dev_stack -q -p no:cacheprovider -m "not compose and not docker"` | 2026-11-05, after `b905422a` | `428 passed, 52 deselected, 1 xfailed` |
| Live plan on the development stack | end-to-end (manual) | See "Live check" | 2026-11-05T16:07:32Z | Artifact, link, last log line and hint all observed |

## Review findings

| Severity | File | Finding | Status |
| --- | --- | --- | --- |
| High | `docker-compose.yml` and its copies | The comments said the artifact links the flow run. The example address pointed at a UI that is off by default. | Fixed, `b905422a` |
| High | `infrahub_sync/service/run_artifact.py` | The FR-004 warning did not name the run and the stage, and the test fixed the wrong text in place. | Fixed, `b905422a` |
| Medium | `infrahub_sync/service/service.py` | A redacted verdict could differ from the raw `terminal_state`. | Fixed, `b905422a` |
| Medium | tests | No test of failure-path redaction, no flow-level test of FR-004, FR-017 clauses unchecked. | Fixed, `b905422a` |
| Medium | `infrahub_sync/service/flow.py` | `RecordedFailure.outcome` was typed `str`. | Fixed, `b905422a` |
| Medium | `infrahub_sync/service/service.py` | `RunService(prefect_ui_url=...)` does not validate a value passed by a direct caller. Only `build_app` validates. | Deferred |
| Low | `infrahub_sync/service/prefect_ui.py` | An empty `?` or `#`, surrounding whitespace, and a bad host were accepted. | Fixed, `b905422a` |
| Low | `infrahub_sync/service/flow.py` | A stale comment. A failing log line could replace the stage failure. | Fixed, `b905422a` |
| Low | `infrahub_sync/service/flow.py` | Prefect's engine also logs the re-raised `ExecutionClaimRefusedError`, so the guidance appears twice in a refused flow run's log. | Deferred |
| Low | `infrahub_sync/service/flow.py` | The refused-claim guidance says `apply <run_id>`, which is right only when the refused run holds a reviewed plan. | Deferred |
| Advisory | `flow.py`, `prefect_ui.py`, `service.py` | Simplifications: one helper per stage exit, a shared next-action reader, a shared URL check with `client/client.py`, a clearer verdict expression. | Deferred |
| Low | `tests/service/test_run_log_next_step.py` | Stale module docstring. | Fixed in the report commit |

## Autonomous decisions

- The spec and the brief cited ADR 0021, taken from the reference commits. That ADR is not on this branch, so
  `b2ef7728` replaced the citations with the secret-redaction guideline.
- FR-019 required changing the expected values of three existing terminal-state tests. A fourth test expected
  `***`, and the review fixes changed it, because the raw `terminal_state` next to it already showed the value.
- The Prefect UI link was checked with HTTP requests, not in a browser. The page returns 200 for an HTML request
  and 404 for `Accept: */*`.
- On the failure exit, the last log line and the artifact publish are best-effort (`suppress(Exception)`), so the
  stage failure is always the error raised.
- Four reviewers ran instead of six. The code and error-handling reviews ran together, and so did the comment and
  type reviews.

## Suggested next steps

1. Review the branch, then open a pull request into `feature/v3-develop` with `changes/minor` when you choose.
2. Consider the deferred findings:
   - validate the URL inside `RunService`;
   - remove the duplicated refused-claim guidance;
   - word the `apply` hint for a refused run that is not a plan run;
   - the simplifications.
3. Run `speckit-opsmill-extract` after review.

## Baseline

Measured on branch `016-prefect-run-observability` at `8bc54ce9`, at 2026-11-05T15:30:17Z, before any
implementation:

- `uv run invoke tests.tests-unit`: `6422 passed, 7 skipped, 239 deselected, 1 xfailed, 5 warnings in 156.25s`
- `uv run ruff check .`: `All checks passed!`

## Gates

Measured on `defa1951` plus the T030–T033 working-tree changes, from 2026-11-05T16:06:59Z:

- `uv run invoke format`: exit 0. `413 files left unchanged`, `All checks passed!`,
  `All formatters have been executed!`. It changed no file.
- `uv run invoke lint`: exit 0.
  - rumdl: `Success: No issues found in 140 files`.
  - ruff: `413 files already formatted`, `All checks passed!`.
  - Pylint: `Pylint passed (0 diagnostics, score 10.0)`.
  - ty: `Found 18 diagnostics`. The chunk rules give a baseline of 22, so ty reports no new diagnostic.
- `uv run ruff check .`: `All checks passed!`
- `uv run invoke tests.tests-unit`, first run at 2026-11-05T16:10:16Z: exit 1,
  `3 failed, 6515 passed, 4 skipped, 246 deselected, 1 xfailed, 7 warnings in 159.89s`. The three failures
  (`tests/test_release_pin.py::test_the_repository_files_validate_together_at_the_project_version` and two
  tests in `tests/testcontainers/test_packaging.py`) came from `defa1951`: it added
  `INFRAHUB_SYNC_PREFECT_UI_URL` to the root `docker-compose.yml` but not to the packaged copy
  `infrahub_sync/testcontainers/docker-compose.yml`. The root file was copied over the packaged copy.
- `uv run invoke tests.tests-unit`, second run at 2026-11-05T16:13:04Z: exit 0,
  `6518 passed, 4 skipped, 246 deselected, 1 xfailed, 7 warnings in 142.83s (0:02:22)`.

## Live check

Run on the development stack (project `infrahub-sync-dev`) after `uv run invoke dev.build` and
`uv run invoke dev.start`, both exit 0, at 2026-11-05T16:07:32Z. Only `plan` was run; no `sync` or `apply`.

- Plan run ID: `20261105T1607-44f997d8`. It planned 1 operation (`by_action: delete=1`,
  `by_kind: IpamNamespace=1`).
- `infrahub-sync runs show 20261105T1607-44f997d8` printed:

  ```text
  phase: planned
  outcome: planned
  execution_state: completed
  flow_run_id: 5dddef3a-f6d5-46e0-b8a3-f7b0155d70ce
  prefect_flow_run_url: http://127.0.0.1:4230/runs/flow-run/5dddef3a-f6d5-46e0-b8a3-f7b0155d70ce
  ```

- Opening the link: a `GET` with httpx's default `Accept: */*` returned HTTP 404 with the JSON body
  `{"detail":"Not Found"}`. The same `GET` with `Accept: text/html,application/xhtml+xml`, as a browser sends
  for a navigation, returned HTTP 200 with the Prefect UI's `text/html` page. Prefect 3.8.6 serves its
  single-page UI on a deep path only for an HTML navigation request. A 200 shows that the UI page loads at that
  address; it does not prove that the UI's router shows the flow run. No browser was opened.
- `POST http://127.0.0.1:4230/api/artifacts/filter` for the flow run returned one artifact, key
  `sync-run-20261105t1607-44f997d8-plan`, type `markdown`:

  ```markdown
  # Sync run 20261105T1607-44f997d8

  - Stage: plan
  - Outcome: planned
  - Configuration: 20261105T1514-9d8722ad@1
  - Destination branch: netbox-import
  - Operations by action: delete 1
  - Operations by kind: IpamNamespace 1

  ## Next steps

  - `infrahub-sync runs show 20261105T1607-44f997d8`
  - `infrahub-sync runs plan 20261105T1607-44f997d8 --detail`
  ```

- `POST /api/flow_runs/filter`: state `COMPLETED` (`Completed`), name `Plan sync of from-netbox`.
- `POST /api/logs/filter`, 65 lines, sorted by time. The last two:

  ```text
  INFO 2026-11-05T16:08:02.163892Z service run 20261105T1607-44f997d8 stage=plan outcome=planned; next step: `infrahub-sync runs show 20261105T1607-44f997d8`
  INFO 2026-11-05T16:08:02.622602Z Finished in state Completed()
  ```

  The worker's line is its last one. Prefect's own final-state line follows it, so the quickstart's "last line
  in the Logs tab" is the line before last. `docs/docs/orchestration.mdx` says so.
- Hint check:

  ```text
  $ env -u INFRAHUB_SYNC_API_URL uv run infrahub-sync runs list; echo "exit $?"
  error: client-input
  argument: service_url
  hint: set INFRAHUB_SYNC_API_URL or pass --api-url to an absolute http or https URL of the Sync API
  exit 2
  $ INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001 env -u INFRAHUB_SYNC_API_TOKEN uv run infrahub-sync runs list; echo "exit $?"
  error: client-input
  argument: token
  hint: set INFRAHUB_SYNC_API_TOKEN to a Sync API bearer token
  exit 2
  ```

## Review fixes

- The Compose comments said run summaries link each flow run, but the markdown artifact holds no address; the comments now name run responses and `runs show`, say the UI is off until "Turn the Prefect UI on" is applied, and use `http://127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}`. Files: `docker-compose.yml`, `infrahub_sync/testcontainers/docker-compose.yml` (byte-identical), `development/docker-compose.yml`. Test: `tests/compose`, `tests/dev_stack` (copy and Compose checks stay green).
- FR-004: the publish-failure warning now names the run and the stage, without the error text: `run summary artifact for run <run_id> stage <stage> could not be published (<ErrorType>)`. File: `infrahub_sync/service/run_artifact.py`; `docs/docs/orchestration.mdx` and `contracts/prefect-surfaces.md` quote the new text. Test: `tests/service/test_run_artifact.py::test_a_failed_publish_logs_one_warning_without_the_error_text`.
- A secret that is a substring of a terminal state name made `state` differ from `terminal_state`; the verdict is a fixed enum the response already returns raw, so `state` now repeats it raw. Live and retained observation text is still redacted. File: `infrahub_sync/service/service.py`. Test: `tests/service/test_status_observation_budget.py::test_a_secret_inside_a_terminal_state_name_leaves_state_equal_to_terminal_state`. `test_a_secret_that_spells_a_terminal_state_still_reaches_its_cancellation_verdict` now expects `state` `cancelled` instead of `***`, because `terminal_state` next to it already carried `cancelled` raw, so the `***` hid nothing.
- The failure exit's last line is redacted before the stage's secrets are cleared. File: none changed. Test: `tests/service/test_run_log_next_step.py::test_the_recorded_failure_line_is_redacted_before_the_secrets_are_cleared`.
- FR-004 at flow level: a publish that raises leaves the success result and the sanitized stage failure unchanged. File: none changed. Tests: `tests/service/test_run_log_next_step.py::test_a_failed_publish_leaves_the_success_result_unchanged`, `::test_a_failed_publish_leaves_the_stage_failure_unchanged`.
- FR-017: the refused-claim message names a Prefect Retry, an expired admission and that a run is not replayed. File: none changed. Test: `tests/service/test_worker_claim.py::test_claim_refusal_message_names_the_run_and_the_recovery_commands`.
- The outside-Prefect test counted on the flow swallowing an `AssertionError`; it now records calls and asserts none. Test: `tests/service/test_run_log_next_step.py::test_outside_a_prefect_run_nothing_is_published`.
- `RecordedFailure.outcome` is narrowed to `Literal["failed", "ambiguous"]`. File: `infrahub_sync/service/flow.py`. Test: `uv run ty check .` (19 diagnostics, baseline 22).
- `INFRAHUB_SYNC_PREFECT_UI_URL`: surrounding whitespace is stripped; a value holding `?` or `#` is refused, because httpx drops an empty query or fragment; a host httpx percent-encodes (for example `http://[bad`) is refused; only `httpx.InvalidURL` is caught. File: `infrahub_sync/service/prefect_ui.py`. Test: `tests/service/test_prefect_ui.py` (cases ` http://h `, `http://h/?`, `http://h/#`, `http://[bad`, `http://h:x`).
- The failure-exit comment said the line never announces an outcome the store does not hold; it now says an unrecorded failure reports `failed` while the store keeps the run nonterminal, as in plan.md "Stage exits". File: `infrahub_sync/service/flow.py`. Test: none (comment).
- If the last line or the publish raised on the failure exit, that exception replaced the stage failure and skipped `sanitize_exception_chain`; both are now best-effort, and `secrets.clear()` runs in `finally`. File: `infrahub_sync/service/flow.py`. Test: `tests/service/test_run_log_next_step.py::test_a_failing_last_line_still_raises_the_original_sanitized_failure`.
