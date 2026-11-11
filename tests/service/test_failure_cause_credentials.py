"""No credential and no error text reaches the record of a failed run (spec 019 T047, SC-005, SC-007).

FR-019, FR-029 and FR-030, and decision 3 of the specification: a failure record is built only
from allowlisted fields, never from the text of an error or from connection settings, so its
protection does not depend on replacing values in text.

Each failure type of `tests/service/failure_cases.py` is raised with error text that holds a
credential of five characters and a credential that holds `"`, `\\` and `,`, in an address with a
user name and password, a request header and a traceback. The checks run with no secret list, so
the masking pass replaces nothing. Neither value, in any form JSON gives it, nor any part of that
text may appear in:

- the failure record that the worker writes (`results.<stage>_failure`)
- `GET /runs/{run_id}` and `GET /runs/{run_id}/results`
- the CLI output of `runs show` and of the failed stage's command
- the Prefect run summary artifact of the stage

The worker and Prefect log lines keep masked error text until 3.0 (FR-030); they are not
checked here.
"""

from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.client import ResultsResource, RunResource
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.run_artifact import run_markdown
from infrahub_sync.service.service import RunService
from tests.cli.test_client_commands import (
    CHECKSUM,
    _invoke,
    _restore_package_logging,  # noqa: F401 - the CLI suite's autouse fixture, used by name.
    _terminal,
    client,  # noqa: F401 - the fixture is used by name.
)
from tests.service.execution_fixtures import append_execution
from tests.service.failure_cases import CASES, CREDENTIALS, ERROR_TEXT, OTHER_CASES, FailureCase, forbidden_values
from tests.service.test_http_api import _FakeOrchestration, _registered_package

if TYPE_CHECKING:
    from pathlib import Path
    from unittest.mock import MagicMock

    from infrahub_sync.product_store import ProductProjection

OWNER_TOKEN = "owner-token-canary-0190"  # noqa: S105 - deliberate non-secret boundary canary.
HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
WORKER_ID = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
RUN_ID = "run-failure-credentials"

ALL_CASES = [pytest.param(case, id=case.case_id) for case in (*CASES, *OTHER_CASES)]

# The command of each stage, as the CLI starts it.
_STAGE_COMMANDS: dict[str, tuple[str, ...]] = {
    "plan": ("plan", "--config-id", "edge-sync", "--version", "1", "--reason", "review"),
    "apply": ("apply", "service-run-1", "--expected-checksum", CHECKSUM, "--reason", "apply reviewed plan"),
}


def _raised(case: FailureCase) -> Exception:
    """The case's error with the credentials in its text; checks that they really are there."""
    exc = case.build(ERROR_TEXT)
    formatted = "".join(traceback.format_exception(exc))
    for credential in CREDENTIALS:
        assert credential in formatted, "The error text holds the credential, so its absence is the record's doing."
    return exc


def _assert_absent(raw: str | bytes, surface: str) -> None:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    for forbidden in forbidden_values():
        assert forbidden not in text, f"{forbidden!r} reached {surface}"


def _serialized(value: Any) -> list[str]:  # noqa: ANN401 - any JSON value.
    """The value as JSON, with and without escaping of characters outside ASCII."""
    return [json.dumps(value, ensure_ascii=True), json.dumps(value, ensure_ascii=False)]


@pytest.mark.parametrize("case", ALL_CASES)
def test_no_credential_reaches_the_failure_record(case: FailureCase) -> None:
    evidence = service_flow._failure_evidence(case.stage, _raised(case), outcome="failed", record=None, secrets=())

    for rendered in _serialized(evidence):
        _assert_absent(rendered, f"the {case.case_id} failure record")


def _service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[ProductProjection, TestClient, str]:
    """A product store with one claimed run, and a client of the Sync API over it."""
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection((tmp_path / "store").resolve())
    version = projection.create_configuration(_registered_package())
    projection.create_run(
        ProductRun(
            run_id=RUN_ID,
            operation="plan",
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="owner",
            started_at=datetime(2026, 11, 11, 12, tzinfo=timezone.utc),
            phase="accepted",
            summary={"sync_name": "inventory"},
        )
    )
    flow_run_id = str(uuid5(NAMESPACE_URL, RUN_ID))
    append_execution(projection, RUN_ID, PrefectExecutionLink(flow_run_id=flow_run_id, purpose="plan", attempt=1))
    assert projection.claim_execution(RUN_ID, flow_run_id, worker_id=WORKER_ID)
    service = RunService(projection, _FakeOrchestration(), secrets=resolver.secret_values)
    return projection, TestClient(create_app(service, resolver)), flow_run_id


def _record(projection: ProductProjection, flow_run_id: str, case: FailureCase) -> service_flow.RecordedFailure:
    """Record the case's failure for the claimed run, the way the worker does, with no secret list."""
    recorded = service_flow._record_failure(
        RUN_ID,
        case.stage,
        _raised(case),
        logging.getLogger("test-service"),
        [],
        projection,
        flow_run_id,
        WORKER_ID,
        dispatch_started=False,
        record=None,
    )
    assert recorded is not None
    return recorded


@pytest.mark.parametrize("case", ALL_CASES)
def test_no_credential_reaches_the_api_the_cli_or_the_run_artifact(
    case: FailureCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client: MagicMock,  # noqa: F811 - the imported fixture.
) -> None:
    projection, api, flow_run_id = _service(monkeypatch, tmp_path)
    recorded = _record(projection, flow_run_id, case)

    run_response = api.get(f"/runs/{RUN_ID}", headers=HEADERS)
    results_response = api.get(f"/runs/{RUN_ID}/results", headers=HEADERS)
    assert run_response.status_code == 200, run_response.text
    assert results_response.status_code == 200, results_response.text
    assert f"{case.stage}_failure" in results_response.json()["results"]
    _assert_absent(run_response.content, "GET /runs/{run_id}")
    _assert_absent(results_response.content, "GET /runs/{run_id}/results")

    client.get_run.return_value = RunResource.model_validate(run_response.json())
    shown = _invoke(client, "runs", "show", RUN_ID)
    assert shown.exit_code == 0, shown.output
    assert f"{case.stage} failed: " in shown.output
    _assert_absent(shown.output, "runs show")

    client.wait_for_run.side_effect = _terminal(f"{case.stage}-failed")
    client.get_results.return_value = ResultsResource.model_validate(results_response.json())
    failed = _invoke(client, *_STAGE_COMMANDS[case.stage])
    assert f"{case.stage} failed: " in failed.output
    _assert_absent(failed.output, f"the output of {case.stage}")

    stored = projection.lookup_run(RUN_ID).value
    assert stored is not None
    artifact = run_markdown(stored, case.stage, stored.outcome or "failed", recorded.next_action)
    _assert_absent(artifact, "the run summary artifact")
