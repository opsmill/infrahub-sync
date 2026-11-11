"""The cause of a failed run, as the CLI prints it (spec 019 T049, FR-015, contracts/cli.md).

`plan`, `verify`, `apply`, `sync` and `runs show` keep the lines they print today and add the
fields of the failure record between `<stage> failed:` and `hint:`. Each value goes through the
existing `_display` guard, so a control character is printed escaped.

The records are the examples of contracts/sync-api.md "Failure record". contracts/cli.md gives
the lines of a rejected write; the lines of a source failure follow the same pattern. The
contract gives no line for `record` and `field`, so they are not checked here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.client import ResultsResource
from tests.cli.test_client_commands import (
    CHECKSUM,
    _invoke,
    _restore_package_logging,  # noqa: F401 - the CLI suite's autouse fixture, used by name.
    _run,
    _terminal,
    client,  # noqa: F401 - the fixture is used by name.
)

if TYPE_CHECKING:
    from unittest.mock import MagicMock

_US3_PENDING = pytest.mark.xfail(strict=True, raises=AssertionError, reason="spec 019 US3 not implemented")

_REJECTED_WRITE_CAUSE = "Infrahub rejected a write to DcimDevice attribute status (ATTRIBUTE_CONSTRAINT_VIOLATION)"
_REJECTED_WRITE_NEXT_ACTION = "Fix the cause, then create and review a new plan with `infrahub-sync plan`."
_REFUSED_CAUSE = "The source (netbox) rejected the token"
_REFUSED_NEXT_ACTION = "Check the credential that the configuration references for netbox, then plan again."


def _rejected_write(*, infrahub_message: str = "planned is not a valid value for status") -> dict[str, Any]:
    """The apply record of a rejected write: 692 of 2291 operations written."""
    return {
        "stage": "apply",
        "outcome": "ambiguous",
        "error_type": "OperationApplyFailedError",
        "cause_type": "GraphQLError",
        "failure_type": "destination_rejected_write",
        "system": {"role": "destination", "adapter": "infrahub"},
        "error_code": "ATTRIBUTE_CONSTRAINT_VIOLATION",
        "node_kind": "DcimDevice",
        "attribute": "status",
        "infrahub_message": infrahub_message,
        "cause": _REJECTED_WRITE_CAUSE,
        "next_action": _REJECTED_WRITE_NEXT_ACTION,
        "applied_operations": [f"op_{index:016x}" for index in range(692)],
        "failed_operation": "op_518aada155d790ad",
        "failed_operation_wrote": None,
        "may_have_partially_written": True,
        "operations_total": 2291,
        "failed_operation_detail": {
            "action": "update",
            "kind": "DcimDevice",
            "identity": {"location": "r1", "name": "rtr1"},
        },
    }


_REJECTED_WRITE_LINES = [
    "apply failed: OperationApplyFailedError",
    "failure type: destination_rejected_write (destination infrahub)",
    f"cause: {_REJECTED_WRITE_CAUSE}",
    "Infrahub message: planned is not a valid value for status",
    "written: 692 of 2291 operations",
    "failed operation: op_518aada155d790ad update DcimDevice location=r1 name=rtr1 (may have written part of its change)",
    f"hint: {_REJECTED_WRITE_NEXT_ACTION}",
]


def _token_refused(stage: str) -> dict[str, Any]:
    """The record of a source that refused the token."""
    return {
        "stage": stage,
        "outcome": "failed",
        "error_type": "RunExecutionError",
        "cause_type": "SideLoadError",
        "failure_type": "token_rejected",
        "system": {"role": "source", "adapter": "netbox"},
        "error_code": None,
        "node_kind": None,
        "attribute": None,
        "infrahub_message": None,
        "cause": _REFUSED_CAUSE,
        "next_action": _REFUSED_NEXT_ACTION,
    }


def _token_lines(stage: str) -> list[str]:
    return [
        f"{stage} failed: RunExecutionError",
        "failure type: token_rejected (source netbox)",
        f"cause: {_REFUSED_CAUSE}",
        f"hint: {_REFUSED_NEXT_ACTION}",
    ]


def _block(output: str, stage: str) -> list[str]:
    """The lines from `<stage> failed:` to the first `hint:` after it."""
    lines = output.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith(f"{stage} failed: ")]
    assert starts, f"no `{stage} failed:` line in:\n{output}"
    start = starts[0]
    ends = [index for index, line in enumerate(lines) if index > start and line.startswith("hint: ")]
    assert ends, f"no `hint:` line after `{stage} failed:` in:\n{output}"
    return lines[start : ends[0] + 1]


_COMMANDS: dict[str, tuple[str, ...]] = {
    "plan": ("plan", "--config-id", "edge-sync", "--version", "1", "--reason", "review"),
    "sync": ("sync", "--config-id", "edge-sync", "--version", "1", "--reason", "sync now"),
    "verify": ("verify", "service-run-1", "--reason", "recheck"),
    "apply": ("apply", "service-run-1", "--expected-checksum", CHECKSUM, "--reason", "apply reviewed plan"),
}


def _failed_command(client: MagicMock, stage: str, failure: dict[str, Any]) -> str:  # noqa: F811 - the imported fixture.
    client.wait_for_run.side_effect = _terminal(f"{stage}-failed")
    client.get_results.return_value = ResultsResource(run_id="service-run-1", results={f"{stage}_failure": failure})
    result = _invoke(client, *_COMMANDS[stage])
    assert result.exit_code == 1, result.output
    return result.output


@pytest.mark.parametrize("stage", [pytest.param(stage, marks=_US3_PENDING) for stage in ("plan", "sync", "verify")])
def test_a_failed_stage_prints_the_failure_type_and_system_before_the_hint(
    client: MagicMock,  # noqa: F811 - the imported fixture.
    stage: str,
) -> None:
    output = _failed_command(client, stage, _token_refused(stage))

    assert _block(output, stage) == _token_lines(stage)


@_US3_PENDING
def test_a_stopped_apply_prints_the_rejected_write_and_what_it_wrote(client: MagicMock) -> None:  # noqa: F811
    output = _failed_command(client, "apply", _rejected_write())

    assert _block(output, "apply") == _REJECTED_WRITE_LINES


@_US3_PENDING
def test_runs_show_prints_the_same_lines(client: MagicMock) -> None:  # noqa: F811 - the imported fixture.
    resource = _run(operation="apply", phase="apply-interrupted", outcome="ambiguous")
    failed_run = resource.run.model_copy(update={"results": {"apply_failure": _rejected_write()}})
    client.get_run.return_value = resource.model_copy(update={"run": failed_run})

    result = _invoke(client, "runs", "show", "service-run-1")

    assert result.exit_code == 0, result.output
    assert _block(result.stdout, "apply") == _REJECTED_WRITE_LINES


@_US3_PENDING
def test_infrahub_message_is_printed_with_its_control_characters_made_visible(
    client: MagicMock,  # noqa: F811 - the imported fixture.
) -> None:
    output = _failed_command(client, "apply", _rejected_write(infrahub_message="planned\x1b[2J is not valid"))

    assert "\x1b" not in output
    assert "Infrahub message: planned\\x1b[2J is not valid" in _block(output, "apply")
