# Tasks: Prefect run observability and the missing Sync API address

**Input**: Design documents from `dev/specs/016-prefect-run-observability/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Required. The feature is delivered test-first. In each story, the test tasks come first and must
fail before the implementation task starts. Run a single test file with
`uv run pytest <file> -q -p no:cacheprovider`. Run the full offline tier with `uv run invoke tests.tests-unit`.

**Order**: The phases follow the plan's test-first order (plan.md, "Test-first order"), not the spec's story
numbers. US2 comes before US1, because US1 uses the `RecordedFailure` value that US2 introduces.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task).
- **[Story]**: the user story in spec.md: US1 artifact, US2 last log line, US3 Prefect link, US4 refused claim,
  US5 terminal state, US6 CLI hint.

## Phase 1: Setup

- [X] T001 Confirm the baseline: on branch `016-prefect-run-observability`, run `uv run invoke tests.tests-unit` and `uv run ruff check .`, and record the pass count in `dev/specs/016-prefect-run-observability/opsmill-implement-report.md` (create the file with a "Baseline" heading).

## Phase 2: Foundational

No shared foundation. Each story touches its own functions. US1 depends on US2 (see Dependencies).

---

## Phase 3: User Story 6 - Fix a missing Sync API address from the error (Priority: P1)

**Goal**: A `client-input` error for `service_url` or `token` prints a `hint:` line that names the setting
(FR-020–FR-022, [contracts/cli-output.md](./contracts/cli-output.md)).

**Independent test**: `env -u INFRAHUB_SYNC_API_URL uv run infrahub-sync runs list` prints `hint:` naming
`INFRAHUB_SYNC_API_URL` and `--api-url`, with the same exit code as before.

### Tests for User Story 6 (write first, must fail)

- [X] T002 [P] [US6] In `tests/cli/test_client_commands.py`, add parametrized tests that run the Typer app through `CliRunner` with `INFRAHUB_SYNC_API_URL` unset, then set to `ftp://x`, then set to `http://user:pw@h` (each case expects `argument: service_url` followed by `hint: set INFRAHUB_SYNC_API_URL or pass --api-url to an absolute http or https URL of the Sync API`), and with the URL set and `INFRAHUB_SYNC_API_TOKEN` unset (expects `argument: token` followed by `hint: set INFRAHUB_SYNC_API_TOKEN to a Sync API bearer token`). Assert that the `error` and `argument` lines come first, in that order, and that the exit code equals the one the command returns today for `client-input`. Read that exit code from the existing code, do not hard-code a new one.
- [X] T003 [P] [US6] In `tests/cli/test_client_commands.py`, add a test that a `client-input` error for another argument, such as `limit` from `runs list --limit 0` or the closest existing path that raises `ClientInputError` for a non-address argument, prints no `hint:` line.

### Implementation for User Story 6

- [X] T004 [US6] In `infrahub_sync/cli.py` `_error_fields` (around line 202), add a module-level fixed mapping `_CLIENT_INPUT_HINTS = {"service_url": ..., "token": ...}` with the texts from the contract, and append `("hint", text)` after `("argument", ...)` only when `error.argument` is a key. Do not change the exit code or other fields. Make T002 and T003 pass.

**Checkpoint**: US6 is complete and testable on its own.

---

## Phase 4: User Story 5 - See a finished execution's verdict as its state (Priority: P3)

**Goal**: An orchestration entry with a recorded terminal state reports that state (FR-019).

**Independent test**: a run whose execution link has `terminal_state="completed"` and a live observation of
`Running` reports `state == "completed"`.

### Tests for User Story 5 (write first, must fail)

- [ ] T005 [P] [US5] In `tests/service/test_status_observation_budget.py`, or in the test module that builds run summaries with live observations (find it with `grep -rn "live-detail-not-requested\|detail_available" tests/service`), add two tests. The first: an execution link with `terminal_state="completed"` and an available live observation of `Running` gives `state == "completed"`. The second: a link with no terminal state and an available live observation of `Running` gives `state == "Running"`, which pins today's rule.

### Implementation for User Story 5

- [ ] T006 [US5] In `infrahub_sync/service/service.py`, in the loop that builds `OrchestrationSummary` (around lines 1005-1050), set `state` to `link.terminal_state` when it is not `None`, in both the observed and the unobserved branches. Otherwise keep the current rule. Redact as the retained value is redacted today. Make T005 pass without changing other expected values.

**Checkpoint**: US5 is complete.

---

## Phase 5: User Story 4 - Understand a refused Prefect retry (Priority: P2)

**Goal**: A refused claim raises `ExecutionClaimRefusedError` with fixed guidance and logs it at ERROR
(FR-016–FR-018, [contracts/prefect-surfaces.md](./contracts/prefect-surfaces.md)).

**Independent test**: a refused claim raises `ExecutionClaimRefusedError`. Its message starts with
`service worker execution claim was refused`, names the four commands and does not contain `diff`. The run
logger records it at ERROR.

### Tests for User Story 4 (write first, must fail)

- [ ] T007 [P] [US4] In `tests/service/test_worker_claim.py`, tighten the two existing refusal tests (around lines 194 and 221) to `pytest.raises(ExecutionClaimRefusedError, match="service worker execution claim was refused")`. Add a test of the message text: it contains the run ID, `Nothing was read or written`, `infrahub-sync runs show <run_id>`, `infrahub-sync plan`, `infrahub-sync sync` and `infrahub-sync apply <run_id> --expected-checksum <checksum>`, and does not contain `infrahub-sync diff`. Assert that `issubclass(ExecutionClaimRefusedError, RuntimeError)`.
- [ ] T008 [P] [US4] In `tests/service/test_worker_claim.py`, add a test that runs `service_sync_run.fn(...)` (the undecorated flow function, as the existing flow tests do), with `_claim_current_execution` patched to raise `ExecutionClaimRefusedError("<run_id>")` and `_run_logger` patched to return a recording logger. Assert that the logger recorded one ERROR entry holding the message, and that the error is raised again.

### Implementation for User Story 4

- [ ] T009 [US4] In `infrahub_sync/service/flow.py`, add the fixed guidance template next to `_WORKER_EXECUTION_REFUSED`, with the text from the contract and the `{run_id}` placeholder only. Add `class ExecutionClaimRefusedError(RuntimeError)` with `__init__(self, run_id: str)`, which builds `f"{_WORKER_EXECUTION_REFUSED}: {guidance}"`. Raise it from `_claim_current_execution` (line 422). In `service_sync_run`, wrap the claim call so that `ExecutionClaimRefusedError` is logged with `run_logger.log(logging.ERROR, "%s", refused)` and raised again. Make T007 and T008 pass.

**Checkpoint**: US4 is complete.

---

## Phase 6: User Story 2 - Read the next step in the flow run log (Priority: P1)

**Goal**: Every stage exit ends with `service run <id> stage=<stage> outcome=<outcome>; next step: <action>`
(FR-007–FR-009, FR-008a; plan.md "Stage exits").

**Independent test**: the success, recorded-failure and unrecorded-failure exits each end with the line, with
the outcome and action from the plan's table.

### Tests for User Story 2 (write first, must fail)

- [ ] T010 [P] [US2] Create `tests/service/test_run_log_next_step.py` with unit tests for a pure helper `final_log_line(run_id, stage, outcome, next_action)` in `infrahub_sync/service/flow.py`. With `next_action=None` it returns `` service run R stage=plan outcome=planned; next step: `infrahub-sync runs show R` ``. With a next action it returns that action as given. Also test `result_next_action(result)`: it returns `result["failure"]["next_action"]` when that is a non-empty string, and `None` for a missing failure, a non-dict failure or an empty string.
- [ ] T011 [P] [US2] In `tests/service/test_run_log_next_step.py`, add tests that `_record_failure` returns `RecordedFailure(outcome="failed", next_action=<evidence next_action>)` when it stores a failure. Build the projection and the error the way the existing `_record_failure` tests do (find them with `grep -rn "_record_failure" tests`). It returns `RecordedFailure(outcome="ambiguous", ...)` for an ambiguous verdict, and `None` when `lookup_run` returns no run or the commit raises.
- [ ] T012 [P] [US2] In `tests/service/test_run_log_next_step.py`, add flow-level tests that call `service_sync_run.fn(...)` with `_claim_current_execution`, `_runtime`, `_execute_stage` and `_commit_success` patched, and with a recording `run_logger`. Cover the three exits from plan.md:
  - success: the last INFO line ends with `` next step: `infrahub-sync runs show <id>` ``;
  - success whose result holds `{"failure": {"next_action": "Re-run `plan`."}}`: the line ends with that action;
  - recorded failure: `_execute_stage` raises, and `_record_failure` is patched to return `RecordedFailure("failed", "Do X.")`;
  - unrecorded failure: `_record_failure` returns `None`. The line reads `outcome=failed` and the next step is `runs show`.

  Also assert that the line is no longer logged before `_commit_success` is called: the order of the recorded calls.

### Implementation for User Story 2

- [ ] T013 [US2] In `infrahub_sync/service/flow.py`, add `@dataclass(frozen=True) class RecordedFailure: outcome: str; next_action: str | None`, `final_log_line(...)` and `result_next_action(...)`. Make `_record_failure` return `RecordedFailure(terminal_outcome, evidence.get("next_action") if isinstance(..., str) and ... else None)` after a successful commit, and `None` on every early return and in the persistence-error branch. Make T010 and T011 pass.
- [ ] T014 [US2] In `infrahub_sync/service/flow.py`, remove the final `run_logger.info(...)` at the end of `_execute_stage` (line 714). In `service_sync_run`, log `redact(final_log_line(...), secrets)` with `run_logger.info` at the three exits from plan.md: after `_commit_success` on success, and on failure after `_record_failure` (or in its place when `record_terminal` is false) and before `secrets.clear()`. Make T012 pass, and keep every other test in `tests/service/` green.

**Checkpoint**: US2 is complete. US1 can start.

---

## Phase 7: User Story 1 - Read a run's result in the Prefect UI (Priority: P1)

**Goal**: One markdown artifact `sync-run-<run_id>-<stage>` per stage exit inside Prefect
(FR-001–FR-006, [data-model.md](./data-model.md), [contracts/prefect-surfaces.md](./contracts/prefect-surfaces.md)).

**Independent test**: a recorded `create` callable receives the key, description and markdown described in the
contract. A raising `create` leaves the outcome unchanged and logs one WARNING.

### Tests for User Story 1 (write first, must fail)

- [ ] T015 [P] [US1] Create `tests/service/test_run_artifact.py` with tests for `artifact_key(run_id, stage)` in `infrahub_sync/service/run_artifact.py`. `artifact_key("20261105T1514-f62dfb83", "plan") == "sync-run-20261105t1514-f62dfb83-plan"`. Characters other than `a-z0-9-` become `-`, repeated dashes collapse, and leading or trailing dashes are stripped.
- [ ] T016 [P] [US1] In `tests/service/test_run_artifact.py`, add tests for `run_markdown(run, stage, outcome, next_action)` with `ProductRun` objects built from `infrahub_sync.product_store.models`:
  - a plan run with `summary={"by_action": {"create": 2, "delete": 1}, "by_kind": {"B": 1, "A": 2}}`, `config_id`, `registry_version=1` and `destination_branch="b1"` shows `- Configuration: <config_id>@1`, `- Destination branch: b1`, `- Operations by action: create 2, delete 1` and `- Operations by kind: A 2, B 1` (sorted);
  - an apply run with per-action counts (`ACTION_KEYS`) shows them;
  - a run with no counts shows no counts line;
  - a run with a saved plan review artifact reference (`kind == "saved-plan-review"`) lists `` `infrahub-sync runs plan <id> --detail` `` after `` `infrahub-sync runs show <id>` ``;
  - a `next_action` is the last next step;
  - a failure `cause` in `run.results` never appears in the markdown.
- [ ] T017 [P] [US1] In `tests/service/test_run_artifact.py`, add tests for `publish_run_artifact(projection, run_id, stage, *, outcome, next_action, secrets, run_logger, create)`. It calls `create(markdown=..., key=..., description=f"Sync run {run_id}, stage {stage}")` once. A secret value placed in `summary` (for example in `by_kind` as a kind name) is redacted in the markdown. When `create` raises `RuntimeError("boom")`, the function returns normally and logs exactly one WARNING, `run summary artifact could not be published (RuntimeError)`, without `boom`. When `lookup_run` returns `None`, it logs one WARNING and calls nothing.
- [ ] T018 [P] [US1] In `tests/service/test_run_log_next_step.py` (the flow-level harness from T012), add tests:
  - With `_run_logger` returning `prefect_context=True` and `create_markdown_artifact` patched in `infrahub_sync.service.flow`, each of the three exits calls the publisher once, after the last log line and before `secrets.clear()`. Check this with a secret value that must be redacted.
  - With `prefect_context=False`, nothing is published.
  - A refused claim publishes nothing.

### Implementation for User Story 1

- [ ] T019 [US1] Create `infrahub_sync/service/run_artifact.py`, using `git show 43986fa9:infrahub_sync/service/run_artifact.py` as reference only. The module provides `artifact_key`, `run_markdown` and `publish_run_artifact` per data-model.md and research R4. It lists only commands that exist on this branch (`runs show`, `runs plan --detail`), reads `config_id`, `registry_version` and `destination_branch` from `ProductRun`, sorts kinds, never reads `cause`, and passes the markdown through `infrahub_sync`'s `redact` with the given secrets. Write a module docstring that cites ADR 0021. Make T015–T017 pass.
- [ ] T020 [US1] In `infrahub_sync/service/flow.py`, import `create_markdown_artifact` from `prefect.artifacts` and add `_publish_run_summary(projection, run_id, stage, outcome, next_action, secrets, run_logger, *, prefect_context)`. It returns at once when `prefect_context` is false. Call it at each of the three exits, right after the last log line (T014) and before `secrets.clear()`. Make T018 pass.

**Checkpoint**: US1 and US2 are complete.

---

## Phase 8: User Story 3 - Open a run's Prefect flow run from the CLI (Priority: P2)

**Goal**: `prefect_flow_run_url` in run responses and in `runs show` when `INFRAHUB_SYNC_PREFECT_UI_URL` is set
(FR-010–FR-015, [contracts/run-response.md](./contracts/run-response.md)).

**Independent test**: with the setting set, a run response entry holds
`<base>/runs/flow-run/<flow_run_id>`, and `runs show` prints it. With the setting unset, the field is `null` and
`runs show` prints `<none>`.

### Tests for User Story 3 (write first, must fail)

- [ ] T021 [P] [US3] Create `tests/service/test_prefect_ui.py`:
  - `prefect_ui_url_from_environment(env)` returns `None` for a missing or empty value, and the value without its trailing `/` for `http://127.0.0.1:4230/` and for `https://example.com/prefect`.
  - It raises `ValueError` whose message names `INFRAHUB_SYNC_PREFECT_UI_URL` and does not contain the value, for `ftp://h`, `127.0.0.1:4230`, `http://u:p@h`, `http://h/?q=1` and `http://h/#f`.
  - `flow_run_url("http://h/prefect", "abc")` returns `"http://h/prefect/runs/flow-run/abc"`.
- [ ] T022 [P] [US3] In the service test module that checks run summaries (same module as T005), add a test. `RunService(..., prefect_ui_url="http://127.0.0.1:4230")` returns `prefect_flow_run_url` for every orchestration entry, in both the observed and the unobserved branches. Without the argument the field is `None`.
- [ ] T023 [P] [US3] In `tests/service/` (the module that tests `build_app` in `infrahub_sync/service/serve.py`; find it with `grep -rln "build_app" tests/service`), add a test that `build_app` passes the validated setting to `run_service_factory` as `prefect_ui_url`, and a test that an invalid value raises `ValueError` from `build_app`.
- [ ] T024 [P] [US3] In `tests/cli/test_client_commands.py`, add tests that `runs show` prints `prefect_flow_run_url: <url>` right after `flow_run_id:` when the response holds the field, and `prefect_flow_run_url: <none>` when it is absent or `null`.
- [ ] T025 [P] [US3] In `tests/dev_stack/test_compose_valid.py`, or in the Compose contract tests under `tests/compose/` that read the root file, add assertions:
  - `development/docker-compose.yml` sets `INFRAHUB_SYNC_PREFECT_UI_URL: "http://127.0.0.1:${PREFECT_PORT:-4230}"` on `sync-api`;
  - the root `docker-compose.yml` passes `INFRAHUB_SYNC_PREFECT_UI_URL: "${INFRAHUB_SYNC_PREFECT_UI_URL:-}"` to `sync-api`.

### Implementation for User Story 3

- [ ] T026 [US3] Create `infrahub_sync/service/prefect_ui.py` with `PREFECT_UI_URL_ENV = "INFRAHUB_SYNC_PREFECT_UI_URL"`, `prefect_ui_url_from_environment(environ: Mapping[str, str] = os.environ) -> str | None` and `flow_run_url(base: str, flow_run_id: str) -> str`. Validate with `httpx.URL`, as `client/client.py:131-142` does. Make T021 pass.
- [ ] T027 [US3] In `infrahub_sync/client/models.py`, add `prefect_flow_run_url: str | None = None` to `OrchestrationSummary`. In `infrahub_sync/service/service.py`, add the keyword argument `prefect_ui_url: str | None = None` to `RunService.__init__`, and set the field in both `OrchestrationSummary(...)` constructions. In `infrahub_sync/service/serve.py` `build_app`, read `prefect_ui_url_from_environment()` and pass it to `run_service_factory`. Make T022 and T023 pass.
- [ ] T028 [US3] In `infrahub_sync/cli.py`, around line 620, add `("prefect_flow_run_url", selected.prefect_flow_run_url if selected is not None else None)` after `flow_run_id`. Make T024 pass.
- [ ] T029 [US3] In `development/docker-compose.yml`, add `INFRAHUB_SYNC_PREFECT_UI_URL: "http://127.0.0.1:${PREFECT_PORT:-4230}"` to the `sync-api` environment. In the root `docker-compose.yml`, add `INFRAHUB_SYNC_PREFECT_UI_URL: "${INFRAHUB_SYNC_PREFECT_UI_URL:-}"` to `sync-api`, with a comment that gives an example value and says that an empty value turns the link off. Make T025 pass, and run `uv run invoke linter.lint-yaml`.

**Checkpoint**: All six stories are complete.

---

## Phase 9: Polish & cross-cutting

- [ ] T030 [P] Update `docs/docs/orchestration.mdx`: the run summary artifact (key, content, the case where publishing fails), the last log line, and the refused claim with its message. Update `docs/docs/running-a-sync.mdx` only where it describes what the Prefect UI shows.
- [ ] T031 [P] Update `docs/docs/compose-deployment.mdx` with `INFRAHUB_SYNC_PREFECT_UI_URL`: what it does, valid values, and the refusal at start-up. Add the start-up refusal to `docs/docs/operations/compose-troubleshooting.mdx`.
- [ ] T032 [P] Update `docs/docs/reference/sync-http-api.mdx`: `prefect_flow_run_url` on orchestration entries, and the rule that a recorded terminal state is the entry's state. Update `docs/docs/reference/cli.mdx`: the `hint` field on `client-input` errors, and `prefect_flow_run_url` in `runs show`. Regenerate generated CLI docs with `uv run invoke docs.generate` if the help text changed.
- [ ] T033 [P] Add news fragments with `uv run --extra dev towncrier create -c "<text>" +<slug>.<type>.md`:
  - `+prefect-run-summary-artifact.added.md`;
  - `+worker-log-next-step.added.md`;
  - `+prefect-flow-run-url.added.md`;
  - `+refused-claim-guidance.changed.md`;
  - `+terminal-state-as-state.fixed.md`;
  - `+cli-api-address-hint.changed.md`.
- [ ] T034 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .` and `uv run invoke tests.tests-unit`. Fix every finding, then record the results in `opsmill-implement-report.md`.
- [ ] T035 Validate on the running development stack per [quickstart.md](./quickstart.md), steps 1–4. Record these in `opsmill-implement-report.md`:
  - the plan's run ID;
  - the `prefect_flow_run_url` printed by `runs show`, and whether the Prefect UI opens it;
  - the artifact key and its markdown, from `POST /api/artifacts/filter`;
  - the flow run's last log line;
  - the hint output.

## Dependencies & Execution Order

- Setup (T001) comes before everything.
- US6 (T002–T004), US5 (T005–T006) and US4 (T007–T009) are independent of each other and of the rest.
- US2 (T010–T014) comes before US1 (T015–T020): T020 uses `RecordedFailure` and the exits from T014.
- US3 (T021–T029) is independent of US1 and US2.
- Polish (T030–T035) comes after all stories. T035 needs a rebuilt image.
- Within a story, the test tasks come before the implementation tasks. `flow.py` is edited by T009, T013, T014
  and T020, so those tasks run one after another.

## Parallel Execution Examples

- Tests: T002, T005, T007, T010, T015 and T021 are in different files and can be written in parallel.
- US3: T021–T025 in parallel, then T026 → T027 → T028, and T029 in parallel with T028.
- Polish: T030–T033 in parallel.

## Implementation Strategy

- MVP: US6 and US2, the two P1 stories that need no new module. US1, the third P1 story, follows directly.
- Then US4, US3 and US5.
- Commit each story separately, after its checkpoint, with explicit paths.
