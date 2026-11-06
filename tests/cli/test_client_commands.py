"""Typer coverage for the SyncClient-backed command surface."""

from __future__ import annotations

import json
import logging
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from infrahub_sync.cli import _display, app  # noqa: PLC2701 - the renderer under test is private.
from infrahub_sync.client import (
    APIError,
    ApplyRunRequest,
    ArtifactListResource,
    ArtifactReferenceResource,
    CancelRunRequest,
    ClientInputError,
    CompatibilityError,
    ConfigMutationRequest,
    ConfigsAPIError,
    ConfigurationPage,
    ConfigurationSummaryResource,
    ConfigurationVersionPage,
    ConfigurationVersionResource,
    ConfigurationVersionSummaryResource,
    CreateRunRequest,
    EventPage,
    EventPrimaryResource,
    EventResource,
    OrchestrationSummary,
    PlanLastChangeResource,
    PlanOperationResource,
    PlanResource,
    PlanSummaryResource,
    PrincipalResource,
    ProtocolError,
    PublicExecutionLink,
    PublicRunResource,
    RegisteredConfigurationResource,
    RegisteredVersionResource,
    ResultsResource,
    RunLogLine,
    RunLogPage,
    RunPage,
    RunReference,
    RunResource,
    RunTerminalError,
    RunWaitTimeoutError,
    SyncClient,
    TransportError,
    ValidationFindingResource,
    ValidationReportResource,
    VerifyRunRequest,
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


def _version_summary(
    version: int = 1,
    *,
    latest_run: RunReference | None = None,
    latest_applied_run: RunReference | None = None,
) -> ConfigurationVersionSummaryResource:
    return ConfigurationVersionSummaryResource(
        config_id="edge-sync",
        registry_version=version,
        package_checksum=CHECKSUM,
        created_at=NOW,
        latest_run=latest_run,
        latest_applied_run=latest_applied_run,
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
    injected.list_configs.return_value = ConfigurationPage(
        items=(ConfigurationSummaryResource(config_id="edge-sync", created_at=NOW),)
    )
    # The resolver reads `GET /configs/{value}` first, so any identifier resolves to itself.
    injected.get_config.side_effect = lambda config_id: ConfigurationSummaryResource(
        config_id=config_id, created_at=NOW
    )
    injected.list_config_versions.return_value = ConfigurationVersionPage(items=(_version_summary(),))
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
    injected.verify.return_value = _run()
    injected.cancel_run.return_value = _run(phase="cancelling")
    injected.iter_configs.side_effect = lambda **_kwargs: iter(())
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
    client.list_configs.assert_called_once_with(name=None, limit=None)
    # Each CONFIG argument is resolved through `GET /configs/{value}` first; `show` prints that read.
    assert client.get_config.call_count == 4
    client.get_config_version.assert_called_once_with("edge-sync", 1)
    client.list_config_versions.assert_called_once_with("edge-sync", limit=None)
    client.validate_config.assert_called_once_with("edge-sync", 1, offset=2, limit=3)
    assert f"package_checksum: {CHECKSUM}" in shown.output
    assert "offset: 2" in validated.output
    assert "limit: 3" in validated.output
    assert "total_findings: 7" in validated.output
    assert "next_offset: 5" in validated.output
    assert validated.output.index("first finding") < validated.output.index("second finding")


def test_plan_uses_registered_tuple_waits_and_renders_service_plan(client: MagicMock) -> None:
    result = _invoke(
        client,
        "plan",
        "--config",
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


REBUILD_HINT = "re-run plan for this sync, review the new plan, then apply that run"
COMPATIBLE_VERSION_HINT = (
    "re-run plan with this version and review the new plan, or apply the reviewed plan with the version that wrote it"
)


@pytest.mark.parametrize(
    ("error_type", "recorded", "expected_hint"),
    [
        (
            "PlanSchemaChangedError",
            {"next_action": "create and review a new plan before applying again"},
            "hint: create and review a new plan before applying again",
        ),
        (
            "RegisteredPlanVerificationError",
            {"recovery_action": "rebuild", "next_action": REBUILD_HINT},
            f"hint: {REBUILD_HINT}",
        ),
        (
            "RegisteredPlanVerificationError",
            {"recovery_action": "compatible_version", "next_action": COMPATIBLE_VERSION_HINT},
            f"hint: {COMPATIBLE_VERSION_HINT}",
        ),
        (
            "RegisteredPlanVerificationError",
            {"recovery_action": "rebuild", "next_action": "checksum: rebuild"},
            "hint: checksum: rebuild",
        ),
        # An older record without `next_action` gets the fixed hint for its recovery signal.
        ("RegisteredPlanVerificationError", {"recovery_action": "rebuild"}, f"hint: {REBUILD_HINT}"),
        (
            "RegisteredPlanVerificationError",
            {"recovery_action": "compatible_version"},
            f"hint: {COMPATIBLE_VERSION_HINT}",
        ),
        ("PlanSchemaChangedError", {}, "hint: create and review a new plan before applying again"),
        ("RegisteredPlanVerificationError", {"recovery_action": "private-token-canary"}, None),
        ("RegisteredPlanVerificationError", {}, None),
        ("OperationApplyFailedError", {"recovery_action": "rebuild"}, None),
    ],
)
def test_failed_apply_renders_the_recorded_next_action_or_the_older_records_hint(
    client: MagicMock,
    error_type: str,
    recorded: dict[str, str],
    expected_hint: str | None,
) -> None:
    """Print the recorded next action as the hint; without one, the fixed hint an older record's type or signal maps to."""
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
                **recorded,
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


MISSING_PERMISSION_CAUSE = (
    "a missing permission: the import account needs the permission `object:*:Core:Account:view:allow_all` "
    "(AuthenticationError, HTTP 403)"
)
MISMATCH_CAUSE = (
    "The saved plan for run 'service-run-1' was made with the import account 'netbox' (id a-1), and this apply "
    "runs with the import account 'other' (id a-2). Nothing was written to the destination."
)
MISMATCH_NEXT_ACTION = (
    "Apply with a token of the account the plan recorded, or create a new plan with this token and review it."
)


def _terminal(phase: str) -> RunTerminalError:
    return RunTerminalError(
        "service-run-1", terminal_state="failed", terminal_outcome="failed", phase=phase, outcome="failed"
    )


@pytest.mark.parametrize(
    ("arguments", "stage"),
    [
        (("diff", "--config-id", "edge-sync", "--version", "1", "--reason", "review"), "plan"),
        (("sync", "--config-id", "edge-sync", "--version", "1", "--reason", "sync now"), "sync"),
        (("apply", "service-run-1", "--expected-checksum", CHECKSUM, "--reason", "apply reviewed plan"), "apply"),
    ],
)
def test_a_failed_run_prints_the_recorded_missing_permission(
    client: MagicMock, arguments: tuple[str, ...], stage: str
) -> None:
    """A 403 at any stage is printed as a missing permission naming the permission, never a refused token."""
    client.wait_for_run.side_effect = _terminal(f"{stage}-failed")
    client.get_results.return_value = ResultsResource(
        run_id="service-run-1",
        results={
            f"{stage}_failure": {
                "stage": stage,
                "outcome": "failed",
                "error_type": "ValueError",
                "cause_type": "ValueError",
                "infrahub_error": {
                    "problem": "missing_permission",
                    "http_status": 403,
                    "permissions": ["object:*:Core:Account:view:allow_all"],
                },
                "cause": MISSING_PERMISSION_CAUSE,
            }
        },
    )

    result = _invoke(client, *arguments)

    assert result.exit_code == 1
    assert f"{stage} failed: ValueError" in result.output
    assert f"cause: {MISSING_PERMISSION_CAUSE}" in result.output
    assert "refused the token" not in result.output
    assert "error: run-terminal" in result.output


def test_a_failed_apply_prints_the_account_mismatch_and_its_next_action(client: MagicMock) -> None:
    """The operator reads both accounts and what to do, not only the error class."""
    client.wait_for_run.side_effect = _terminal("apply-failed")
    client.get_results.return_value = ResultsResource(
        run_id="service-run-1",
        results={
            "apply_failure": {
                "stage": "apply",
                "outcome": "failed",
                "error_type": "ImportAccountMismatchError",
                "cause_type": None,
                "cause": MISMATCH_CAUSE,
                "next_action": MISMATCH_NEXT_ACTION,
            }
        },
    )

    result = _invoke(
        client, "apply", "service-run-1", "--expected-checksum", CHECKSUM, "--reason", "apply reviewed plan"
    )

    assert result.exit_code == 1
    assert "apply failed: ImportAccountMismatchError" in result.output
    assert f"cause: {MISMATCH_CAUSE}" in result.output
    assert f"hint: {MISMATCH_NEXT_ACTION}" in result.output


def test_a_recorded_cause_is_printed_with_its_control_characters_made_visible(client: MagicMock) -> None:
    client.wait_for_run.side_effect = _terminal("plan-failed")
    client.get_results.return_value = ResultsResource(
        run_id="service-run-1",
        results={
            "plan_failure": {
                "stage": "plan",
                "outcome": "failed",
                "error_type": "ValueError",
                "cause": "line\x1b[2Jone",
                "next_action": "x" * 5000,
            }
        },
    )

    result = _invoke(client, "diff", "--config-id", "edge-sync", "--version", "1", "--reason", "review")

    assert "\x1b" not in result.output
    assert "cause: line\\x1b[2Jone" in result.output
    hint = next(line for line in result.output.splitlines() if line.startswith("hint: "))
    assert len(hint) <= len("hint: ") + 1000


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
    ("link", "expected"),
    [
        ("http://127.0.0.1:4230/runs/flow-run/flow-service-1", "http://127.0.0.1:4230/runs/flow-run/flow-service-1"),
        (None, "<none>"),
    ],
    ids=("linked", "null"),
)
def test_runs_show_prints_the_prefect_flow_run_link_right_after_the_flow_run_id(
    client: MagicMock, link: str | None, expected: str
) -> None:
    """US3: the operator copies the Prefect UI link from the line under the flow run it opens."""
    resource = _run()
    execution = resource.orchestration[0].model_copy(update={"prefect_flow_run_url": link})
    client.get_run.return_value = resource.model_copy(update={"orchestration": (execution,)})

    result = _invoke(client, "runs", "show", "service-run-1")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    flow_run_line = lines.index("flow_run_id: flow-service-1")
    assert lines[flow_run_line + 1] == f"prefect_flow_run_url: {expected}"


def test_runs_show_prints_no_prefect_flow_run_link_when_the_response_omits_it(client: MagicMock) -> None:
    """A Sync API that predates the link sends no field, which reads as no link."""
    payload = _run().orchestration[0].model_dump(mode="json")
    del payload["prefect_flow_run_url"]
    resource = _run()
    client.get_run.return_value = resource.model_copy(
        update={"orchestration": (OrchestrationSummary.model_validate(payload),)}
    )

    result = _invoke(client, "runs", "show", "service-run-1")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[lines.index("flow_run_id: flow-service-1") + 1] == "prefect_flow_run_url: <none>"


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


def test_runs_show_prints_the_recorded_cause_and_next_action_of_a_failed_stage(client: MagicMock) -> None:
    """FR-002: a run that failed on a missing branch names the branch and the fix in `runs show`."""
    resource = _run(operation="sync", phase="failed", outcome="failed")
    cause = "Branch `demo-test` does not exist in Infrahub."
    next_action = "Create the branch `demo-test` in Infrahub, or set the run's destination branch, then run again."
    failed_run = resource.run.model_copy(
        update={
            "results": {
                "sync_failure": {
                    "stage": "sync",
                    "error_type": "BranchNotFoundError",
                    "cause": cause,
                    "next_action": next_action,
                }
            }
        }
    )
    client.get_run.return_value = resource.model_copy(update={"run": failed_run})

    result = _invoke(client, "runs", "show", "service-run-1")

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert "sync failed: BranchNotFoundError" in lines
    assert f"cause: {cause}" in lines
    assert f"hint: {next_action}" in lines
    client.get_results.assert_not_called()


def test_runs_show_leaves_out_a_stage_failure_that_a_later_attempt_of_the_stage_succeeded(
    client: MagicMock,
) -> None:
    """A verify merges its result into the run's results, so a failed verify's record outlives a later success."""
    resource = _run(phase="planned", terminal=("completed", "succeeded"))
    verified = resource.orchestration[0].model_copy(update={"purpose": "verify"})
    stale_run = resource.run.model_copy(
        update={
            "results": {
                "verification": {"checksum_ok": True},
                "verify_failure": {
                    "stage": "verify",
                    "error_type": "DestinationSchemaUnavailableError",
                    "cause": "Infrahub did not answer in time.",
                },
            }
        }
    )
    client.get_run.return_value = resource.model_copy(update={"run": stale_run, "orchestration": (verified,)})

    result = _invoke(client, "runs", "show", "service-run-1")

    assert result.exit_code == 0, result.output
    assert "verify failed: DestinationSchemaUnavailableError" not in result.stdout.splitlines()
    assert "cause: Infrahub did not answer in time." not in result.stdout.splitlines()


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
    """Check every code point against an independently spelled expected escape."""
    readable = {"\t": "\\t", "\n": "\\n", "\r": "\\r"}
    for code in range(sys.maxunicode + 1):
        character = chr(code)
        rendered = _display(character)
        if unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}:
            if character in readable:
                expected = readable[character]
            elif code <= 0xFF:
                expected = "\\x" + format(code, "02x")
            elif code <= 0xFFFF:
                expected = "\\u" + format(code, "04x")
            else:
                expected = "\\U" + format(code, "08x")
            assert rendered == expected, hex(code)
        else:
            assert rendered == character, hex(code)


def test_display_keeps_ordinary_non_ascii_text() -> None:
    assert _display("Zürich-東京 Łódź") == "Zürich-東京 Łódź"


def test_plan_summary_escapes_terminal_controls_in_the_saved_plan(client: MagicMock) -> None:
    plan = _plan()
    client.get_plan.return_value = plan.model_copy(update={"verification_notes": ("ok\x1b[1A\rforged",)})

    result = _invoke(client, "plan", "--config-id", "edge-sync", "--version", "1", "--reason", "review")

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


@pytest.mark.parametrize("operation", ["register", "plan"])
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
        arguments = ("plan", "--config", "edge-sync", "--version", "1", "--reason", "plan inventory")

    result = _invoke(client, *arguments)

    assert result.exit_code == 1
    assert "Configured sync stores, including Redis, are not supported in V3." in result.output
    assert "Remove the store block to keep sync data in memory." in result.output


@pytest.mark.parametrize(
    ("arguments", "method"),
    [
        pytest.param(("configs", "register", "{package}"), "register_config", id="configs-register"),
        pytest.param(("configs", "version", "edge-sync", "{package}"), "create_config_version", id="configs-version"),
        pytest.param(("plan", "--config", "edge-sync", "--version", "1"), "plan", id="plan"),
        pytest.param(("verify", "service-run-1"), "verify", id="verify"),
        pytest.param(("runs", "cancel", "service-run-1", "--yes"), "cancel_run", id="runs-cancel"),
        pytest.param(("sync", "--config-id", "edge-sync", "--version", "1"), "sync", id="sync"),
        pytest.param(("apply", "service-run-1", "--expected-checksum", CHECKSUM), "apply", id="apply"),
    ],
)
def test_a_mutation_without_reason_sends_no_reason(
    client: MagicMock, tmp_path: Path, arguments: tuple[str, ...], method: str
) -> None:
    """`--reason` is optional: when it is omitted, the request carries no reason at all."""
    package = tmp_path / "package.yaml"
    package.write_text("name: edge-sync\n", encoding="utf-8")

    result = _invoke(client, *(argument.format(package=package) for argument in arguments))

    assert result.exit_code == 0, result.output
    request = next(argument for argument in getattr(client, method).call_args.args if hasattr(argument, "reason"))
    assert request.reason is None
    assert request.model_dump(mode="json")["reason"] is None
    assert "None" not in result.output


@pytest.mark.parametrize("reason", ["", "line\nbreak"])
def test_a_given_reason_is_still_validated(client: MagicMock, tmp_path: Path, reason: str) -> None:
    package = tmp_path / "package.yaml"
    package.write_text("name: edge-sync\n", encoding="utf-8")

    result = _invoke(client, "configs", "register", str(package), "--reason", reason)

    assert result.exit_code != 0
    client.register_config.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [
        ("configs", "register"),
        ("configs", "version"),
        ("plan",),
        ("sync",),
        ("apply",),
        ("verify",),
        ("runs", "cancel"),
    ],
    ids=" ".join,
)
def test_help_does_not_mark_reason_as_required(command: tuple[str, ...]) -> None:
    result = RUNNER.invoke(app, [*command, "--help"], env={"NO_COLOR": "1", "COLUMNS": "200", "TERM": "dumb"})

    assert result.exit_code == 0, result.output
    reason_line = next(line for line in result.output.splitlines() if "--reason" in line)
    assert "required" not in reason_line.lower()


def _plan_with_an_update(last_changes: tuple[PlanLastChangeResource, ...] | None) -> PlanResource:
    plan = _plan()
    update = PlanOperationResource(
        operation_id="op_0123456789abcdef",
        action="update",
        kind="Site",
        identity={"name": "par1"},
        tier=0,
        payload={"description": "Paris"},
        relationships=(),
        destination_id="site-1",
    )
    return plan.model_copy(update={"operations": (*plan.operations, update), "last_changes": last_changes})


def _last_change(field: str, field_type: str, updated_by: dict[str, str | None] | None, updated_at: str | None):  # type: ignore[no-untyped-def]
    return PlanLastChangeResource.model_validate(
        {
            "operation_id": "op_0123456789abcdef",
            "field": field,
            "field_type": field_type,
            "updated_by": updated_by,
            "updated_at": updated_at,
        }
    )


def test_runs_plan_detail_shows_who_last_changed_each_field_an_update_changes(client: MagicMock) -> None:
    """Each changed field shows `updated_by` and `updated_at`, or "not recorded"."""
    client.get_plan.return_value = _plan_with_an_update(
        (
            _last_change(
                "description", "attribute", {"id": "account-2", "display_label": "Jane Doe"}, "2026-10-21T09:14:03Z"
            ),
            _last_change("site", "relationship", None, None),
            _last_change("status", "attribute", {"id": "account-3", "display_label": None}, None),
        )
    )

    result = _invoke(client, "runs", "plan", "service-run-1", "--detail")

    assert result.exit_code == 0, result.output
    lines = result.output.split("\n")
    update_line = lines.index("op_0123456789abcdef update Site name=par1")
    assert lines[update_line + 1 : update_line + 7] == [
        "  destination id: site-1",
        '  payload: {"description":"Paris"}',
        "  last change: description (attribute): updated_by=Jane Doe, updated_at=2026-10-21T09:14:03Z",
        "  last change: site (relationship): updated_by=not recorded, updated_at=not recorded",
        # Infrahub holds the account, without a label: the id is shown, not "not recorded".
        "  last change: status (attribute): updated_by=account-3, updated_at=not recorded",
        "",
    ]
    # A create has no last-change record, and no line.
    create_line = lines.index("op-create create Device name=edge-01")
    assert not lines[create_line + 1].startswith("  last change:")


def test_runs_plan_detail_of_an_older_plan_shows_no_last_change_lines(client: MagicMock) -> None:
    """A plan made before `last_changes.jsonl` existed is reviewed without last-change data."""
    client.get_plan.return_value = _plan_with_an_update(None)

    result = _invoke(client, "runs", "plan", "service-run-1", "--detail")

    assert result.exit_code == 0, result.output
    assert "last change:" not in result.output


def test_runs_plan_detail_of_a_plan_with_a_bad_last_change_record_shows_the_warning(client: MagicMock) -> None:
    """A bad record shows one warning and the operations, never a "not recorded" line."""
    note = "last_changes.jsonl line 4 repeats field 'name' of operation 'op-12'; last-change details are not shown"
    plan = _plan_with_an_update(None)
    client.get_plan.return_value = plan.model_copy(update={"verification_notes": (note,)})

    result = _invoke(client, "runs", "plan", "service-run-1", "--detail")

    assert result.exit_code == 0, result.output
    assert f"verification_note: {note}" in result.output.splitlines()
    assert "last change:" not in result.output
    assert all(operation.operation_id in result.output for operation in plan.operations)


def test_runs_plan_summary_shows_no_last_change_lines(client: MagicMock) -> None:
    """Without `--detail` the review stays a summary, however many fields a plan changes."""
    client.get_plan.return_value = _plan_with_an_update((_last_change("site", "relationship", None, None),))

    result = _invoke(client, "runs", "plan", "service-run-1")

    assert result.exit_code == 0, result.output
    assert "last change:" not in result.output


def test_runs_plan_escapes_the_recorded_account_label(client: MagicMock) -> None:
    """A display label comes from Infrahub, so it is escaped before a reviewer reads it."""
    client.get_plan.return_value = _plan_with_an_update(
        (
            _last_change(
                "description",
                "attribute",
                {"id": "account-2", "display_label": "Jane\x1b[2KDoe"},
                "2026-10-21T09:14:03Z",
            ),
        )
    )

    result = _invoke(client, "runs", "plan", "service-run-1", "--detail")

    assert result.exit_code == 0, result.output
    _assert_no_terminal_controls(result.output)
    assert "updated_by=Jane\\x1b[2KDoe" in result.output


def _public_run(run_id: str, *, config_id: str | None = "edge-sync", outcome: str | None = None) -> PublicRunResource:
    return PublicRunResource(
        run_id=run_id,
        operation="plan",
        configuration_reference="edge-sync@1",
        config_id=config_id,
        registry_version=1 if config_id is not None else None,
        package_checksum=CHECKSUM if config_id is not None else None,
        actor="operator",
        started_at=NOW,
        phase="planned",
        outcome=outcome,
        destination_branch="review" if config_id is not None else None,
    )


def _help_text(*args: str) -> str:
    result = RUNNER.invoke(app, [*args, "--help"], env={"NO_COLOR": "1", "COLUMNS": "200", "TERM": "dumb"})
    assert result.exit_code == 0, result.output
    return result.output


def test_diff_is_a_hidden_alias_of_plan_that_warns_on_stderr(client: MagicMock) -> None:
    result = _invoke(
        client, "diff", "--config-id", "edge-sync", "--version", "1", "--reason", "review", "--idempotency-key", "k"
    )

    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines()[0] == "warning: 'diff' is deprecated, use 'plan'"
    assert "deprecated" not in result.stdout
    request, key = client.plan.call_args.args
    assert request == CreateRunRequest(
        operation="plan", config_id="edge-sync", registry_version=1, confirm_writes=False, reason="review"
    )
    assert key == "k"
    assert f"plan_checksum: {CHECKSUM}" in result.stdout


def test_root_help_lists_plan_and_hides_diff() -> None:
    commands = {line.split()[1] for line in _help_text().splitlines() if line.startswith("│ ")}

    assert {"plan", "verify", "whoami", "sync", "apply", "configs", "runs"} <= commands
    assert "diff" not in commands


def test_plan_accepts_config_and_its_config_id_alias() -> None:
    help_text = _help_text("plan")

    assert "--config" in help_text
    assert "--config-id" in help_text


def test_verify_calls_client_verify_with_the_reason_and_retry_key(client: MagicMock) -> None:
    result = _invoke(client, "verify", "service-run-1", "--reason", "recheck", "--idempotency-key", "retry-verify")

    assert result.exit_code == 0, result.output
    client.verify.assert_called_once_with("service-run-1", VerifyRunRequest(reason="recheck"), "retry-verify")
    assert "idempotency_key: retry-verify" in result.output
    assert "run_id: service-run-1" in result.output


def test_runs_list_without_filters_prints_one_row_per_run(client: MagicMock) -> None:
    client.list_runs.return_value = RunPage(
        items=(_public_run("run-2", outcome="planned"), _public_run("run-1", config_id=None)),
        next_cursor=None,
    )

    result = _invoke(client, "runs", "list")

    assert result.exit_code == 0, result.output
    client.list_runs.assert_called_once_with(config_id=None, registry_version=None, limit=None)
    client.get_config.assert_not_called()
    assert result.stdout.splitlines() == [
        f"run-2  edge-sync@1  review  planned  planned  operator  {NOW.isoformat()}",
        f"run-1  -  -  planned  -  operator  {NOW.isoformat()}",
    ]
    assert "more runs" not in result.output


def test_runs_list_filters_by_configuration_and_version_and_limit(client: MagicMock) -> None:
    client.list_runs.return_value = RunPage(items=(_public_run("run-1"),), next_cursor=None)

    result = _invoke(client, "runs", "list", "--config", "edge-sync", "--version", "2", "--limit", "5")

    assert result.exit_code == 0, result.output
    client.get_config.assert_called_once_with("edge-sync")
    client.list_runs.assert_called_once_with(config_id="edge-sync", registry_version=2, limit=5)


def test_runs_list_version_without_config_is_refused(client: MagicMock) -> None:
    result = _invoke(client, "runs", "list", "--version", "2")

    assert result.exit_code == 2
    assert "argument: version" in result.output
    client.list_runs.assert_not_called()


def test_runs_list_prints_the_more_runs_hint_when_a_next_page_exists(client: MagicMock) -> None:
    client.list_runs.return_value = RunPage(items=(_public_run("run-2"),), next_cursor="c1")

    result = _invoke(client, "runs", "list")

    assert result.exit_code == 0, result.output
    assert "more runs: rerun with --all, or --limit up to 200" in result.stderr
    assert "more runs" not in result.stdout


def test_runs_list_all_follows_cursors(client: MagicMock) -> None:
    client.iter_runs.return_value = iter((_public_run("run-3"), _public_run("run-2"), _public_run("run-1")))

    result = _invoke(client, "runs", "list", "--all", "--limit", "1")

    assert result.exit_code == 0, result.output
    client.iter_runs.assert_called_once_with(config_id=None, registry_version=None, limit=1)
    client.list_runs.assert_not_called()
    assert [line.split()[0] for line in result.stdout.splitlines()] == ["run-3", "run-2", "run-1"]
    assert "more runs" not in result.output


def test_runs_artifacts_calls_list_artifacts(client: MagicMock) -> None:
    client.list_artifacts.return_value = ArtifactListResource(
        run_id="service-run-1",
        artifacts=(
            ArtifactReferenceResource(
                artifact_id="artifact-1",
                run_id="service-run-1",
                kind="plan",
                media_type="application/json",
                digest=CHECKSUM,
                size=12,
                object_key=f"runs/service-run-1/{CHECKSUM}/plan.json",
                manifest_key=f"runs/service-run-1/{CHECKSUM}/manifest.json",
                created_at=NOW,
            ),
        ),
    )

    result = _invoke(client, "runs", "artifacts", "service-run-1")

    assert result.exit_code == 0, result.output
    client.list_artifacts.assert_called_once_with("service-run-1")
    assert "artifact_id: artifact-1" in result.output
    assert "kind: plan" in result.output
    assert f"digest: {CHECKSUM}" in result.output


def test_runs_cancel_without_yes_refuses_when_not_interactive(client: MagicMock) -> None:
    result = _invoke(client, "runs", "cancel", "service-run-1")

    assert result.exit_code == 2
    assert "--yes" in result.stderr
    client.cancel_run.assert_not_called()


def test_runs_cancel_with_yes_calls_cancel_run(client: MagicMock) -> None:
    result = _invoke(
        client, "runs", "cancel", "service-run-1", "--yes", "--reason", "stop", "--idempotency-key", "retry-cancel"
    )

    assert result.exit_code == 0, result.output
    client.cancel_run.assert_called_once_with("service-run-1", CancelRunRequest(reason="stop"), "retry-cancel")
    assert "phase: cancelling" in result.output


@pytest.mark.parametrize(("answer", "cancelled"), [("y\n", True), ("n\n", False)])
def test_runs_cancel_prompts_when_interactive(
    client: MagicMock, monkeypatch: pytest.MonkeyPatch, answer: str, *, cancelled: bool
) -> None:
    monkeypatch.setattr("infrahub_sync.cli._stdin_is_interactive", lambda: True)

    result = RUNNER.invoke(app, ["runs", "cancel", "service-run-1"], obj={"client": client}, input=answer)

    assert client.cancel_run.called is cancelled
    assert result.exit_code == (0 if cancelled else 1), result.output


def test_whoami_prints_actor_and_administrator(client: MagicMock) -> None:
    client.me.return_value = PrincipalResource(actor="operator", administrator=False)

    result = _invoke(client, "whoami")

    assert result.exit_code == 0, result.output
    client.me.assert_called_once_with()
    assert result.stdout.splitlines() == ["actor: operator", "administrator: false"]


def test_configs_list_filters_by_name_and_prints_the_name(client: MagicMock) -> None:
    client.list_configs.return_value = ConfigurationPage(
        items=(
            ConfigurationSummaryResource(config_id="edge-sync", created_at=NOW, name="from-netbox", latest_version=3),
        ),
        next_cursor="c1",
    )

    result = _invoke(client, "configs", "list", "--name", "from-netbox", "--limit", "1")

    assert result.exit_code == 0, result.output
    client.list_configs.assert_called_once_with(name="from-netbox", limit=1)
    assert "name: from-netbox" in result.stdout
    assert "latest_version: 3" in result.stdout
    assert "more configurations: rerun with --all, or --limit up to 200" in result.stderr


def test_configs_list_all_follows_cursors(client: MagicMock) -> None:
    client.iter_configs.side_effect = None
    client.iter_configs.return_value = iter(
        (
            ConfigurationSummaryResource(config_id="b", created_at=NOW),
            ConfigurationSummaryResource(config_id="a", created_at=NOW),
        )
    )

    result = _invoke(client, "configs", "list", "--all")

    assert result.exit_code == 0, result.output
    client.iter_configs.assert_called_once_with(name=None, limit=None)
    client.list_configs.assert_not_called()
    assert result.stdout.index("config_id: b") < result.stdout.index("config_id: a")


def test_configs_versions_prints_latest_run_columns_and_pages(client: MagicMock) -> None:
    latest = RunReference(run_id="run-2", phase="planned", outcome=None, started_at=NOW)
    applied_at = NOW + timedelta(hours=1)
    applied = RunReference(run_id="run-1", phase="applied", outcome="succeeded", started_at=NOW, finished_at=applied_at)
    registered = _version_summary(2, latest_run=latest, latest_applied_run=applied).model_copy(
        update={"registered_by": "admin@example.com", "registration_reason": "first import"}
    )
    client.list_config_versions.return_value = ConfigurationVersionPage(
        items=(registered, _version_summary(1)),
        next_cursor="c1",
    )

    result = _invoke(client, "configs", "versions", "edge-sync", "--limit", "2")

    assert result.exit_code == 0, result.output
    client.list_config_versions.assert_called_once_with("edge-sync", limit=2)
    lines = result.stdout.splitlines()
    assert "registered_by: admin@example.com" in lines
    assert "registration_reason: first import" in lines
    assert "registered_by: <none>" in lines
    assert f"latest_run: run-2 phase=planned outcome=- started_at={NOW.isoformat()}" in lines
    # The applied run shows when its apply finished, not when its plan was created.
    assert f"latest_applied_run: run-1 phase=applied outcome=succeeded finished_at={applied_at.isoformat()}" in lines
    assert "latest_run: <none>" in lines
    assert "more versions: rerun with --all, or --limit up to 200" in result.stderr


def test_configs_versions_all_follows_cursors(client: MagicMock) -> None:
    client.iter_config_versions.return_value = iter((_version_summary(2), _version_summary(1)))

    result = _invoke(client, "configs", "versions", "edge-sync", "--all")

    assert result.exit_code == 0, result.output
    client.iter_config_versions.assert_called_once_with("edge-sync", limit=None)
    client.list_config_versions.assert_not_called()
    assert "registry_version: 2" in result.stdout
    assert "registry_version: 1" in result.stdout


# Each row names a command that takes a configuration and reads back the config_id it sent.
_RESOLVING_COMMANDS = [
    pytest.param(
        ("plan", "--config", "{config}", "--version", "1", "--no-wait"),
        lambda client: client.plan.call_args.args[0].config_id,
        lambda client: client.plan,
        id="plan",
    ),
    pytest.param(
        ("sync", "--config-id", "{config}", "--version", "1", "--no-wait"),
        lambda client: client.sync.call_args.args[0].config_id,
        lambda client: client.sync,
        id="sync",
    ),
    pytest.param(
        ("runs", "list", "--config", "{config}"),
        lambda client: client.list_runs.call_args.kwargs["config_id"],
        lambda client: client.list_runs,
        id="runs-list",
    ),
    pytest.param(
        ("configs", "versions", "{config}"),
        lambda client: client.list_config_versions.call_args.args[0],
        lambda client: client.list_config_versions,
        id="configs-versions",
    ),
]


def _configured(client: MagicMock) -> MagicMock:
    client.list_runs.return_value = RunPage(items=(), next_cursor=None)
    return client


def _named(*config_ids: str) -> Callable[..., Iterator[ConfigurationSummaryResource]]:
    return lambda **_kwargs: iter(
        ConfigurationSummaryResource(config_id=config_id, created_at=NOW, name="from netbox")
        for config_id in config_ids
    )


@pytest.mark.parametrize(("arguments", "sent_config", "target"), _RESOLVING_COMMANDS)
def test_configuration_identifier_found_is_used_without_a_name_lookup(
    client: MagicMock,
    arguments: tuple[str, ...],
    sent_config: Callable[[MagicMock], str],
    target: Callable[[MagicMock], MagicMock],
) -> None:
    result = _invoke(_configured(client), *(argument.format(config="edge-sync") for argument in arguments))

    assert result.exit_code == 0, result.output
    client.get_config.assert_called_once_with("edge-sync")
    client.iter_configs.assert_not_called()
    target(client).assert_called_once()
    assert sent_config(client) == "edge-sync"


@pytest.mark.parametrize(
    "not_found",
    [
        pytest.param(ConfigsAPIError(404, "configuration-not-found", "not-found", "unknown"), id="unknown-id"),
        pytest.param(ClientInputError("config_id"), id="not-an-identifier"),
    ],
)
@pytest.mark.parametrize(("arguments", "sent_config", "target"), _RESOLVING_COMMANDS)
def test_configuration_name_with_one_match_is_used(
    client: MagicMock,
    arguments: tuple[str, ...],
    sent_config: Callable[[MagicMock], str],
    target: Callable[[MagicMock], MagicMock],
    not_found: Exception,
) -> None:
    client.get_config.side_effect = not_found
    client.iter_configs.side_effect = _named("cfg-1")

    result = _invoke(_configured(client), *(argument.format(config="from netbox") for argument in arguments))

    assert result.exit_code == 0, result.output
    client.iter_configs.assert_called_once_with(name="from netbox")
    target(client).assert_called_once()
    assert sent_config(client) == "cfg-1"


@pytest.mark.parametrize(("arguments", "_sent_config", "target"), _RESOLVING_COMMANDS)
def test_configuration_name_with_several_matches_is_refused_with_the_ids(
    client: MagicMock,
    arguments: tuple[str, ...],
    _sent_config: Callable[[MagicMock], str],
    target: Callable[[MagicMock], MagicMock],
) -> None:
    client.get_config.side_effect = ConfigsAPIError(404, "configuration-not-found", "not-found", "unknown")
    client.iter_configs.side_effect = _named("cfg-1", "cfg-2")

    result = _invoke(_configured(client), *(argument.format(config="from netbox") for argument in arguments))

    assert result.exit_code == 2
    assert result.stderr.splitlines() == [
        "error: name 'from netbox' matches several configurations:",
        "cfg-1",
        "cfg-2",
    ]
    target(client).assert_not_called()


@pytest.mark.parametrize(("arguments", "_sent_config", "target"), _RESOLVING_COMMANDS)
def test_configuration_with_no_identifier_or_name_match_is_refused(
    client: MagicMock,
    arguments: tuple[str, ...],
    _sent_config: Callable[[MagicMock], str],
    target: Callable[[MagicMock], MagicMock],
) -> None:
    client.get_config.side_effect = ConfigsAPIError(404, "configuration-not-found", "not-found", "unknown")

    result = _invoke(_configured(client), *(argument.format(config="missing") for argument in arguments))

    assert result.exit_code == 2
    assert result.stderr.splitlines() == ["error: no configuration has the id or name 'missing'"]
    target(client).assert_not_called()


def test_configs_version_retry_after_a_rename_uses_the_printed_config_id(client: MagicMock, tmp_path: Path) -> None:
    """The name follows the newest version, so the first attempt can rename the configuration."""
    package = tmp_path / "package.yaml"
    package.write_text("name: renamed\n", encoding="utf-8")
    names = {"cfg-1": "from netbox"}

    def get_config(value: str) -> ConfigurationSummaryResource:
        if value not in names:
            raise ConfigsAPIError(404, "configuration-not-found", "not-found", "unknown")
        return ConfigurationSummaryResource(config_id=value, created_at=NOW, name=names[value])

    def iter_configs(*, name: str, **_kwargs: object) -> Iterator[ConfigurationSummaryResource]:
        return iter(
            ConfigurationSummaryResource(config_id=config_id, created_at=NOW, name=current)
            for config_id, current in names.items()
            if current == name
        )

    def stored_then_lost(*_args: object) -> RegisteredVersionResource:
        # The service stores the version, which renames the configuration, and the response is lost.
        names["cfg-1"] = "renamed"
        operation = "create_config_version"
        raise TransportError(operation)

    client.get_config.side_effect = get_config
    client.iter_configs.side_effect = iter_configs
    client.create_config_version.side_effect = stored_then_lost

    first = _invoke(client, "configs", "version", "from netbox", str(package))
    key = client.create_config_version.call_args.args[2]
    by_name = _invoke(client, "configs", "version", "from netbox", str(package), "--idempotency-key", key)
    client.create_config_version.side_effect = None
    by_id = _invoke(client, "configs", "version", "cfg-1", str(package), "--idempotency-key", key)

    assert first.exit_code == 1
    assert "config_id: cfg-1" in first.stdout
    assert f"idempotency_key: {key}" in first.stdout
    assert by_name.exit_code == 2
    assert by_name.stderr.splitlines() == [
        "error: no configuration has the id or name 'from netbox'",
        "hint: retry with the config_id printed by the first attempt; "
        "the name follows the newest version and can change",
    ]
    assert by_id.exit_code == 0, by_id.output
    assert client.create_config_version.call_count == 2
    assert client.create_config_version.call_args.args[0] == "cfg-1"
    assert client.create_config_version.call_args.args[2] == key


def test_configuration_lookup_errors_other_than_not_found_are_reported(client: MagicMock) -> None:
    client.get_config.side_effect = ConfigsAPIError(403, "forbidden", "authorization", "denied")

    result = _invoke(client, "configs", "versions", "edge-sync")

    assert result.exit_code == 1
    assert "status: 403" in result.output
    client.iter_configs.assert_not_called()


# `_client_errors` in `infrahub_sync/cli.py` exits with 2 for every `client-input` error.
CLIENT_INPUT_EXIT = 2
SERVICE_URL_HINT = "hint: set INFRAHUB_SYNC_API_URL or pass --api-url to an absolute http or https URL of the Sync API"
BEARER_HINT = "hint: set INFRAHUB_SYNC_API_TOKEN to a Sync API bearer token"


@pytest.mark.parametrize(
    ("api_url", "token", "argument", "hint"),
    [
        (None, "token-1", "service_url", SERVICE_URL_HINT),
        ("ftp://x", "token-1", "service_url", SERVICE_URL_HINT),
        ("http://user:pw@h", "token-1", "service_url", SERVICE_URL_HINT),
        ("http://sync.example", None, "token", BEARER_HINT),
    ],
    ids=["url-unset", "url-not-http", "url-with-credentials", "token-unset"],
)
def test_a_missing_or_invalid_address_prints_how_to_set_it(
    api_url: str | None, token: str | None, argument: str, hint: str
) -> None:
    result = RUNNER.invoke(
        app,
        ["configs", "list"],
        env={"INFRAHUB_SYNC_API_URL": api_url, "INFRAHUB_SYNC_API_TOKEN": token},
    )

    assert result.exit_code == CLIENT_INPUT_EXIT
    assert result.output.splitlines()[:3] == ["error: client-input", f"argument: {argument}", hint]


def test_a_client_input_error_for_another_argument_prints_no_hint(client: MagicMock) -> None:
    result = _invoke(
        client,
        "sync",
        "--config-id",
        "edge-sync",
        "--version",
        "1",
        "--reason",
        "wait for sync",
        "--wait-timeout",
        "0",
    )

    assert result.exit_code == CLIENT_INPUT_EXIT
    assert "argument: wait_timeout" in result.output
    assert "hint:" not in result.output


def _log_line(message: str, *, stage: str = "apply", level: str = "INFO") -> RunLogLine:
    return RunLogLine(stage=stage, attempt=1, timestamp=NOW, level=level, source="infrahub_sync", message=message)


def _log_page(*lines: RunLogLine, offset: int = 0, has_more: bool = False, run_phase: str = "applied") -> RunLogPage:
    return RunLogPage(
        run_id="service-run-1", items=lines, limit=500, offset=offset, has_more=has_more, run_phase=run_phase
    )


@pytest.fixture
def poll_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record each poll interval the CLI asks for instead of waiting it."""
    sleeps: list[float] = []
    monkeypatch.setattr("infrahub_sync.cli.sleep", sleeps.append)
    return sleeps


def test_runs_logs_prints_one_tab_separated_row_per_line(client: MagicMock, poll_sleeps: list[float]) -> None:
    client.read_run_logs.return_value = _log_page(
        _log_line("loaded 3 devices", stage="plan"),
        _log_line("wrote\t1 device\x1b[2J", level="ERROR"),
    )

    result = _invoke(client, "runs", "logs", "service-run-1")

    assert result.exit_code == 0, result.output
    client.read_run_logs.assert_called_once_with("service-run-1", stage=None, offset=0)
    assert result.stdout.splitlines() == [
        f"{NOW.isoformat()}\tplan\tINFO\tloaded 3 devices",
        f"{NOW.isoformat()}\tapply\tERROR\twrote\\t1 device\\x1b[2J",
    ]
    assert "prefect" not in result.output.lower()
    assert not poll_sleeps


def test_runs_logs_passes_the_stage_and_reads_every_page(client: MagicMock, poll_sleeps: list[float]) -> None:
    client.read_run_logs.side_effect = [
        _log_page(_log_line("first"), has_more=True),
        _log_page(_log_line("second"), offset=1),
    ]

    result = _invoke(client, "runs", "logs", "service-run-1", "--stage", "apply")

    assert result.exit_code == 0, result.output
    assert [call.kwargs for call in client.read_run_logs.call_args_list] == [
        {"stage": "apply", "offset": 0},
        {"stage": "apply", "offset": 1},
    ]
    assert [line.split("\t")[-1] for line in result.stdout.splitlines()] == ["first", "second"]
    assert not poll_sleeps


def test_runs_logs_follow_prints_each_line_once_and_stops_after_the_run_ends(
    client: MagicMock, poll_sleeps: list[float]
) -> None:
    client.read_run_logs.side_effect = [
        _log_page(_log_line("one"), _log_line("two"), run_phase="accepted"),
        _log_page(offset=2, run_phase="accepted"),
        _log_page(_log_line("three"), offset=2, run_phase="applied"),
        _log_page(offset=3, run_phase="applied"),
        _log_page(offset=3, run_phase="applied"),
        _log_page(offset=3, run_phase="applied"),
    ]
    client.get_run.side_effect = [_run(operation="apply"), _run(operation="apply", terminal=("completed", "succeeded"))]

    result = _invoke(client, "runs", "logs", "service-run-1", "--follow")

    assert result.exit_code == 0, result.output
    assert [call.kwargs["offset"] for call in client.read_run_logs.call_args_list] == [0, 2, 2, 3, 3, 3]
    assert [line.split("\t")[-1] for line in result.stdout.splitlines()] == ["one", "two", "three"]
    # Once the run has ended, three empty reads one interval apart stop the command.
    assert poll_sleeps == [2.0] * 5


def test_runs_logs_follow_prints_a_line_stored_after_the_run_ended(client: MagicMock, poll_sleeps: list[float]) -> None:
    # The orchestrator stores an execution's last lines in batches, after Sync records that it ended.
    client.read_run_logs.side_effect = [
        _log_page(_log_line("applying"), run_phase="planned"),
        _log_page(offset=1, run_phase="applied"),
        _log_page(_log_line("service run service-run-1 stage=apply outcome=applied"), offset=1, run_phase="applied"),
        _log_page(offset=2, run_phase="applied"),
        _log_page(offset=2, run_phase="applied"),
        _log_page(offset=2, run_phase="applied"),
    ]
    client.get_run.return_value = _run(operation="apply", terminal=("completed", "succeeded"))

    result = _invoke(client, "runs", "logs", "service-run-1", "--follow")

    assert result.exit_code == 0, result.output
    assert [line.split("\t")[-1] for line in result.stdout.splitlines()] == [
        "applying",
        "service run service-run-1 stage=apply outcome=applied",
    ]
    client.get_run.assert_called_once_with("service-run-1")
    assert poll_sleeps == [2.0] * 5


def test_runs_logs_follow_keeps_reading_while_an_apply_of_a_planned_run_runs(
    client: MagicMock, poll_sleeps: list[float]
) -> None:
    # An apply keeps the run's phase `planned` until it ends; the apply execution's verdict decides.
    client.read_run_logs.side_effect = [
        _log_page(_log_line("planned 3 changes", stage="plan"), run_phase="planned"),
        _log_page(offset=1, run_phase="planned"),
        _log_page(_log_line("wrote 3 changes"), offset=1, run_phase="planned"),
        _log_page(offset=2, run_phase="applied"),
        _log_page(offset=2, run_phase="applied"),
        _log_page(offset=2, run_phase="applied"),
    ]
    client.get_run.side_effect = [_run(operation="apply"), _run(operation="apply", terminal=("completed", "succeeded"))]

    result = _invoke(client, "runs", "logs", "service-run-1", "--follow")

    assert result.exit_code == 0, result.output
    assert [line.split("\t")[-1] for line in result.stdout.splitlines()] == ["planned 3 changes", "wrote 3 changes"]
    assert client.get_run.call_count == 2
    assert poll_sleeps == [2.0] * 5


def test_runs_logs_follow_stops_when_the_execution_ended_without_a_new_phase(
    client: MagicMock, poll_sleeps: list[float]
) -> None:
    # A verify run keeps the phase `accepted` after it ends; its execution verdict says it ended.
    client.read_run_logs.side_effect = [
        _log_page(_log_line("checked"), run_phase="accepted"),
        _log_page(offset=1, run_phase="accepted"),
        _log_page(offset=1, run_phase="accepted"),
        _log_page(offset=1, run_phase="accepted"),
    ]
    client.get_run.return_value = _run(terminal=("completed", "succeeded"))

    result = _invoke(client, "runs", "logs", "service-run-1", "--follow")

    assert result.exit_code == 0, result.output
    assert [line.split("\t")[-1] for line in result.stdout.splitlines()] == ["checked"]
    client.get_run.assert_called_once_with("service-run-1")
    assert poll_sleeps == [2.0] * 3


@pytest.mark.parametrize(("status", "code"), [(410, "log-expired"), (503, "log-unavailable")])
def test_runs_logs_prints_an_unreadable_log_as_an_api_error(
    client: MagicMock, poll_sleeps: list[float], status: int, code: str
) -> None:
    client.read_run_logs.side_effect = APIError(status, code, run_id="service-run-1")

    result = _invoke(client, "runs", "logs", "service-run-1", "--follow")

    assert result.exit_code == 1
    assert result.stderr.splitlines()[:4] == [
        "error: api",
        f"status: {status}",
        f"code: {code}",
        "run_id: service-run-1",
    ]
    assert not result.stdout
    assert not poll_sleeps


def _event(event_id: str, *, primary: tuple[str, str] | None = ("run", "service-run-1")) -> EventResource:
    return EventResource(
        event_id=event_id,
        event_type="infrahub_sync.run.planned",
        occurred_at=NOW,
        actor="operator",
        operation="plan",
        outcome="succeeded",
        primary_resource=EventPrimaryResource(kind=primary[0], id=primary[1]) if primary is not None else None,
    )


def test_events_filters_by_run_configuration_name_and_type_and_prints_one_row_per_event(client: MagicMock) -> None:
    client.get_config.side_effect = APIError(404, "not-found")
    client.iter_configs.side_effect = lambda **_kwargs: iter(
        (ConfigurationSummaryResource(config_id="cfg-1", created_at=NOW, name="from-netbox"),)
    )
    client.iter_events.return_value = iter((_event("event-1"), _event("event-2", primary=None)))

    result = _invoke(
        client,
        "events",
        "--run-id",
        "service-run-1",
        "--config-id",
        "from-netbox",
        "--type",
        "infrahub_sync.run.planned",
        "--type",
        "infrahub_sync.run.failed",
    )

    assert result.exit_code == 0, result.output
    client.iter_events.assert_called_once_with(
        run_id="service-run-1",
        config_id="cfg-1",
        event_types=("infrahub_sync.run.planned", "infrahub_sync.run.failed"),
    )
    assert result.stdout.splitlines() == [
        f"{NOW.isoformat()}\tinfrahub_sync.run.planned\trun:service-run-1\toperator\tsucceeded",
        f"{NOW.isoformat()}\tinfrahub_sync.run.planned\t-\toperator\tsucceeded",
    ]


def test_events_follow_prints_each_new_event_once(client: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    polls: list[float] = []

    def stop_after_two_polls(seconds: float) -> None:
        polls.append(seconds)
        if len(polls) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("infrahub_sync.cli.sleep", stop_after_two_polls)
    client.list_events.side_effect = [
        EventPage(items=(_event("event-1"), _event("event-2")), limit=50, offset=0, has_more=False),
        EventPage(items=(_event("event-3", primary=("config", "cfg-1")),), limit=50, offset=2, has_more=False),
    ]

    result = _invoke(client, "events", "--follow")

    assert result.exit_code == 130
    assert [call.kwargs["offset"] for call in client.list_events.call_args_list] == [0, 2]
    assert [line.split("\t")[2] for line in result.stdout.splitlines()] == [
        "run:service-run-1",
        "run:service-run-1",
        "config:cfg-1",
    ]
    assert polls == [2.0, 2.0]
    client.iter_events.assert_not_called()
