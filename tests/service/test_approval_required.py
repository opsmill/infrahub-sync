"""`INFRAHUB_SYNC_REQUIRE_APPROVAL`: a site that requires an approved plan before `apply`, and refuses `sync`.

The cases follow the HTTP contract of spec 019 (``dev/specs/019-plan-approval/contracts/http-api.md``)
and the setting's values in ``data-model.md``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.product_store import ProductProjection, ProductRun, RunFilters, local_product_projection
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.models import PlanOperationResource, PlanResource, PlanSummaryResource
from infrahub_sync.service.orchestration import CancellationResult, Observation, PoolStatus, Submission
from infrahub_sync.service.service import PLAN_ARTIFACT_ID, RunService
from infrahub_sync.service.settings import REQUIRE_APPROVAL_ENV, approval_required, validate_settings

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response

    from infrahub_sync.product_store import ConfigurationVersion

OWNER_TOKEN = "owner-token-required-0001"  # noqa: S105 - deliberate non-secret boundary canary.
CHECKSUM = "a" * 64
OTHER_CHECKSUM = "b" * 64
RUN_ID = "run-approval-required-001"


# T027: parsing the setting.
@pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "YES", "on", "On"])
def test_a_true_value_in_any_case_turns_the_setting_on(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, value)

    validate_settings()

    assert approval_required() is True


@pytest.mark.parametrize("value", ["false", "FALSE", "False", "0", "no", "NO", "off", "Off"])
def test_a_false_value_in_any_case_turns_the_setting_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, value)

    validate_settings()

    assert approval_required() is False


def test_the_setting_is_off_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REQUIRE_APPROVAL_ENV, raising=False)

    validate_settings()

    assert approval_required() is False


@pytest.mark.parametrize("value", ["maybe", "2", "enabled", "tru"])
def test_any_other_value_stops_the_service_at_start_up_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, value)
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    service = RunService(local_product_projection(tmp_path.resolve()), _RecordingOrchestration())

    with pytest.raises(ValueError, match=REQUIRE_APPROVAL_ENV):
        create_app(service, resolver)


# T028: admission with the setting on.
class _RecordingOrchestration:
    """Records every submission so a test can show a refusal submits nothing."""

    def __init__(self) -> None:
        self.submissions: list[dict[str, object]] = []

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:
        del idempotency_key
        self.submissions.append(parameters)
        return Submission(flow_run_id=f"flow-{len(self.submissions)}", state="pending")

    async def observe(self, flow_run_id: str) -> Observation:  # noqa: PLR6301
        del flow_run_id
        return Observation(available=False, state=None, reason="prefect-execution-unavailable")

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:  # noqa: PLR6301
        del work_pool_name, now
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:  # noqa: PLR6301
        del flow_run_id
        return CancellationResult(acknowledged=False, reason="not-used")


def _registered_package() -> ConfigurationPackage:
    return ConfigurationPackage.model_validate(
        {
            "format_version": 1,
            "configuration": {
                "name": "registered-inventory",
                "source": {
                    "name": "netbox",
                    "settings": {"url": "https://netbox.example", "token": {"$credential": "token"}},
                },
                "destination": {
                    "name": "infrahub",
                    "settings": {"url": "https://infrahub.example", "token": {"$credential": "token"}},
                },
                "order": [],
                "schema_mapping": [],
                "diffsync_flags": [],
                "incremental": None,
            },
            "credentials": {"token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_TOKEN"}},
        }
    )


_Api = tuple[TestClient, ProductProjection, _RecordingOrchestration, "ConfigurationVersion"]


@pytest.fixture(name="api")
def _api_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Api:
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path.resolve())
    orchestration = _RecordingOrchestration()
    version = projection.create_configuration(_registered_package())
    service = RunService(projection, orchestration, secrets=resolver.secret_values)
    return TestClient(create_app(service, resolver)), projection, orchestration, version


def _stored_plan_run(projection: ProductProjection, version: ConfigurationVersion) -> None:
    projection.create_run(
        ProductRun(
            run_id=RUN_ID,
            operation="plan",
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="owner",
            started_at=datetime(2026, 11, 10, 12, tzinfo=timezone.utc),
            phase="planned",
        )
    )
    plan = PlanResource(
        run_id=RUN_ID,
        checksum=CHECKSUM,
        checksum_ok=True,
        verification_notes=(),
        summary=PlanSummaryResource(
            by_action={"create": 1},
            by_kind={"Device": 1},
            total=1,
            delete_operations_computed=True,
            deletes_not_executed=0,
        ),
        operations=(
            PlanOperationResource(
                operation_id="op-001", action="create", kind="Device", identity={"name": "edge-01"}, tier=0
            ),
        ),
    )
    projection.publish_artifact(
        RUN_ID,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=plan.model_dump_json().encode(),
    )


def _post(client: TestClient, path: str, body: dict[str, Any], *, key: str) -> Response:
    return client.post(path, headers={"Authorization": f"Bearer {OWNER_TOKEN}", "Idempotency-Key": key}, json=body)


def _approve(client: TestClient) -> None:
    response = _post(client, f"/runs/{RUN_ID}/approve", {"expected_checksum": CHECKSUM}, key="approve-key")
    assert response.status_code == 200, response.text


def _apply(client: TestClient, *, checksum: str = CHECKSUM, key: str = "apply-key") -> Response:
    return _post(client, f"/runs/{RUN_ID}/apply", {"expected_checksum": checksum, "confirm_writes": True}, key=key)


def _create(client: TestClient, version: ConfigurationVersion, operation: str, *, key: str) -> Response:
    body = {
        "operation": operation,
        "config_id": version.config_id,
        "registry_version": version.registry_version,
        "confirm_writes": operation == "sync",
    }
    return _post(client, "/runs", body, key=key)


def _key_digest(key: str) -> str:
    return sha256(key.encode()).hexdigest()


def _outcomes(projection: ProductProjection, operation: str, run_id: str | None = None) -> list[str]:
    return [event.outcome for event in projection.audit_events(run_id) if event.operation == operation]


def test_apply_of_an_unapproved_run_is_refused_and_names_the_approve_command(api: _Api) -> None:
    client, projection, orchestration, version = api
    _stored_plan_run(projection, version)

    response = _apply(client)

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "approval-required"
    assert f"approve {RUN_ID} --expected-checksum {CHECKSUM}" in error["message"]
    assert orchestration.submissions == []
    assert projection.lookup_mutation("owner", _key_digest("apply-key")).value is None
    assert _outcomes(projection, "apply", RUN_ID) == ["refused-approval-required"]


def test_apply_with_a_checksum_other_than_the_approved_one_is_refused_showing_both(api: _Api) -> None:
    client, projection, orchestration, version = api
    _stored_plan_run(projection, version)
    _approve(client)

    response = _apply(client, checksum=OTHER_CHECKSUM)

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "approval-checksum-mismatch"
    assert CHECKSUM in error["message"]
    assert OTHER_CHECKSUM in error["message"]
    assert orchestration.submissions == []
    assert _outcomes(projection, "apply", RUN_ID) == ["refused-approval-required"]


def test_apply_of_an_approved_run_with_the_approved_checksum_is_accepted(api: _Api) -> None:
    client, projection, orchestration, version = api
    _stored_plan_run(projection, version)
    _approve(client)

    response = _apply(client)

    assert response.status_code == 202, response.text
    assert [submission["stage"] for submission in orchestration.submissions] == ["apply"]
    assert _outcomes(projection, "apply", RUN_ID) == ["accepted"]


def test_sync_is_refused_before_any_run_record_is_created(api: _Api) -> None:
    client, projection, orchestration, version = api

    response = _create(client, version, "sync", key="sync-key")

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "approval-required-for-sync"
    for command in ("plan", "approve", "apply"):
        assert command in error["message"]
    assert orchestration.submissions == []
    assert projection.list_runs(RunFilters()).items == ()
    assert projection.lookup_mutation("owner", _key_digest("sync-key")).value is None
    assert _outcomes(projection, "sync") == ["refused-approval-required"]


def test_plan_is_not_affected_by_the_setting(api: _Api) -> None:
    client, projection, orchestration, version = api

    response = _create(client, version, "plan", key="plan-key")

    assert response.status_code == 202, response.text
    assert [submission["stage"] for submission in orchestration.submissions] == ["plan"]
    assert _outcomes(projection, "plan") == ["accepted"]


def test_a_retried_apply_whose_submission_failed_is_refused_once_approval_is_required(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-015: a receipt reserved before the setting was on is not a way past the approval check."""
    client, projection, orchestration, version = api
    _stored_plan_run(projection, version)
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "false")

    async def unavailable(parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: RUF029 - matches the async protocol
        del parameters, idempotency_key
        raise ConnectionError

    available = orchestration.submit
    monkeypatch.setattr(orchestration, "submit", unavailable)
    first = _apply(client)
    assert first.status_code == 503, first.text
    monkeypatch.setattr(orchestration, "submit", available)
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")

    retry = _apply(client)

    assert retry.status_code == 409, retry.text
    assert retry.json()["error"]["code"] == "approval-required"
    assert orchestration.submissions == []
    assert _outcomes(projection, "apply", RUN_ID)[-1] == "refused-approval-required"


def test_an_apply_accepted_before_the_setting_was_turned_on_still_replays(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, projection, orchestration, version = api
    _stored_plan_run(projection, version)
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "false")
    first = _apply(client)
    assert first.status_code == 202, first.text
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")

    replay = _apply(client)

    assert replay.status_code == 202, replay.text
    assert replay.json() == first.json()
    assert len(orchestration.submissions) == 1
    assert _outcomes(projection, "apply", RUN_ID) == ["accepted", "replayed"]
