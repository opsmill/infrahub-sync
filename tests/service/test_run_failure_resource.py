"""`GET /runs/{run_id}` returns the run's failure cause as `run.failure` (spec 009 T069).

A failed run returns the fields of data-model.md "Run failure cause"; a successful run returns
`null`. The existing `results.{stage}_failure` evidence keeps its keys, so scripts that read it
keep working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

import requests
from infrahub_sdk.exceptions import GraphQLError

from infrahub_sync import SideLoadError
from infrahub_sync.service import flow as service_flow
from tests.plan.problem_fixtures import Variant
from tests.service.sync_stage_harness import SyncStageHarness, build_sync_stage_harness

if TYPE_CHECKING:
    from pathlib import Path

# The fields of data-model.md "Run failure cause", and nothing else.
CAUSE_FIELDS = {
    "stage",
    "failure_type",
    "system",
    "error_code",
    "node_kind",
    "attribute",
    "record",
    "field",
    "infrahub_message",
    "summary",
    "error_class",
    "apply_progress",
    "next_action",
}
# The evidence keys a failure without an apply record has carried since before spec 009.
EVIDENCE_KEYS = {"stage", "outcome", "error_type", "cause_type"}


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SyncStageHarness:
    return build_sync_stage_harness(monkeypatch, tmp_path)


def _token_rejected(*_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
    response = requests.Response()
    response.status_code = 401
    error = SideLoadError("An error occurred while loading netbox", side="source", adapter="netbox")
    error.__cause__ = requests.HTTPError("401 Client Error", response=response)
    raise error


def _run(harness: SyncStageHarness, run_id: str) -> dict[str, Any]:
    response = harness.get(f"/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()["run"]


def test_a_failed_run_returns_its_cause(harness: SyncStageHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_flow, "_plan", _token_rejected)

    run = _run(harness, harness.run_sync())

    failure = run["failure"]
    assert set(failure) == CAUSE_FIELDS
    assert failure == {
        "stage": "sync",
        "failure_type": "token_rejected",
        "system": {"role": "source", "adapter": "netbox"},
        "error_code": None,
        "node_kind": None,
        "attribute": None,
        "record": None,
        "field": None,
        "infrahub_message": None,
        "summary": failure["summary"],
        "error_class": "SideLoadError",
        "apply_progress": None,
        "next_action": failure["next_action"],
    }
    assert "netbox" in failure["summary"]
    assert failure["next_action"]
    assert run["outcome"] == "failed"


def test_a_rejected_write_returns_infrahub_s_fields(harness: SyncStageHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    harness.variant[0] = Variant.NO_PROBLEMS
    rejected = GraphQLError(
        errors=[
            {
                "message": "planned is not a valid value for status",
                "extensions": {
                    "code": "ATTRIBUTE_CONSTRAINT_VIOLATION",
                    "node_kind": "DcimDevice",
                    "attribute": "status",
                },
            }
        ],
        query="mutation { DcimDeviceUpdate }",
    )
    engine = service_flow.execute_run

    def execute_run(instance: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if kwargs.get("operation") == "apply":
            raise rejected
        return engine(instance, **kwargs)

    monkeypatch.setattr(service_flow, "execute_run", execute_run)

    run = _run(harness, harness.run_sync())

    failure = run["failure"]
    assert set(failure) == CAUSE_FIELDS
    assert failure["failure_type"] == "destination_rejected_write"
    assert (failure["error_code"], failure["node_kind"], failure["attribute"]) == (
        "ATTRIBUTE_CONSTRAINT_VIOLATION",
        "DcimDevice",
        "status",
    )
    assert failure["infrahub_message"] == "planned is not a valid value for status"
    assert failure["error_class"] == "GraphQLError"


def test_the_stage_failure_evidence_keeps_its_keys(harness: SyncStageHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_flow, "_plan", _token_rejected)

    run_id = harness.run_sync()

    stored = harness.projection.lookup_run(run_id).value
    assert stored is not None
    evidence = stored.results["sync_failure"]
    assert set(evidence) == EVIDENCE_KEYS
    assert evidence == {"stage": "sync", "outcome": "failed", "error_type": "SideLoadError", "cause_type": "HTTPError"}
    results = harness.get(f"/runs/{run_id}/results").json()["results"]
    assert results["sync_failure"] == evidence


def test_a_successful_run_returns_no_cause(harness: SyncStageHarness) -> None:
    harness.variant[0] = Variant.NO_PROBLEMS

    run = _run(harness, harness.run_sync())

    assert run["outcome"] == "applied"
    assert run["failure"] is None
