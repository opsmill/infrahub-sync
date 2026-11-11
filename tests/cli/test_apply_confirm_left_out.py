"""The CLI side of the left-out confirmation and of a stopped `sync` (spec 019 T037).

contracts/cli.md "Apply a plan that leaves out records" and "Cause of a failed or stopped run":

- `apply --confirm-left-out` is a flag without a value, and sends `confirm_left_out: true`
- without it, on a plan with records left out, the command exits with code 1 before any write
  and prints `apply refused: this plan leaves out 12 records; confirm with --confirm-left-out`
- a `sync` that stopped after its plan prints `sync stopped: left_out_review_required (12
  records left out)` and the `hint:` read from `results.failure`, and exits with code 1
- the client sends the field only when it is true (FR-026)

The specification does not say how the CLI learns the count for the refusal line. The refusal
double below offers it twice: as a `left_out` attribute on the API error, and in the summary of
the run's plan. Either way satisfies these tests.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import ValidationError

from infrahub_sync.client import APIError, ApplyRunRequest, PlanSummaryResource, ResultsResource, SyncClient
from tests.cli.test_client_commands import (
    CHECKSUM,
    _invoke,
    _plan,
    _restore_package_logging,  # noqa: F401 - the CLI suite's autouse fixture, used by name.
    _run,
    client,  # noqa: F401 - the fixture is used by name.
)
from tests.client.test_client import (
    TOKEN,
    _version,
)
from tests.client.test_client import _run as _run_payload

if TYPE_CHECKING:
    from unittest.mock import MagicMock

_NOT_IMPLEMENTED = "spec 019 US2 not implemented"
_RUN_ID = "service-run-1"
_NEXT_ACTION = (
    f"Review the plan with `infrahub-sync runs plan {_RUN_ID} --problems` and `--left-out`, then apply it with "
    f"`infrahub-sync apply {_RUN_ID} --expected-checksum {CHECKSUM} --confirm-left-out`."
)


def _apply_arguments(*extra: str) -> tuple[str, ...]:
    return ("apply", _RUN_ID, "--expected-checksum", CHECKSUM, "--reason", "left-out records reviewed", *extra)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_NOT_IMPLEMENTED)
def test_the_flag_sends_the_confirmation(client: MagicMock) -> None:  # noqa: F811 - the imported fixture.
    result = _invoke(client, *_apply_arguments("--confirm-left-out", "--no-wait"))

    assert result.exit_code == 0, result.output
    _run_id, request, _key = client.apply.call_args.args
    assert getattr(request, "confirm_left_out", None) is True


def test_without_the_flag_the_request_does_not_confirm(client: MagicMock) -> None:  # noqa: F811 - the imported fixture.
    result = _invoke(client, *_apply_arguments("--no-wait"))

    assert result.exit_code == 0, result.output
    _run_id, request, _key = client.apply.call_args.args
    assert getattr(request, "confirm_left_out", False) is False


def _plan_leaving_out(count: int) -> object:
    plan = _plan()
    summary = PlanSummaryResource.model_validate({**plan.summary.model_dump(), "left_out": count})
    return plan.model_copy(update={"summary": summary})


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_NOT_IMPLEMENTED)
def test_an_unconfirmed_apply_prints_the_refusal_and_exits_1(client: MagicMock) -> None:  # noqa: F811
    refusal = APIError(409, "left-out-confirmation-required", run_id=_RUN_ID)
    setattr(refusal, "left_out", 12)  # noqa: B010 - one of the two ways the count may reach the CLI.
    client.apply.side_effect = refusal
    client.get_plan.return_value = _plan_leaving_out(12)

    result = _invoke(client, *_apply_arguments())

    assert result.exit_code == 1, result.output
    assert "apply refused: this plan leaves out 12 records; confirm with --confirm-left-out" in result.output
    client.wait_for_run.assert_not_called()


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_NOT_IMPLEMENTED)
def test_a_stopped_sync_prints_the_stop_and_its_hint_from_the_stage_result(client: MagicMock) -> None:  # noqa: F811
    stopped = _run(
        operation="sync", phase="planned", outcome="left-out-review-required", terminal=("completed", "succeeded")
    )
    client.wait_for_run.side_effect = lambda _accepted, **_kwargs: stopped
    client.get_results.return_value = ResultsResource(
        run_id=_RUN_ID,
        results={
            "run_id": _RUN_ID,
            "stage": "sync",
            "outcome": "left-out-review-required",
            "failure": {
                "stage": "sync",
                "failure_type": "left_out_review_required",
                "left_out": 12,
                "next_action": _NEXT_ACTION,
            },
        },
    )

    result = _invoke(client, "sync", "--config-id", "edge-sync", "--version", "1", "--reason", "import")

    assert result.exit_code == 1, result.output
    assert "sync stopped: left_out_review_required (12 records left out)" in result.output
    assert f"hint: {_NEXT_ACTION}" in result.output
    client.get_results.assert_called_with(_RUN_ID)


@pytest.mark.xfail(strict=True, raises=ValidationError, reason=_NOT_IMPLEMENTED)
def test_the_client_sends_the_field_only_when_it_is_true() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json=_version())
        bodies.append(json.loads(request.content))
        return httpx.Response(202, json=_run_payload())

    sync_client = SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))
    confirmed = ApplyRunRequest(
        expected_checksum=CHECKSUM,
        confirm_writes=True,
        reason="test",
        confirm_left_out=True,  # ty: ignore[unknown-argument] - added by T040.
    )
    sync_client.apply_run("run-1", confirmed, "key-confirmed")
    sync_client.apply_run(
        "run-1", ApplyRunRequest(expected_checksum=CHECKSUM, confirm_writes=True, reason="test"), "key-plain"
    )

    assert bodies[0]["confirm_left_out"] is True
    assert "confirm_left_out" not in bodies[1]
