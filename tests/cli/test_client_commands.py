"""Typer coverage for the SyncClient-backed command surface."""

from __future__ import annotations

import json
import logging
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from infrahub_sync.cli import _display, app  # noqa: PLC2701 - the renderer under test is private.
from infrahub_sync.client import (
    APIError,
    ApplyRunRequest,
    ClientInputError,
    CompatibilityError,
    ConfigMutationRequest,
    ConfigsAPIError,
    ConfigurationSummaryResource,
    ConfigurationVersionResource,
    CreateRunRequest,
    OrchestrationSummary,
    PlanOperationResource,
    PlanResource,
    PlanSummaryResource,
    ProtocolError,
    PublicExecutionLink,
    PublicRunResource,
    RegisteredConfigurationResource,
    RegisteredVersionResource,
    ResultsResource,
    RunResource,
    RunTerminalError,
    RunWaitTimeoutError,
    SyncClient,
    TransportError,
    ValidationFindingResource,
    ValidationReportResource,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from infrahub_sync.client.models import TerminalOutcome, TerminalState

NOW = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
CHECKSUM = "a" * 64
RUNNER = CliRunner()


def _version(version: int = 1) -> ConfigurationVersionResource:
    return ConfigurationVersionResource(
        config_id="edge-sync",
        registry_version=version,
        package_checksum=CHECKSUM,
        declared_content={"name": "edge-sync"},
        created_at=NOW,
    )


def _run(
    *,
    operation: Literal["plan", "sync", "apply"] = "plan",
    phase: str = "accepted",
    reconciliation_required: bool = False,
    outcome: str | None = None,
    terminal: tuple[TerminalState, TerminalOutcome] | None = None,
) -> RunResource:
    # The three verdict fields are all-or-nothing, so they are set from one optional pair.
    terminal_state, terminal_outcome = terminal if terminal is not None else (None, None)
    execution = OrchestrationSummary(
        flow_run_id="flow-service-1",
        purpose=operation,
        attempt=1,
        state="pending",
        detail_available=True,
        submitted_at=NOW,
        claimed_at=None,
        stalled_at=None,
        cancellation_requested_at=None,
        cancellation_recovery_deadline_at=None,
        cancellation_acknowledged_at=None,
        terminal_at=NOW if terminal is not None else None,
        terminal_state=terminal_state,
        terminal_outcome=terminal_outcome,
    )
    return RunResource(
        run=PublicRunResource(
            run_id="service-run-1",
            operation=operation,
            configuration_reference="edge-sync@1",
            config_id="edge-sync",
            registry_version=1,
            package_checksum=CHECKSUM,
            actor="operator",
            started_at=NOW,
            phase=phase,
            outcome=outcome,
            prefect_executions=(PublicExecutionLink(flow_run_id="flow-service-1", purpose=operation, attempt=1),),
            reconciliation_required=reconciliation_required,
        ),
        orchestration=(execution,),
    )


def _plan() -> PlanResource:
    return PlanResource(
        run_id="service-run-1",
        checksum=CHECKSUM,
        checksum_ok=True,
        verification_notes=("reviewed from the service artifact",),
        destination_branch="review",
        summary=PlanSummaryResource(
            by_action={"create": 1, "delete": 1},
            by_kind={"Device": 1, "Site": 1},
            total=2,
            delete_operations_computed=True,
            deletes_not_executed=1,
        ),
        operations=(
            PlanOperationResource(
                operation_id="op-create",
                action="create",
                kind="Device",
                identity={"name": "edge-01"},
                tier=0,
                payload={"name": "edge-01"},
                relationships=(),
            ),
            PlanOperationResource(
                operation_id="op-delete",
                action="delete",
                kind="Site",
                identity={"name": "retired"},
                tier=0,
                payload=None,
                relationships=None,
            ),
        ),
    )


@pytest.fixture
def client() -> MagicMock:
    injected = MagicMock(spec=SyncClient)
    injected.list_configs.return_value = (ConfigurationSummaryResource(config_id="edge-sync", created_at=NOW),)
    injected.get_config.return_value = ConfigurationSummaryResource(config_id="edge-sync", created_at=NOW)
    injected.list_config_versions.return_value = (_version(),)
    injected.get_config_version.return_value = _version()
    injected.register_config.return_value = RegisteredConfigurationResource(
        configuration=ConfigurationSummaryResource(config_id="edge-sync", created_at=NOW),
        version=_version(),
    )
    injected.create_config_version.return_value = RegisteredVersionResource(version=_version(2), created=True)
    injected.validate_config.return_value = ValidationReportResource(
        config_id="edge-sync",
        registry_version=1,
        package_checksum=CHECKSUM,
        destination_schema_fingerprint="schema-1",
        findings=(
            ValidationFindingResource(code="first", severity="warning", location="/a", message="first finding"),
            ValidationFindingResource(code="second", severity="error", location="/b", message="second finding"),
        ),
        offset=2,
        limit=3,
        total_findings=7,
        next_offset=5,
    )
    injected.plan.return_value = _run()
    injected.sync.return_value = _run(operation="sync")
    injected.apply.return_value = _run(operation="apply")
    injected.wait_for_run.side_effect = lambda accepted, **_kwargs: accepted
    injected.get_plan.return_value = _plan()
    injected.get_run.return_value = _run()
    return injected


@pytest.fixture(autouse=True)
def _restore_package_logging() -> Iterator[None]:
    """Undo the logger level and handler that `main` installs on every CLI invocation."""
    package_logger = logging.getLogger("infrahub_sync")
    level = package_logger.level
    handlers = list(package_logger.handlers)
    yield
    package_logger.setLevel(level)
    for handler in list(package_logger.handlers):
        if handler not in handlers:
            package_logger.removeHandler(handler)
            handler.close()


def _invoke(client: MagicMock, *args: str):  # type: ignore[no-untyped-def]
    return RUNNER.invoke(app, list(args), obj={"client": client})


def test_configs_register_reads_one_package_and_preserves_identity(client: MagicMock, tmp_path: Path) -> None:
    package = tmp_path / "package.yaml"
    package.write_text("name: edge-sync\nsource:\n  name: netbox\n", encoding="utf-8")

    result = _invoke(
        client,
        "configs",
        "register",
        str(package),
        "--reason",
        "initial registration",
        "--idempotency-key",
        "retry-register",
    )

    assert result.exit_code == 0, result.output
    request, key = client.register_config.call_args.args
    assert request == ConfigMutationRequest(
        package={"name": "edge-sync", "source": {"name": "netbox"}},
        reason="initial registration",
    )
    assert key == "retry-register"
    assert "config_id: edge-sync" in result.output
    assert "registry_version: 1" in result.output
    assert f"package_checksum: {CHECKSUM}" in result.output
    assert "idempotency_key: retry-register" in result.output


def test_configs_version_generates_and_discloses_retry_key(client: MagicMock, tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    package.write_text('{"name": "edge-sync"}', encoding="utf-8")

    result = _invoke(client, "configs", "version", "edge-sync", str(package), "--reason", "new version")

    assert result.exit_code == 0, result.output
    config_id, request, key = client.create_config_version.call_args.args
    assert config_id == "edge-sync"
    assert request.package == {"name": "edge-sync"}
    assert re.fullmatch(r"[0-9a-f]{32}", key)
    assert f"idempotency_key: {key}" in result.output
    assert "created: true" in result.output


def test_configuration_reads_and_validation_keep_machine_fields_and_finding_order(client: MagicMock) -> None:
    listed = _invoke(client, "configs", "list")
    summary = _invoke(client, "configs", "show", "edge-sync")
    shown = _invoke(client, "configs", "show", "edge-sync", "--version", "1")
    versions = _invoke(client, "configs", "versions", "edge-sync")
    validated = _invoke(client, "configs", "validate", "edge-sync", "1", "--offset", "2", "--limit", "3")

    assert all(result.exit_code == 0 for result in (listed, summary, shown, versions, validated))
    client.list_configs.assert_called_once_with()
    client.get_config.assert_called_once_with("edge-sync")
    client.get_config_version.assert_called_once_with("edge-sync", 1)
    client.list_config_versions.assert_called_once_with("edge-sync")
    client.validate_config.assert_called_once_with("edge-sync", 1, offset=2, limit=3)
    assert f"package_checksum: {CHECKSUM}" in shown.output
    assert "offset: 2" in validated.output
    assert "limit: 3" in validated.output
    assert "total_findings: 7" in validated.output
    assert "next_offset: 5" in validated.output
    assert validated.output.index("first finding") < validated.output.index("second finding")


def test_diff_uses_registered_tuple_waits_and_renders_service_plan(client: MagicMock) -> None:
    result = _invoke(
        client,
        "diff",
        "--config-id",
        "edge-sync",
        "--version",
        "1",
        "--branch",
        "review",
        "--reason",
        "inspect changes",
        "--idempotency-key",
        "retry-plan",
        "--wait-timeout",
        "20",
        "--poll-interval",
        "0.5",
    )

    assert result.exit_code == 0, result.output
    request, key = client.plan.call_args.args
    assert request == CreateRunRequest(
        operation="plan",
        config_id="edge-sync",
        registry_version=1,
        branch="review",
        confirm_writes=False,
        reason="inspect changes",
    )
    assert key == "retry-plan"
    client.wait_for_run.assert_called_once()
    wait_call = client.wait_for_run.call_args
    assert wait_call.args == (client.plan.return_value,)
    assert wait_call.kwargs["timeout"] == pytest.approx(20.0)
    assert wait_call.kwargs["poll_interval"] == pytest.approx(0.5)
    assert callable(wait_call.kwargs["on_observation"])
    client.get_plan.assert_called_once_with("service-run-1")
    assert "run_id: service-run-1" in result.output
    assert f"plan_checksum: {CHECKSUM}" in result.output
    assert "destination_branch: review" in result.output
    assert "operations: 2" in result.output
    assert "delete operation(s)" in result.output


def test_sync_no_wait_returns_service_identity_without_polling(client: MagicMock) -> None:
    result = _invoke(
        client,
        "sync",
        "--config-id",
        "edge-sync",
        "--version",
        "1",
        "--reason",
        "apply registered sync",
        "--idempotency-key",
        "retry-sync",
        "--no-wait",
    )

    assert result.exit_code == 0, result.output
    request, key = client.sync.call_args.args
    assert request == CreateRunRequest(
        operation="sync",
        config_id="edge-sync",
        registry_version=1,
        branch=None,
        confirm_writes=True,
        reason="apply registered sync",
    )
    assert key == "retry-sync"
    client.wait_for_run.assert_not_called()
    assert "run_id: service-run-1" in result.output
    assert "idempotency_key: retry-sync" in result.output


@pytest.mark.parametrize(
    "command",
    [
        ("sync", "--config-id", "edge-sync", "--version", "1", "--reason", "sync inventory"),
        ("apply", "service-run-1", "--expected-checksum", CHECKSUM, "--reason", "apply plan"),
    ],
)
def test_completed_writes_report_the_saved_destination_branch(client: MagicMock, command: tuple[str, ...]) -> None:
    result = _invoke(client, *command)

    assert result.exit_code == 0, result.output
    assert "destination_branch: review" in result.output
    client.get_plan.assert_called_once_with("service-run-1")


@pytest.mark.parametrize("operation", ["sync", "apply"])
@pytest.mark.parametrize(
    "error",
    [APIError(503, "plan-unavailable"), TransportError("get_plan"), ProtocolError("get_plan", 200)],
)
def test_completed_writes_stay_successful_when_the_plan_cannot_be_read(
    client: MagicMock, operation: Literal["sync", "apply"], error: Exception
) -> None:
    client.wait_for_run.return_value = _run(operation=operation, terminal=("completed", "succeeded"))
    client.wait_for_run.side_effect = None
    client.get_plan.side_effect = error
    arguments = (
        ("--config-id", "edge-sync", "--version", "1")
        if operation == "sync"
        else ("service-run-1", "--expected-checksum", CHECKSUM)
    )

    result = _invoke(client, operation, *arguments, "--reason", "apply inventory")

    assert result.exit_code == 0, result.output
    assert "execution_state: completed" in result.output
    assert "destination_branch: <unavailable>" in result.output
    client.get_plan.assert_called_once_with("service-run-1")


def test_an_unregistered_plan_reports_no_destination_branch(client: MagicMock) -> None:
    client.get_plan.return_value = _plan().model_copy(update={"destination_branch": None})

    result = _invoke(client, "runs", "plan", "service-run-1")

    assert result.exit_code == 0, result.output
    assert "destination_branch: <none>" in result.output


def test_apply_sends_only_reviewed_checksum_and_shipped_fields(client: MagicMock) -> None:
    result = _invoke(
        client,
        "apply",
        "service-run-1",
        "--expected-checksum",
        CHECKSUM,
        "--branch",
        "review",
        "--reason",
        "apply reviewed plan",
        "--idempotency-key",
        "retry-apply",
        "--no-wait",
    )

    assert result.exit_code == 0, result.output
    run_id, request, key = client.apply.call_args.args
    assert run_id == "service-run-1"
    assert request == ApplyRunRequest(
        expected_checksum=CHECKSUM,
        confirm_writes=True,
        branch="review",
        reason="apply reviewed plan",
    )
    assert key == "retry-apply"


@pytest.mark.parametrize(
    ("error_type", "recovery_action", "expected_hint"),
    [
        ("PlanSchemaChangedError", None, "hint: create and review a new plan before applying again"),
        ("RegisteredPlanVerificationError", "rebuild", "hint: re-run diff for this sync"),
        (
            "RegisteredPlanVerificationError",
            "compatible_version",
            "or apply the reviewed plan with the version that wrote it",
        ),
        ("RegisteredPlanVerificationError", "private-token-canary", None),
        ("OperationApplyFailedError", "rebuild", None),
    ],
)
def test_failed_apply_renders_only_recognized_recovery_hints(
    client: MagicMock,
    error_type: str,
    recovery_action: str | None,
    expected_hint: str | None,
) -> None:
    """Show a recovery hint only for recognized failed-apply evidence."""
    client.wait_for_run.side_effect = RunTerminalError(
        "service-run-1",
        terminal_state="failed",
        terminal_outcome="failed",
        phase="apply-failed",
        outcome="failed",
    )
    client.get_results.return_value = ResultsResource(
        run_id="service-run-1",
        results={
            "apply_failure": {
                "stage": "apply",
                "outcome": "failed",
                "error_type": error_type,
                "recovery_action": recovery_action,
            }
        },
    )

    result = _invoke(
        client,
        "apply",
        "service-run-1",
        "--expected-checksum",
        CHECKSUM,
        "--reason",
        "apply reviewed plan",
    )

    assert result.exit_code == 1
    client.get_results.assert_called_once_with("service-run-1")
    assert f"apply failed: {error_type}" in result.output
    assert "private-token-canary" not in result.output
    if expected_hint is None:
        assert "hint:" not in result.output
    else:
        assert expected_hint in result.output


def test_failed_apply_keeps_terminal_verdict_when_failure_evidence_is_unavailable(client: MagicMock) -> None:
    client.wait_for_run.side_effect = RunTerminalError(
        "service-run-1",
        terminal_state="failed",
        terminal_outcome="failed",
        phase="apply-failed",
        outcome="failed",
    )
    client.get_results.side_effect = TransportError("get_results")

    result = _invoke(
        client,
        "apply",
        "service-run-1",
        "--expected-checksum",
        CHECKSUM,
        "--reason",
        "apply reviewed plan",
    )

    assert result.exit_code == 1
    client.get_results.assert_called_once_with("service-run-1")
    assert "error: run-terminal" in result.output
    assert "run_id: service-run-1" in result.output
    assert "terminal_state: failed" in result.output
    assert "terminal_outcome: failed" in result.output
    assert "phase: apply-failed" in result.output
    assert "outcome: failed" in result.output


def test_runs_show_renders_the_service_record_and_its_selected_execution(client: MagicMock) -> None:
    """An operator following a run needs the Prefect correlation, not just the phase."""
    client.get_run.return_value = _run(operation="apply", phase="applied")

    result = _invoke(client, "runs", "show", "service-run-1")

    assert result.exit_code == 0, result.output
    client.get_run.assert_called_once_with("service-run-1")
    for field in (
        "run_id: service-run-1",
        "operation: apply",
        "config_id: edge-sync",
        "registry_version: 1",
        f"package_checksum: {CHECKSUM}",
        "phase: applied",
        "execution_state: pending",
        "flow_run_id: flow-service-1",
    ):
        assert field in result.output


@pytest.mark.parametrize(
    ("terminal", "expected_state"),
    [
        (None, "pending"),
        (("completed", "succeeded"), "completed"),
        (("failed", "failed"), "failed"),
        (("cancelled", "cancelled"), "cancelled"),
        (("abandoned", "abandoned"), "abandoned"),
        (("interrupted", "ambiguous"), "interrupted"),
    ],
    ids=("pending", "completed", "failed", "cancelled", "abandoned", "interrupted"),
)
def test_runs_show_prefers_a_durable_execution_verdict_over_its_last_observed_state(
    client: MagicMock, terminal: tuple[TerminalState, TerminalOutcome] | None, expected_state: str
) -> None:
    """Nothing observes an execution after its verdict, so the observed state stops being current."""
    client.get_run.return_value = _run(terminal=terminal)

    result = _invoke(client, "runs", "show", "service-run-1")

    assert result.exit_code == 0, result.output
    assert f"execution_state: {expected_state}" in result.output.splitlines()


def test_runs_show_keeps_the_run_disposition_beside_an_interrupted_execution_verdict(client: MagicMock) -> None:
    """The ambiguous disposition an interrupted write leaves stays readable without a new field."""
    client.get_run.return_value = _run(
        operation="sync", phase="interrupted", outcome="ambiguous", terminal=("interrupted", "ambiguous")
    )

    result = _invoke(client, "runs", "show", "service-run-1")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "phase: interrupted" in lines
    assert "outcome: ambiguous" in lines
    assert "execution_state: interrupted" in lines


@pytest.mark.parametrize(
    ("needs_reconciling", "rendered"),
    [(True, "reconciliation_required: true"), (False, "reconciliation_required: false")],
)
def test_runs_show_reports_whether_the_run_needs_reconciling(
    client: MagicMock, rendered: str, *, needs_reconciling: bool
) -> None:
    """The write-safety verdict is a field of the run, so `runs show` is where it is read.

    Both values are printed. A field that appeared only when it was true would
    leave an operator unable to tell a run that does not need reconciling from
    one whose verdict this command does not report, and the only way to settle
    that is to go around the CLI at the HTTP API.
    """
    client.get_run.return_value = _run(operation="sync", phase="interrupted", reconciliation_required=needs_reconciling)

    result = _invoke(client, "runs", "show", "service-run-1")

    assert result.exit_code == 0, result.output
    assert rendered in result.output


def test_runs_results_renders_the_typed_response_as_parseable_json(client: MagicMock) -> None:
    """The API owns what a result says; this renders it without inventing structure."""
    client.get_results.return_value = ResultsResource(
        run_id="service-run-1",
        results={"apply": {"created": 3, "updated": 0}, "verification": {"checksum_ok": True}},
    )

    result = _invoke(client, "runs", "results", "service-run-1")

    assert result.exit_code == 0, result.output
    client.get_results.assert_called_once_with("service-run-1")
    assert json.loads(result.output) == {
        "run_id": "service-run-1",
        "results": {"apply": {"created": 3, "updated": 0}, "verification": {"checksum_ok": True}},
    }


def test_runs_results_escapes_control_characters_rather_than_emitting_them(client: MagicMock) -> None:
    """Recorded provider text can carry an escape sequence a terminal would act on."""
    client.get_results.return_value = ResultsResource(
        run_id="service-run-1",
        results={"apply_failure": {"error_type": "OperationApplyFailedError", "detail": "line\x1b[2Jone\ttwo"}},
    )

    result = _invoke(client, "runs", "results", "service-run-1")

    assert result.exit_code == 0, result.output
    rendered = result.output
    # The literal escape must be absent and its JSON escape present, so the two
    # are compared as the six characters `\u001b` rather than as the character.
    assert "\x1b" not in rendered
    assert "\\u001b" in rendered
    assert json.loads(rendered)["results"]["apply_failure"]["detail"] == "line\x1b[2Jone\ttwo"


def test_runs_plan_filters_detail_and_marks_deletes_not_executed(client: MagicMock) -> None:
    result = _invoke(client, "runs", "plan", "service-run-1", "--detail", "--kind", "Site")

    assert result.exit_code == 0, result.output
    client.get_plan.assert_called_once_with("service-run-1")
    assert f"plan_checksum: {CHECKSUM}" in result.output
    assert "op-delete" in result.output
    assert "(not executed)" in result.output
    assert "op-create" not in result.output


def test_runs_plan_renders_schema_fingerprint_when_present(client: MagicMock) -> None:
    fingerprint = "b" * 64
    client.get_plan.return_value = _plan().model_copy(update={"schema_fingerprint": fingerprint})

    result = _invoke(client, "runs", "plan", "service-run-1")

    assert result.exit_code == 0, result.output
    assert f"schema_fingerprint: {fingerprint}" in result.output


def test_runs_plan_unfiltered_detail_renders_every_operation(client: MagicMock) -> None:
    result = _invoke(client, "runs", "plan", "service-run-1", "--detail")

    assert result.exit_code == 0, result.output
    assert "op-create" in result.output
    assert "op-delete" in result.output


def test_runs_plan_discloses_when_delete_operations_were_not_computed(client: MagicMock) -> None:
    plan = _plan()
    summary = plan.summary.model_copy(update={"delete_operations_computed": False})
    client.get_plan.return_value = plan.model_copy(update={"summary": summary})

    result = _invoke(client, "runs", "plan", "service-run-1")

    assert result.exit_code == 0, result.output
    assert "Delete operations were NOT computed" in result.output


def test_runs_plan_discloses_zero_recorded_deletes(client: MagicMock) -> None:
    plan = _plan()
    summary = plan.summary.model_copy(
        update={
            "by_action": {"create": 1},
            "by_kind": {"Device": 1},
            "total": 1,
            "deletes_not_executed": 0,
        }
    )
    client.get_plan.return_value = plan.model_copy(update={"summary": summary, "operations": plan.operations[:1]})

    result = _invoke(client, "runs", "plan", "service-run-1")

    assert result.exit_code == 0, result.output
    assert "0 delete operations are recorded; apply has no deletes to skip." in result.output


def test_runs_plan_unmatched_kind_is_typed_input_error(client: MagicMock) -> None:
    result = _invoke(client, "runs", "plan", "service-run-1", "--detail", "--kind", "Typo")

    assert result.exit_code == 2
    assert "error: client-input" in result.output
    assert "argument: kind" in result.output


# A terminal acts on these rather than printing them: cursor movement, line erase,
# carriage return, newline, and a right-to-left override that reorders what follows.
# Built from code points so this source file carries no bidirectional control itself.
_RLO = chr(0x202E)
_LRI = chr(0x2066)
# Zero-width characters, direction marks, and the line and paragraph separators hide or
# break up text without a visible glyph.
_INVISIBLE_CODES = (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x200E, 0x200F, 0x061C, 0x2028, 0x2029)
_INVISIBLES = "".join(chr(code) for code in _INVISIBLE_CODES)
_TERMINAL_CONTROLS = ("\x1b", "\r", "\t", "\x9b", "\x7f", _RLO, _LRI, *_INVISIBLES)


def _assert_no_terminal_controls(rendered: str) -> None:
    # An injected newline is caught by the exact-line assertions: a split value never
    # matches the single escaped line they expect.
    for line in rendered.split("\n"):
        for character in _TERMINAL_CONTROLS:
            assert character not in line, repr(line)


def test_runs_plan_detail_escapes_terminal_controls_in_source_values(client: MagicMock) -> None:
    """A source-derived value must not be able to rewrite the lines a reviewer approves."""
    forged = f"edge\x1b[1A\x1b[2K\rop-forged create Device name=fake\t\n\x9b2K\x7f{_RLO}evil{_LRI}x{_INVISIBLES}"
    plan = _plan()
    operation = plan.operations[0].model_copy(
        update={"kind": "Dev\x1bice", "identity": {"name": forged}, "destination_id": "dest\r1"}
    )
    summary = plan.summary.model_copy(update={"by_kind": {"Dev\x1bice": 1, "Site": 1}})
    client.get_plan.return_value = plan.model_copy(
        update={
            "operations": (operation, plan.operations[1]),
            "summary": summary,
            "verification_notes": ("note\x1b[2Kforged",),
            "destination_branch": "review\rmain",
        }
    )

    result = _invoke(client, "runs", "plan", "service-run-1", "--detail")

    assert result.exit_code == 0, result.output
    _assert_no_terminal_controls(result.output)
    # Split on "\n" only: `splitlines` would also break at an unescaped U+2028 or U+2029.
    lines = result.output.split("\n")
    assert (
        "op-create create Dev\\x1bice name=edge\\x1b[1A\\x1b[2K\\rop-forged create Device name=fake"
        "\\t\\n\\x9b2K\\x7f\\u202eevil\\u2066x"
        "\\u200b\\u200c\\u200d\\u2060\\ufeff\\u200e\\u200f\\u061c\\u2028\\u2029"
    ) in lines
    assert "  destination id: dest\\r1" in lines
    assert "verification_note: note\\x1b[2Kforged" in lines
    assert "destination_branch: review\\rmain" in lines
    assert "by_kind: Dev\\x1bice=1, Site=1" in lines


def test_runs_plan_detail_keeps_ordinary_non_ascii_values_unchanged(client: MagicMock) -> None:
    plan = _plan()
    operation = plan.operations[0].model_copy(update={"identity": {"name": "Zürich-東京 Łódź"}})
    client.get_plan.return_value = plan.model_copy(update={"operations": (operation, plan.operations[1])})

    result = _invoke(client, "runs", "plan", "service-run-1", "--detail")

    assert result.exit_code == 0, result.output
    assert "op-create create Device name=Zürich-東京 Łódź" in result.output.splitlines()


def test_runs_plan_detail_escapes_invisible_format_characters_in_source_values(client: MagicMock) -> None:
    plan = _plan()
    operation = plan.operations[0].model_copy(update={"identity": {"name": "a\u00adb\U000e0041c"}})
    client.get_plan.return_value = plan.model_copy(update={"operations": (operation, plan.operations[1])})

    result = _invoke(client, "runs", "plan", "service-run-1", "--detail")

    assert result.exit_code == 0, result.output
    assert "op-create create Device name=a\\xadb\\U000e0041c" in result.output.split("\n")


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0x00AD, "\\xad"),
        (0x180E, "\\u180e"),
        (0x2061, "\\u2061"),
        (0x2064, "\\u2064"),
        (0x206A, "\\u206a"),
        (0x206F, "\\u206f"),
        (0xFFF9, "\\ufff9"),
        (0xFFFB, "\\ufffb"),
        (0xE0001, "\\U000e0001"),
        (0xE0041, "\\U000e0041"),
        (0xE007F, "\\U000e007f"),
    ],
)
def test_display_escapes_invisible_format_characters(code: int, expected: str) -> None:
    assert _display(chr(code)) == expected


def test_display_escapes_every_control_and_format_code_point() -> None:
    readable = {"\t": "\\t", "\n": "\\n", "\r": "\\r"}
    for code in range(sys.maxunicode + 1):
        character = chr(code)
        rendered = _display(character)
        if unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}:
            assert rendered == readable.get(character, rendered), hex(code)
            assert rendered.isascii(), hex(code)
            assert rendered != character, hex(code)
        else:
            assert rendered == character, hex(code)


def test_display_keeps_ordinary_non_ascii_text() -> None:
    assert _display("Zürich-東京 Łódź") == "Zürich-東京 Łódź"


def test_diff_summary_escapes_terminal_controls_in_the_saved_plan(client: MagicMock) -> None:
    plan = _plan()
    client.get_plan.return_value = plan.model_copy(update={"verification_notes": ("ok\x1b[1A\rforged",)})

    result = _invoke(client, "diff", "--config-id", "edge-sync", "--version", "1", "--reason", "review")

    assert result.exit_code == 0, result.output
    _assert_no_terminal_controls(result.output)
    assert "verification_note: ok\\x1b[1A\\rforged" in result.output.splitlines()


def test_configuration_validation_escapes_terminal_controls_in_findings(client: MagicMock) -> None:
    client.validate_config.return_value = client.validate_config.return_value.model_copy(
        update={
            "findings": (
                ValidationFindingResource(
                    code="first", severity="warning", location="/a", message=f"bad\x1b[2K{_RLO}eulav"
                ),
            )
        }
    )

    validated = _invoke(client, "configs", "validate", "edge-sync", "1")

    assert validated.exit_code == 0, validated.output
    _assert_no_terminal_controls(validated.output)
    assert "finding: code=first severity=warning location=/a message=bad\\x1b[2K\\u202eeulav" in (
        validated.output.splitlines()
    )


def test_client_errors_escape_terminal_controls_in_server_text(client: MagicMock) -> None:
    client.list_configs.side_effect = ConfigsAPIError(
        403, "forbidden", "authorization", "denied\x1b[1A\rerror: none", mutation_id=f"m{_RLO}1"
    )

    result = _invoke(client, "configs", "list")

    assert result.exit_code == 1
    _assert_no_terminal_controls(result.output)
    assert "reason: denied\\x1b[1A\\rerror: none" in result.output.splitlines()
    assert "mutation_id: m\\u202e1" in result.output.splitlines()


def test_typed_config_refusal_preserves_machine_fields(client: MagicMock) -> None:
    client.list_configs.side_effect = ConfigsAPIError(
        403,
        "forbidden",
        "authorization",
        "administrator-required",
        mutation_id="mutation-1",
    )

    result = _invoke(client, "configs", "list")

    assert result.exit_code == 1
    for field in (
        "status: 403",
        "code: forbidden",
        "family: authorization",
        "reason: administrator-required",
        "mutation_id: mutation-1",
    ):
        assert field in result.output


def test_wait_timeout_preserves_run_and_last_product_state(client: MagicMock) -> None:
    client.wait_for_run.side_effect = RunWaitTimeoutError(
        "service-run-1",
        phase="running",
        outcome=None,
        execution_state="future-state",
    )

    result = _invoke(
        client,
        "sync",
        "--config-id",
        "edge-sync",
        "--version",
        "1",
        "--reason",
        "wait for sync",
    )

    assert result.exit_code == 1
    assert "run_id: service-run-1" in result.output
    assert "phase: running" in result.output
    assert "outcome: <none>" in result.output
    assert "execution_state: future-state" in result.output


@pytest.mark.parametrize("argument", ["wait_timeout", "poll_interval"])
def test_invalid_wait_input_maps_to_exit_two(client: MagicMock, argument: str) -> None:
    option = "--wait-timeout" if argument == "wait_timeout" else "--poll-interval"
    result = _invoke(
        client,
        "sync",
        "--config-id",
        "edge-sync",
        "--version",
        "1",
        "--reason",
        "wait for sync",
        option,
        "0",
    )

    assert result.exit_code == 2
    assert f"argument: {argument}" in result.output
    client.sync.assert_not_called()


def test_keyboard_interrupt_stops_only_the_local_wait(client: MagicMock) -> None:
    def interrupt_after_poll(_accepted: RunResource, **kwargs: object) -> None:
        observer = cast("Callable[[RunResource], None]", kwargs["on_observation"])
        observer(_run(operation="sync", phase="running"))
        raise KeyboardInterrupt

    client.wait_for_run.side_effect = interrupt_after_poll

    result = _invoke(
        client,
        "sync",
        "--config-id",
        "edge-sync",
        "--version",
        "1",
        "--reason",
        "wait for sync",
    )

    assert result.exit_code == 130
    assert "remote run was not cancelled" in result.output
    assert "run_id: service-run-1" in result.output
    assert "phase: running" in result.output
    client.cancel_run.assert_not_called()


def test_wait_transport_error_prints_last_observed_product_state(client: MagicMock) -> None:
    def fail_after_poll(_accepted: RunResource, **kwargs: object) -> None:
        observer = cast("Callable[[RunResource], None]", kwargs["on_observation"])
        observer(_run(operation="sync", phase="running"))
        operation = "get_run"
        raise TransportError(operation)

    client.wait_for_run.side_effect = fail_after_poll

    result = _invoke(
        client,
        "sync",
        "--config-id",
        "edge-sync",
        "--version",
        "1",
        "--reason",
        "wait for sync",
    )

    assert result.exit_code == 1
    assert "run_id: service-run-1" in result.output
    assert "phase: running" in result.output
    assert result.output.index("phase: running") < result.output.index("error: transport")


def test_mutation_transport_failure_still_discloses_the_retry_key(client: MagicMock) -> None:
    client.sync.side_effect = TransportError("create_run")

    result = _invoke(
        client,
        "sync",
        "--config-id",
        "edge-sync",
        "--version",
        "1",
        "--reason",
        "retry uncertain admission",
        "--idempotency-key",
        "retry-after-timeout",
        "--no-wait",
    )

    assert result.exit_code == 1
    assert "idempotency_key: retry-after-timeout" in result.output
    assert "error: transport" in result.output


@pytest.mark.parametrize(
    ("error", "label", "exit_code"),
    [
        (ClientInputError("config_id"), "client-input", 2),
        (CompatibilityError("3", ("v4",)), "compatibility", 1),
        (TransportError("list_configs"), "transport", 1),
        (ProtocolError("list_configs", 200), "protocol", 1),
        (APIError(404, "not-found", run_id="run-1", mutation_id="mutation-1"), "api", 1),
        (
            RunTerminalError(
                "run-1",
                terminal_state="failed",
                terminal_outcome="failed",
                phase="finished",
                outcome="failed",
            ),
            "run-terminal",
            1,
        ),
    ],
)
def test_closed_client_errors_map_to_cli_exits(
    client: MagicMock,
    error: Exception,
    label: str,
    exit_code: int,
) -> None:
    client.list_configs.side_effect = error

    result = _invoke(client, "configs", "list")

    assert result.exit_code == exit_code
    assert f"error: {label}" in result.output


@pytest.mark.parametrize("operation", ["register", "diff"])
def test_store_refusal_explains_how_to_keep_data_in_memory(tmp_path: Path, client: MagicMock, operation: str) -> None:
    """A 422 unsupported-sync-store response prints guidance to remove the store block."""
    if operation == "register":
        package_path = tmp_path / "package.json"
        package_path.write_text("{}", encoding="utf-8")
        client.register_config.side_effect = ConfigsAPIError(
            422, "configs-validation", "validation", "unsupported-sync-store"
        )
        arguments = ("configs", "register", str(package_path), "--reason", "register inventory")
    else:
        client.plan.side_effect = APIError(422, "unsupported-sync-store")
        arguments = ("diff", "--config-id", "edge-sync", "--version", "1", "--reason", "plan inventory")

    result = _invoke(client, *arguments)

    assert result.exit_code == 1
    assert "Configured sync stores, including Redis, are not supported in V3." in result.output
    assert "Remove the store block to keep sync data in memory." in result.output
