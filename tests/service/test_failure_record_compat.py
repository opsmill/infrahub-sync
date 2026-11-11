"""The keys of a failure record keep their values, and an older record prints as today (spec 019 T050).

FR-014: the `results.<stage>_failure` record of spec 017 gains fields, and the keys it holds
today keep their meaning. These tests pin the values the worker records today for the keys it
records today (a key absent today, such as the `cause` of a rejected write, may be added),
and the lines the CLI prints for a record that has none of the spec 019 fields, as an older Sync
API writes it. They pass before and after spec 019 User Story 3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.client import ResultsResource
from infrahub_sync.client.models import StageFailureResource
from infrahub_sync.runtime_schema.errors import DestinationBranchNotFoundError
from tests.cli.test_client_commands import (
    CHECKSUM,
    _invoke,
    _restore_package_logging,  # noqa: F401 - the CLI suite's autouse fixture, used by name.
    _run,
    _terminal,
    client,  # noqa: F401 - the fixture is used by name.
)
from tests.service.failure_cases import APPLIED_OPERATION, CASES, FAILED_OPERATION, NEUTRAL_TEXT

if TYPE_CHECKING:
    from collections.abc import Callable
    from unittest.mock import MagicMock

_PERMISSION = "object:*:DcimDevice:create:allow_all"
_APPLY_NEXT_ACTION = (
    "Nothing was rolled back: the operations applied before this one stay written, and this one may have written "
    "part of its own change. Resolve the underlying error at the destination, then re-run `plan` and apply the new "
    "plan — re-applying an operation that already succeeded, in whole or in part, converges rather than "
    "duplicating."
)

# Today's values of the existing keys, for each failure.
_TODAY: dict[str, dict[str, Any]] = {
    "permission-missing": {
        "stage": "plan",
        "outcome": "failed",
        "error_type": "SideLoadError",
        "cause_type": "AuthenticationError",
        "infrahub_error": {"problem": "missing_permission", "http_status": 403, "permissions": [_PERMISSION]},
        "cause": f"a missing permission: the import account needs the permission `{_PERMISSION}` "
        "(AuthenticationError, HTTP 403)",
    },
    "rejected-write-with-code": {
        "stage": "apply",
        "outcome": "failed",
        "error_type": "OperationApplyFailedError",
        "cause_type": "GraphQLError",
        "next_action": _APPLY_NEXT_ACTION,
        "applied_operations": [APPLIED_OPERATION],
        "skipped_delete_operations": [],
        "skipped_delete_count": 0,
        "failed_operation": FAILED_OPERATION,
        "failed_operation_wrote": None,
        "may_have_partially_written": True,
    },
    "lineage-refused": {
        "stage": "plan",
        "outcome": "failed",
        "error_type": "NamedLineageNodeNotFoundError",
        "cause_type": "RuntimeError",
        "cause": "The setting `source` names `CoreAccount` `netbox`, which does not exist.",
        "next_action": "Create `CoreAccount` `netbox` in the destination, or change the setting `source` to name a "
        "node that exists and create a new plan.",
    },
    "source-unreachable": {
        "stage": "plan",
        "outcome": "failed",
        "error_type": "SideLoadError",
        "cause_type": "ConnectionError",
    },
}


@pytest.fixture
def failure_evidence() -> Callable[..., dict[str, Any]]:
    """`_failure_evidence` of the service worker; skipped where Prefect is not installed."""
    pytest.importorskip("prefect")
    from infrahub_sync.service import flow

    return flow._failure_evidence


@pytest.mark.parametrize("case_id", sorted(_TODAY))
def test_the_existing_keys_keep_their_values(failure_evidence: Callable[..., dict[str, Any]], case_id: str) -> None:
    case = next(case for case in CASES if case.case_id == case_id)

    evidence = failure_evidence(case.stage, case.build(NEUTRAL_TEXT), outcome="failed", record=None, secrets=())

    # Only the keys recorded today: spec 019 may add a `cause` where today's record has none.
    assert {key: evidence.get(key) for key in _TODAY[case_id]} == _TODAY[case_id]


def test_a_missing_branch_keeps_its_cause_and_next_action(failure_evidence: Callable[..., dict[str, Any]]) -> None:
    evidence = failure_evidence(
        "plan", DestinationBranchNotFoundError(branch="demo-test"), outcome="failed", record=None, secrets=()
    )

    expected = {
        "stage": "plan",
        "outcome": "failed",
        "error_type": "DestinationBranchNotFoundError",
        "cause_type": None,
        "cause": "Branch `demo-test` does not exist in Infrahub.",
        "next_action": "Create it, or start the run with `--branch <existing branch>`.",
    }
    assert {key: evidence.get(key) for key in expected} == expected


# A record written by a Sync API before spec 019: none of the new fields.
_OLDER_RECORD = {
    "stage": "apply",
    "outcome": "ambiguous",
    "error_type": "OperationApplyFailedError",
    "cause_type": "GraphQLError",
    "cause": "Infrahub said no",
    "next_action": "Re-run `plan` and apply the new plan.",
    "applied_operations": [APPLIED_OPERATION],
    "failed_operation": FAILED_OPERATION,
    "failed_operation_wrote": None,
    "may_have_partially_written": True,
}
_OLDER_LINES = [
    "apply failed: OperationApplyFailedError",
    "cause: Infrahub said no",
    "hint: Re-run `plan` and apply the new plan.",
]


def test_an_older_record_validates_with_the_new_fields_empty() -> None:
    record = StageFailureResource.model_validate(_OLDER_RECORD)

    assert record.failure_type is None
    assert record.system is None
    assert record.operations_total is None


def test_the_results_resource_reads_an_older_record_and_skips_a_malformed_one() -> None:
    resource = ResultsResource(
        run_id="service-run-1",
        results={"apply_failure": _OLDER_RECORD, "plan_failure": {"error_code": "x" * 500}, "verify_failure": "text"},
    )

    older = resource.stage_failure("apply")
    assert older is not None
    assert older.failure_type is None
    assert resource.stage_failure("plan") is None
    assert resource.stage_failure("verify") is None
    assert resource.stage_failure("sync") is None


def _failure_lines(output: str) -> list[str]:
    """The lines from `apply failed:` to the end of the output, without the run-terminal error."""
    lines = output.splitlines()
    start = lines.index("apply failed: OperationApplyFailedError")
    return [
        line
        for line in lines[start:]
        if not line.startswith(("error: ", "run_id: ", "terminal_", "phase: ", "outcome: "))
    ]


def test_an_older_record_prints_as_today_after_a_failed_apply(client: MagicMock) -> None:  # noqa: F811
    client.wait_for_run.side_effect = _terminal("apply-interrupted")
    client.get_results.return_value = ResultsResource(run_id="service-run-1", results={"apply_failure": _OLDER_RECORD})

    result = _invoke(
        client, "apply", "service-run-1", "--expected-checksum", CHECKSUM, "--reason", "apply reviewed plan"
    )

    assert result.exit_code == 1
    assert _failure_lines(result.output) == _OLDER_LINES


def test_an_older_record_prints_as_today_in_runs_show(client: MagicMock) -> None:  # noqa: F811
    resource = _run(operation="apply", phase="apply-interrupted", outcome="ambiguous")
    failed_run = resource.run.model_copy(update={"results": {"apply_failure": _OLDER_RECORD}})
    client.get_run.return_value = resource.model_copy(update={"run": failed_run})

    result = _invoke(client, "runs", "show", "service-run-1")

    assert result.exit_code == 0, result.output
    assert _failure_lines(result.stdout) == _OLDER_LINES


def test_an_older_record_without_a_next_action_still_gets_its_fixed_hint(client: MagicMock) -> None:  # noqa: F811
    client.wait_for_run.side_effect = _terminal("apply-failed")
    client.get_results.return_value = ResultsResource(
        run_id="service-run-1",
        results={
            "apply_failure": {
                "stage": "apply",
                "outcome": "failed",
                "error_type": "RegisteredPlanVerificationError",
                "recovery_action": "rebuild",
            }
        },
    )

    result = _invoke(
        client, "apply", "service-run-1", "--expected-checksum", CHECKSUM, "--reason", "apply reviewed plan"
    )

    lines = result.output.splitlines()
    start = lines.index("apply failed: RegisteredPlanVerificationError")
    assert lines[start + 1].startswith("hint: ")
    assert not any(line.startswith(("failure type:", "written:", "failed operation:")) for line in lines)
