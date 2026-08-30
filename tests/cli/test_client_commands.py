"""Typer coverage for the SyncClient-backed command surface."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from infrahub_sync.cli import app
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
    PlanResource,
    ProtocolError,
    PublicExecutionLink,
    PublicRunResource,
    RegisteredConfigurationResource,
    RegisteredVersionResource,
    RunResource,
    RunTerminalError,
    RunWaitTimeoutError,
    SyncClient,
    TransportError,
    ValidationFindingResource,
    ValidationReportResource,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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


def _run(*, operation: Literal["plan", "sync", "apply"] = "plan", phase: str = "accepted") -> RunResource:
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
        terminal_at=None,
        terminal_state=None,
        terminal_outcome=None,
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
            prefect_executions=(PublicExecutionLink(flow_run_id="flow-service-1", purpose=operation, attempt=1),),
        ),
        orchestration=(execution,),
    )


def _plan() -> PlanResource:
    return PlanResource(
        run_id="service-run-1",
        checksum=CHECKSUM,
        checksum_ok=True,
        verification_notes=("reviewed from the service artifact",),
        summary={
            "by_action": {"create": 1, "delete": 1},
            "by_kind": {"Device": 1, "Site": 1},
            "total": 2,
            "delete_operations_computed": True,
            "deletes_not_executed": 1,
        },
        operations=(
            {
                "operation_id": "op-create",
                "action": "create",
                "kind": "Device",
                "identity": {"name": "edge-01"},
                "tier": 0,
                "payload": {"name": "edge-01"},
                "relationships": [],
            },
            {
                "operation_id": "op-delete",
                "action": "delete",
                "kind": "Site",
                "identity": {"name": "retired"},
                "tier": 0,
                "payload": None,
                "relationships": None,
            },
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
    return injected


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


def test_runs_plan_filters_detail_and_marks_deletes_not_executed(client: MagicMock) -> None:
    result = _invoke(client, "runs", "plan", "service-run-1", "--detail", "--kind", "Site")

    assert result.exit_code == 0, result.output
    client.get_plan.assert_called_once_with("service-run-1")
    assert f"plan_checksum: {CHECKSUM}" in result.output
    assert "op-delete" in result.output
    assert "(not executed)" in result.output
    assert "op-create" not in result.output


def test_runs_plan_unmatched_kind_is_typed_input_error(client: MagicMock) -> None:
    result = _invoke(client, "runs", "plan", "service-run-1", "--detail", "--kind", "Typo")

    assert result.exit_code == 2
    assert "error: client-input" in result.output
    assert "argument: kind" in result.output


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
