"""The Sync API apply gate for a plan that leaves out records (spec 019 T034).

FR-010, FR-011 and FR-026, contracts/sync-api.md "Apply request" and research R10:

- an apply of a plan that leaves out records is refused before any submission with 409
  `left-out-confirmation-required`, the count in the message and in the error body, and the
  audit outcome `refused-left-out-not-confirmed`
- with `confirm_left_out: true` the apply is admitted, and the worker receives the confirmation
  so the engine can check it again
- a plan that leaves out nothing needs no confirmation, and accepts one sent anyway
- `confirm_left_out` is a strict JSON boolean; any other value is 422
- a request without the field keeps working when nothing is left out
- the checksum check comes first, as the contract orders the checks
- a format 3 plan is refused with `PlanFormatApplyUnsupportedError` before any write (SC-006)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from infrahub_sync.plan.errors import PlanFormatApplyUnsupportedError
from infrahub_sync.service.models import (
    PlanOperationResource,
    PlanResource,
    PlanSummaryResource,
)
from infrahub_sync.service.service import PLAN_ARTIFACT_ID
from tests.plan.artifact_fixtures import (
    CONFIG_VERSION,
    write_artifact,
)
from tests.plan.test_plan_format_3 import (
    CountingDestination,
    RecordingOwnership,
    engine_over,
    run_dir,
    update_record,
)
from tests.service.test_http_api import (
    AUTH,
    _create,
    _FakeOrchestration,
    _settle_open_executions,
    service_api,  # noqa: F401 - the fixture is used by name.
)

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient
    from httpx import Response

    from infrahub_sync.product_store import ProductProjection

_NOT_IMPLEMENTED = "spec 019 US2 not implemented"
_API = tuple["TestClient", "ProductProjection", _FakeOrchestration]
_CHECKSUM = "c" * 64


def _publish_plan(projection: ProductProjection, run_id: str, *, left_out: int) -> PlanResource:
    """Publish a review document whose plan leaves out `left_out` records, as the plan worker does."""
    plan = PlanResource(
        run_id=run_id,
        checksum=_CHECKSUM,
        checksum_ok=True,
        verification_notes=(),
        summary=PlanSummaryResource(
            by_action={"create": 1},
            by_kind={"Device": 1},
            total=1,
            delete_operations_computed=True,
            deletes_not_executed=0,
            left_out=left_out,
            left_out_with_problem=min(left_out, 1),
            left_out_dependent=max(left_out - 1, 0),
            left_out_by_kind={"Device": left_out} if left_out else {},
            problems=min(left_out, 1),
            problems_by_type={"wrong_value_type": 1} if left_out else {},
        ),
        operations=(
            PlanOperationResource(
                operation_id="op-001",
                action="create",
                kind="Device",
                identity={"name": "edge-01"},
                tier=0,
                payload={"name": "edge-01"},
                relationships=(),
            ),
        ),
    )
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=plan.model_dump_json().encode(),
    )
    return plan


def _planned_run(api: _API, *, left_out: int) -> str:
    client, projection, _orchestration = api
    run_id = _create(client).json()["run"]["run_id"]
    _publish_plan(projection, run_id, left_out=left_out)
    _settle_open_executions(projection, run_id)
    return run_id


def _apply(client: TestClient, run_id: str, key: str, **extra: Any) -> Response:  # noqa: ANN401
    body = {"expected_checksum": _CHECKSUM, "confirm_writes": True, "reason": "left-out records reviewed", **extra}
    return client.post(f"/runs/{run_id}/apply", headers={**AUTH, "Idempotency-Key": key}, json=body)


def _stages(orchestration: _FakeOrchestration) -> list[object]:
    return [parameters["stage"] for parameters, _key in orchestration.submissions]


def _left_out_in(body: dict[str, Any]) -> object:
    """The count the refusal body carries: in the error object, or next to it."""
    error = body.get("error", {})
    return error.get("left_out", body.get("left_out"))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_NOT_IMPLEMENTED)
@pytest.mark.parametrize("extra", [{}, {"confirm_left_out": False}], ids=["field-absent", "field-false"])
def test_an_unconfirmed_apply_of_a_plan_that_leaves_out_records_is_refused_before_any_write(
    service_api: _API,  # noqa: F811 - the imported fixture.
    extra: dict[str, Any],
) -> None:
    client, projection, orchestration = service_api
    run_id = _planned_run(service_api, left_out=12)

    refused = _apply(client, run_id, "apply-unconfirmed", **extra)

    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["error"]["code"] == "left-out-confirmation-required"
    assert "12" in body["error"]["message"]
    assert "confirm_left_out" in body["error"]["message"]
    assert _left_out_in(body) == 12
    assert _stages(orchestration) == ["plan"], "Nothing was submitted to the worker."
    outcomes = [event.outcome for event in projection.audit_events(run_id)]
    assert "refused-left-out-not-confirmed" in outcomes


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_NOT_IMPLEMENTED)
def test_a_refused_apply_leaves_the_run_free_for_its_confirmed_apply(
    service_api: _API,  # noqa: F811 - the imported fixture.
) -> None:
    """The refusal reserves no write admission, so the confirmed apply that follows is admitted."""
    client, _projection, orchestration = service_api
    run_id = _planned_run(service_api, left_out=12)

    refused = _apply(client, run_id, "apply-unconfirmed")
    confirmed = _apply(client, run_id, "apply-confirmed", confirm_left_out=True)

    assert refused.status_code == 409
    assert confirmed.status_code == 202, confirmed.text
    assert _stages(orchestration) == ["plan", "apply"]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_NOT_IMPLEMENTED)
def test_a_confirmed_apply_is_admitted_and_the_worker_receives_the_confirmation(
    service_api: _API,  # noqa: F811 - the imported fixture.
) -> None:
    client, projection, orchestration = service_api
    run_id = _planned_run(service_api, left_out=12)

    accepted = _apply(client, run_id, "apply-confirmed", confirm_left_out=True)

    assert accepted.status_code == 202, accepted.text
    parameters, _key = orchestration.submissions[-1]
    assert parameters["stage"] == "apply"
    assert parameters.get("confirm_left_out") is True, "The engine checks the confirmation again (research R10)."
    assert "refused-left-out-not-confirmed" not in [event.outcome for event in projection.audit_events(run_id)]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_NOT_IMPLEMENTED)
def test_a_confirmation_sent_for_a_plan_that_leaves_out_nothing_is_accepted_and_ignored(
    service_api: _API,  # noqa: F811 - the imported fixture.
) -> None:
    client, _projection, orchestration = service_api
    run_id = _planned_run(service_api, left_out=0)

    accepted = _apply(client, run_id, "apply-confirmed-anyway", confirm_left_out=True)

    assert accepted.status_code == 202, accepted.text
    assert _stages(orchestration) == ["plan", "apply"]


def test_a_request_without_the_field_keeps_working_when_nothing_is_left_out(
    service_api: _API,  # noqa: F811 - the imported fixture.
) -> None:
    """FR-026: a request body of today stays valid."""
    client, _projection, orchestration = service_api
    run_id = _planned_run(service_api, left_out=0)

    accepted = _apply(client, run_id, "apply-today")

    assert accepted.status_code == 202, accepted.text
    assert _stages(orchestration) == ["plan", "apply"]


@pytest.mark.parametrize("value", ["true", 1, "yes", None], ids=["string-true", "one", "yes", "null"])
def test_the_confirmation_is_a_strict_json_boolean(
    service_api: _API,  # noqa: F811 - the imported fixture.
    value: object,
) -> None:
    """Any value other than a JSON boolean is 422, and nothing is submitted.

    This passes today because the request model refuses any unknown field. After T040 it
    guards that the new field is strict: a lax boolean would accept `"true"` and `1`.
    """
    client, _projection, orchestration = service_api
    run_id = _planned_run(service_api, left_out=12)

    refused = _apply(client, run_id, f"apply-{value!r}", confirm_left_out=value)

    assert refused.status_code == 422, refused.text
    assert _stages(orchestration) == ["plan"]


def test_a_checksum_mismatch_is_refused_before_the_left_out_check(
    service_api: _API,  # noqa: F811 - the imported fixture.
) -> None:
    """contracts/sync-api.md orders the checks: the checksum comes before the confirmation."""
    client, _projection, orchestration = service_api
    run_id = _planned_run(service_api, left_out=12)

    refused = _apply(client, run_id, "apply-stale", expected_checksum="d" * 64)

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "checksum-conflict"
    assert _stages(orchestration) == ["plan"]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_NOT_IMPLEMENTED)
def test_a_format_3_plan_is_refused_before_any_write(tmp_path: Path) -> None:
    """SC-006: a format 3 plan was never checked for problems, so it is reviewable and not applyable.

    Checked at the engine, where the worker applies the plan: the Sync API's review document
    does not carry the plan format.
    """
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record()], source_snapshot=[], format_version=3)
    ownership = RecordingOwnership()
    destination = CountingDestination()

    try:
        outcome: object = engine_over(directory, destination=destination).apply_plan(
            ownership=ownership, config_version=CONFIG_VERSION
        )
    except PlanFormatApplyUnsupportedError as exc:
        outcome = exc

    assert isinstance(outcome, PlanFormatApplyUnsupportedError), "A format 3 plan was applied."
    assert "3" in str(outcome)
    assert "`plan`" in str(outcome)
    assert ownership.proofs == 0
    assert destination.applied == []
