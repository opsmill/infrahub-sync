"""What the CLI prints for a stopped apply and for a second apply of its plan (spec 020 T059).

FR-021: `runs show` prints how many operations the stopped apply wrote out of the total, and the
operation that failed with its action, kind and identity (contracts/cli.md "Cause of a failed or
stopped run").

FR-023: a second apply on that run is refused by the Sync API with the existing
`run-execution-conflict` code (spec decision 4). The CLI keeps the lines it prints for any API
refusal and adds the next step: `verify` and `runs resume` for a run that can be resumed (spec
022), otherwise create and review a new plan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from infrahub_sync.client import APIError
from tests.cli.test_client_commands import (
    CHECKSUM,
    _invoke,
    _restore_package_logging,  # noqa: F401 - the CLI suite's autouse fixture, used by name.
    _run,
    client,  # noqa: F401 - the fixture is used by name.
)

if TYPE_CHECKING:
    from unittest.mock import MagicMock

_RUN_ID = "service-run-1"


def _stopped_apply_failure() -> dict[str, Any]:
    """The apply record of an apply that wrote 692 of 2291 operations and stopped at the next one."""
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
        "infrahub_message": "planned is not a valid value for status",
        "cause": "Infrahub rejected a write to DcimDevice attribute status (ATTRIBUTE_CONSTRAINT_VIOLATION)",
        "next_action": "Fix the cause, then create and review a new plan with `infrahub-sync plan`.",
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


def test_runs_show_prints_what_the_stopped_apply_wrote_and_the_failed_operation(client: MagicMock) -> None:  # noqa: F811
    resource = _run(operation="apply", phase="apply-interrupted", outcome="ambiguous")
    stopped = resource.run.model_copy(update={"results": {"apply_failure": _stopped_apply_failure()}})
    client.get_run.return_value = resource.model_copy(update={"run": stopped})

    result = _invoke(client, "runs", "show", _RUN_ID)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert "written: 692 of 2291 operations" in lines
    assert (
        "failed operation: op_518aada155d790ad update DcimDevice location=r1 name=rtr1 "
        "(may have written part of its change)"
    ) in lines


def test_a_second_apply_prints_the_conflict_refusal_and_the_next_step(client: MagicMock) -> None:  # noqa: F811
    client.apply.side_effect = APIError(409, "run-execution-conflict", run_id=_RUN_ID)

    result = _invoke(client, "apply", _RUN_ID, "--expected-checksum", CHECKSUM, "--reason", "apply again")

    assert result.exit_code == 1, result.output
    lines = result.output.splitlines()
    # The lines printed today for an API refusal stay.
    assert "error: api" in lines
    assert "status: 409" in lines
    assert "code: run-execution-conflict" in lines
    # FR-023: the next step.
    hints = [line for line in lines if line.startswith("hint: ")]
    assert len(hints) == 1, result.output
    assert "create and review a new plan" in hints[0]
    assert "`infrahub-sync plan`" in hints[0]
    # Spec 022: a run that can be resumed continues with `verify`, then `runs resume`.
    assert (
        "When it prints `resumable: yes`, run `infrahub-sync verify RUN_ID`, then `infrahub-sync runs resume RUN_ID"
        in hints[0]
    )
    client.wait_for_run.assert_not_called()
