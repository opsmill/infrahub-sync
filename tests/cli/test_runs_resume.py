"""`infrahub-sync runs resume` and the progress lines of `runs show` (spec 022 T023, contracts/cli.md).

- `runs resume` sends `confirm_writes=true` itself, the checksum, the left-out confirmation and
  the optional reason, and prints the run, the position it starts after and the new execution;
- a `verify-required` refusal prints the error lines and `hint: run infrahub-sync verify RUN_ID first`;
- an `approval-required` refusal prints `hint: run infrahub-sync approve RUN_ID --expected-checksum CHECKSUM first`;
- refusals and failures exit with the codes `apply` uses;
- `runs show` prints the progress, resumable, per-kind and resume lines, and nothing from the plan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest

from infrahub_sync.client import (
    APIError,
    ApplyProgressKindResource,
    ApplyProgressResource,
    ClientInputError,
    ResultsResource,
    ResumeResource,
    RunResource,
    RunTerminalError,
)
from tests.cli.test_client_commands import (
    CHECKSUM,
    _invoke,
    _plan,
    _restore_package_logging,  # noqa: F401 - the CLI suite's autouse fixture, used by name.
    _run,
    client,  # noqa: F401 - the fixture is used by name.
)

if TYPE_CHECKING:
    from unittest.mock import MagicMock

_RUN_ID = "service-run-1"
_SAVED_AT = datetime(2026, 11, 12, 10, 15, tzinfo=timezone.utc)
_REQUESTED_AT = datetime(2026, 11, 12, 11, 2, tzinfo=timezone.utc)


def _interrupted(*, verify_required: bool, resumes: tuple[ResumeResource, ...] = ()) -> RunResource:
    run = _run(operation="apply", phase="interrupted", outcome="ambiguous", reconciliation_required=True)
    return run.model_copy(
        update={
            "apply_progress": ApplyProgressResource(
                position=5999,
                saved_at=_SAVED_AT,
                resumable=True,
                verify_required=verify_required,
                by_kind=(
                    ApplyProgressKindResource(kind="DcimDevice", committed=1200, uncertain=0, not_reached=0),
                    ApplyProgressKindResource(kind="DcimInterface", committed=4800, uncertain=500, not_reached=3500),
                ),
            ),
            "resumes": resumes,
        }
    )


def _resume_arguments(*extra: str) -> tuple[str, ...]:
    return ("runs", "resume", _RUN_ID, "--expected-checksum", CHECKSUM, *extra)


def test_runs_resume_sends_the_request_and_prints_the_start_position(client: MagicMock) -> None:  # noqa: F811
    client.resume.return_value = _interrupted(verify_required=False)

    result = _invoke(client, *_resume_arguments("--reason", "worker restarted", "--confirm-left-out", "--no-wait"))

    assert result.exit_code == 0, result.output
    run_id, request, key = client.resume.call_args.args
    assert run_id == _RUN_ID
    assert (request.expected_checksum, request.confirm_writes, request.confirm_left_out, request.reason) == (
        CHECKSUM,
        True,
        True,
        "worker restarted",
    )
    assert f"idempotency_key: {key}" in result.output
    assert f"run_id: {_RUN_ID}" in result.output
    assert "flow_run_id: flow-service-1" in result.output
    assert "start_after: 5999" in result.output
    client.wait_for_run.assert_not_called()


def test_runs_resume_without_a_reason_sends_none(client: MagicMock) -> None:  # noqa: F811
    client.resume.return_value = _interrupted(verify_required=False)

    result = _invoke(client, *_resume_arguments("--no-wait"))

    assert result.exit_code == 0, result.output
    _run_id, request, _key = client.resume.call_args.args
    assert (request.reason, request.confirm_left_out) == (None, False)


def test_runs_resume_waits_and_prints_the_progress_of_the_finished_run(client: MagicMock) -> None:  # noqa: F811
    client.resume.return_value = _interrupted(verify_required=False)
    finished = _run(operation="apply", phase="applied", outcome="applied", terminal=("completed", "succeeded"))
    client.wait_for_run.side_effect = lambda _accepted, **_kwargs: finished.model_copy(
        update={
            "apply_progress": ApplyProgressResource(
                position=9999,
                saved_at=_SAVED_AT,
                by_kind=(ApplyProgressKindResource(kind="DcimDevice", committed=10000, uncertain=0, not_reached=0),),
            ),
            "resumes": (
                ResumeResource(actor="ops", requested_at=_REQUESTED_AT, start_after=5999, outcome="succeeded"),
            ),
        }
    )

    result = _invoke(client, *_resume_arguments())

    assert result.exit_code == 0, result.output
    assert "progress: 10000/10000 operations committed (saved 2026-11-12T10:15:00+00:00)" in result.output
    assert "resumable: no" in result.output
    assert "resume 1: ops 2026-11-12T11:02:00+00:00 after 5999 succeeded" in result.output


def test_a_verify_required_refusal_prints_the_hint_and_exits_1(client: MagicMock) -> None:  # noqa: F811
    client.resume.side_effect = APIError(409, "verify-required", run_id=_RUN_ID, mutation_id="m-1")

    result = _invoke(client, *_resume_arguments())

    assert result.exit_code == 1, result.output
    assert "error: api" in result.output
    assert "code: verify-required" in result.output
    assert f"hint: run infrahub-sync verify {_RUN_ID} first" in result.output
    client.wait_for_run.assert_not_called()


def test_an_approval_required_refusal_prints_the_approve_hint_and_exits_1(client: MagicMock) -> None:  # noqa: F811
    """Spec 022 FR-019: a run that was never approved, a `sync` run for example, is approved with `approve`."""
    client.resume.side_effect = APIError(409, "approval-required", run_id=_RUN_ID, mutation_id="m-1")

    result = _invoke(client, *_resume_arguments())

    assert result.exit_code == 1, result.output
    assert "code: approval-required" in result.output
    assert f"hint: run infrahub-sync approve {_RUN_ID} --expected-checksum {CHECKSUM} first" in result.output
    client.wait_for_run.assert_not_called()


def test_a_left_out_refusal_prints_the_confirmation_line_and_exits_1(client: MagicMock) -> None:  # noqa: F811
    refusal = APIError(409, "left-out-confirmation-required", run_id=_RUN_ID, left_out=4)
    client.resume.side_effect = refusal

    result = _invoke(client, *_resume_arguments())

    assert result.exit_code == 1, result.output
    assert "resume refused: this plan leaves out 4 records; confirm with --confirm-left-out" in result.output


@pytest.mark.parametrize("code", ["checksum-conflict", "run-not-resumable", "run-execution-conflict", "forbidden"])
def test_other_refusals_exit_with_the_code_apply_uses(client: MagicMock, code: str) -> None:  # noqa: F811
    client.resume.side_effect = APIError(409, code, run_id=_RUN_ID)
    client.apply.side_effect = APIError(409, code, run_id=_RUN_ID)

    resumed = _invoke(client, *_resume_arguments())
    applied = _invoke(client, "apply", _RUN_ID, "--expected-checksum", CHECKSUM)

    assert resumed.exit_code == applied.exit_code == 1
    assert "error: api" in resumed.output
    assert f"code: {code}" in resumed.output


def test_a_checksum_conflict_shows_the_given_and_the_saved_checksum(client: MagicMock) -> None:  # noqa: F811
    client.resume.side_effect = APIError(409, "checksum-conflict", run_id=_RUN_ID)
    client.get_plan.return_value = _plan()
    given = "b" * 64

    result = _invoke(client, "runs", "resume", _RUN_ID, "--expected-checksum", given)

    assert result.exit_code == 1, result.output
    assert "code: checksum-conflict" in result.output
    assert f"hint: the saved plan's checksum is {CHECKSUM}; you gave {given}" in result.output


def test_a_checksum_conflict_whose_plan_cannot_be_read_keeps_the_refusal(client: MagicMock) -> None:  # noqa: F811
    client.resume.side_effect = APIError(409, "checksum-conflict", run_id=_RUN_ID)
    client.get_plan.side_effect = APIError(410, "artifact-expired", run_id=_RUN_ID)
    given = "b" * 64

    result = _invoke(client, "runs", "resume", _RUN_ID, "--expected-checksum", given)

    assert result.exit_code == 1, result.output
    assert "code: checksum-conflict" in result.output
    assert "artifact-expired" not in result.output
    assert f"hint: you gave {given}; `infrahub-sync runs plan {_RUN_ID}` prints the saved plan's checksum" in (
        result.output
    )


def test_a_resume_that_ends_failed_exits_like_a_failed_apply(client: MagicMock) -> None:  # noqa: F811
    client.resume.return_value = _interrupted(verify_required=False)
    client.apply.return_value = _interrupted(verify_required=False)
    failure = RunTerminalError(
        _RUN_ID,
        terminal_state="interrupted",
        terminal_outcome="ambiguous",
        phase="resume-interrupted",
        outcome="ambiguous",
    )
    client.wait_for_run.side_effect = failure

    resumed = _invoke(client, *_resume_arguments())
    applied = _invoke(client, "apply", _RUN_ID, "--expected-checksum", CHECKSUM)

    assert resumed.exit_code == applied.exit_code == 1


def test_a_failed_resume_prints_its_recorded_failure(client: MagicMock) -> None:  # noqa: F811
    client.resume.return_value = _interrupted(verify_required=False)
    client.wait_for_run.side_effect = RunTerminalError(
        _RUN_ID, terminal_state="failed", terminal_outcome="failed", phase="resume-failed", outcome="failed"
    )
    client.get_results.return_value = ResultsResource(
        run_id=_RUN_ID,
        results={
            "resume_failure": {
                "stage": "resume",
                "outcome": "failed",
                "error_type": "OperationApplyFailedError",
                "next_action": "Re-run `plan` for this sync to record the destination's current ids.",
            }
        },
    )

    result = _invoke(client, *_resume_arguments())

    assert result.exit_code == 1, result.output
    assert "resume failed: OperationApplyFailedError" in result.output
    assert "hint: Re-run `plan` for this sync to record the destination's current ids." in result.output
    client.get_results.assert_called_with(_RUN_ID)


def test_a_client_input_error_exits_2_like_apply(client: MagicMock) -> None:  # noqa: F811
    client.resume.side_effect = ClientInputError("run_id")
    client.apply.side_effect = ClientInputError("run_id")

    resumed = _invoke(client, *_resume_arguments())
    applied = _invoke(client, "apply", _RUN_ID, "--expected-checksum", CHECKSUM)

    assert resumed.exit_code == applied.exit_code == 2


def test_runs_show_prints_the_progress_resumable_kind_and_resume_lines(client: MagicMock) -> None:  # noqa: F811
    resumes = (
        ResumeResource(
            actor="ops", requested_at=_REQUESTED_AT, reason="restart", start_after=5999, outcome="ambiguous"
        ),
    )
    client.get_run.return_value = _interrupted(verify_required=True, resumes=resumes)

    result = _invoke(client, "runs", "show", _RUN_ID)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "progress: 6000/10000 operations committed (saved 2026-11-12T10:15:00+00:00)" in lines
    assert "resumable: yes (verify required)" in lines
    assert "kind DcimDevice: committed 1200, uncertain 0, not reached 0" in lines
    assert "kind DcimInterface: committed 4800, uncertain 500, not reached 3500" in lines
    assert "resume 1: ops 2026-11-12T11:02:00+00:00 after 5999 ambiguous" in lines


def test_runs_show_prints_the_operations_each_resume_wrote(client: MagicMock) -> None:  # noqa: F811
    resumes = (
        ResumeResource(
            actor="ops", requested_at=_REQUESTED_AT, start_after=5999, outcome="succeeded", operations_written=4000
        ),
    )
    client.get_run.return_value = _interrupted(verify_required=False, resumes=resumes)

    result = _invoke(client, "runs", "show", _RUN_ID)

    assert result.exit_code == 0, result.output
    assert "resume 1: ops 2026-11-12T11:02:00+00:00 after 5999 succeeded, wrote 4000 operations" in (
        result.output.splitlines()
    )


@pytest.mark.parametrize(
    ("operations_written", "expected"),
    [(6100, "written: 6100 of 10000 operations"), (None, "written: 100 of 10000 operations")],
    ids=["whole-run-count", "record-of-an-older-sync-api"],
)
def test_a_stopped_resume_prints_what_the_whole_run_wrote(
    client: MagicMock,  # noqa: F811
    operations_written: int | None,
    expected: str,
) -> None:
    """The `written` line counts every execution of the run; a record without the count falls back to its own."""
    failure: dict[str, object] = {
        "stage": "resume",
        "outcome": "ambiguous",
        "error_type": "OperationApplyFailedError",
        "failure_type": "destination_rejected_write",
        "applied_operations": [f"op_{index:016x}" for index in range(100)],
        "failed_operation": "op_518aada155d790ad",
        "operations_total": 10000,
        "failed_operation_detail": {"action": "update", "kind": "DcimDevice", "identity": {"name": "rtr1"}},
    }
    if operations_written is not None:
        failure["operations_written"] = operations_written
    resource = _interrupted(verify_required=True)
    client.get_run.return_value = resource.model_copy(
        update={"run": resource.run.model_copy(update={"results": {"resume_failure": failure}})}
    )

    result = _invoke(client, "runs", "show", _RUN_ID)

    assert result.exit_code == 0, result.output
    assert expected in result.stdout.splitlines()


def test_runs_show_prints_no_progress_lines_for_a_run_without_a_write(client: MagicMock) -> None:  # noqa: F811
    client.get_run.return_value = _run()

    result = _invoke(client, "runs", "show", _RUN_ID)

    assert result.exit_code == 0, result.output
    assert "progress:" not in result.output
    assert "resumable:" not in result.output
