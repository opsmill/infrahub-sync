"""Configurations are read from Infrahub; runs record their versions and are mirrored there."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from fastapi.testclient import TestClient
from infrahub_sdk.exceptions import AuthenticationError, BranchNotFoundError

from infrahub_sync.platform.records import InfrahubConfigurations, RunMirror
from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import Principal
from infrahub_sync.service.config_routes import ConfigurationRoutes
from infrahub_sync.service.service import RunService
from tests.platform.test_records import DECLARED, _Store
from tests.service.test_http_api import _FakeOrchestration, _publish_plan, _settle_open_executions

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

TOKEN = "caller-token-0123456789"  # noqa: S105 - test value
HEADERS = {"X-INFRAHUB-KEY": TOKEN}


class _Resolver:
    @property
    def secret_values(self) -> tuple[str, ...]:
        return ()

    @staticmethod
    async def resolve(token: str) -> Principal | None:
        return Principal(actor="alice") if token == TOKEN else None


@contextmanager
def _no_lock(_config_id: str) -> Iterator[None]:
    yield


@pytest.fixture
def store() -> _Store:
    records = _Store()
    records.configuration("netbox-demo", yaml.safe_dump(DECLARED))
    return records


def _client(tmp_path: Path, store: _Store, *, configurations: Any = None) -> TestClient:  # noqa: ANN401
    projection = local_product_projection(tmp_path.resolve())
    service = RunService(
        projection,
        _FakeOrchestration(),
        configurations=InfrahubConfigurations(client=store, branch="main", version_lock=_no_lock)
        if configurations is None
        else configurations,
        mirror=RunMirror(client=store, branch="main"),
    )
    return TestClient(create_app(service, _Resolver()))


def _plan(client: TestClient, key: str, **body: Any) -> Any:  # noqa: ANN401
    return client.post(
        "/runs",
        headers={**HEADERS, "Idempotency-Key": key},
        json={"operation": "plan", "config_id": "netbox-demo", "reason": "infrahub records", **body},
    )


def test_a_run_without_a_version_records_and_uses_the_current_content(tmp_path: Path, store: _Store) -> None:
    response = _plan(_client(tmp_path, store), "first")

    assert response.status_code == 202, response.text
    (version,) = store.filters(kind="SyncConfigurationVersion")
    assert version.number.value == 1
    run = response.json()["run"]
    (mirrored,) = store.filters(kind="SyncRun")
    assert mirrored.run_id.value == run["run_id"]
    assert mirrored.requested_by.value == "alice"
    assert mirrored.version.value == version.id


def test_a_second_run_on_unchanged_content_reuses_the_version(tmp_path: Path, store: _Store) -> None:
    client = _client(tmp_path, store)

    _plan(client, "first")
    _plan(client, "second")

    assert len(store.filters(kind="SyncConfigurationVersion")) == 1
    assert len(store.filters(kind="SyncRun")) == 2


def test_invalid_content_is_refused_and_records_nothing(tmp_path: Path, store: _Store) -> None:
    content = json.loads(json.dumps(DECLARED))
    content["configuration"]["source"]["settings"]["token"] = "a-literal-credential"  # noqa: S105 - the defect
    store.filters(kind="SyncConfiguration")[0].document.value = yaml.safe_dump(content)

    response = _plan(_client(tmp_path, store), "invalid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "configuration-invalid"
    assert store.filters(kind="SyncConfigurationVersion") == []
    assert store.filters(kind="SyncRun") == []


def test_an_unknown_configuration_is_not_found(tmp_path: Path, store: _Store) -> None:
    response = _client(tmp_path, store).post(
        "/runs",
        headers={**HEADERS, "Idempotency-Key": "unknown"},
        json={"operation": "plan", "config_id": "missing", "reason": "infrahub records"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "configuration-not-found"


def test_a_store_without_recorded_versions_asks_for_an_explicit_version(tmp_path: Path, store: _Store) -> None:
    projection = local_product_projection((tmp_path / "other").resolve())

    response = _plan(_client(tmp_path, store, configurations=projection), "explicit")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "configuration-version-required"


def test_a_mirror_failure_never_fails_the_run(tmp_path: Path, store: _Store) -> None:
    client = _client(tmp_path, store)
    store.fail_saves = False
    _plan(client, "warm")
    store.fail_saves = True

    response = _plan(client, "while-infrahub-refuses")

    assert response.status_code == 202, response.text


class _BranchStore(_Store):
    """Holds a different document on one named branch, and knows no other branch."""

    def __init__(self, branch_document: str) -> None:
        super().__init__()
        self._branch_document = branch_document

    def filters(self, kind: str, branch: str | None = None, **filters: Any) -> list[Any]:  # noqa: ANN401
        if branch == "missing-branch":
            raise BranchNotFoundError(identifier=branch)
        found = super().filters(kind, branch=branch, **filters)
        if branch == "my-change" and kind == "SyncConfiguration":
            for node in found:
                node.document.value = self._branch_document
        return found


def _routes_client(tmp_path: Path, store: _Store, principal: Principal | None = None) -> TestClient:
    projection = local_product_projection(tmp_path.resolve())
    configurations = InfrahubConfigurations(client=store, branch="main", version_lock=_no_lock)
    service = RunService(projection, _FakeOrchestration(), configurations=configurations)
    routes = ConfigurationRoutes(product_projection=projection, configurations=configurations)
    resolver = _Resolver() if principal is None else _Fixed(principal)
    return TestClient(create_app(service, resolver, routes))


class _Fixed(_Resolver):
    def __init__(self, principal: Principal) -> None:
        self._principal = principal

    async def resolve(self, token: str) -> Principal | None:
        return self._principal if token == TOKEN else None


def test_a_branch_document_is_validated_without_recording_anything(tmp_path: Path) -> None:
    changed = json.loads(json.dumps(DECLARED))
    changed["configuration"]["source"]["settings"]["token"] = "a-literal-credential"  # noqa: S105 - the defect
    store = _BranchStore(yaml.safe_dump(changed))
    store.configuration("netbox-demo", yaml.safe_dump(DECLARED))
    client = _routes_client(tmp_path, store)

    clean = client.post("/configs/netbox-demo/validate", headers=HEADERS)
    flagged = client.post("/configs/netbox-demo/validate", params={"branch": "my-change"}, headers=HEADERS)

    assert clean.status_code == 200, clean.text
    assert clean.json()["total_findings"] == 0
    assert flagged.status_code == 200, flagged.text
    assert flagged.json()["branch"] == "my-change"
    assert flagged.json()["total_findings"] > 0
    assert "a-literal-credential" not in flagged.text
    assert store.filters(kind="SyncConfigurationVersion") == []


def test_an_unknown_branch_is_not_found(tmp_path: Path) -> None:
    store = _BranchStore("")
    store.configuration("netbox-demo", yaml.safe_dump(DECLARED))

    response = _routes_client(tmp_path, store).post(
        "/configs/netbox-demo/validate", params={"branch": "missing-branch"}, headers=HEADERS
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "branch-not-found"


def test_configuration_reads_come_from_infrahub(tmp_path: Path, store: _Store) -> None:
    client = _routes_client(tmp_path, store)
    _plan(_client(tmp_path, store), "record-a-version")

    listed = client.get("/configs", headers=HEADERS)
    versions = client.get("/configs/netbox-demo/versions", headers=HEADERS)

    assert [item["config_id"] for item in listed.json()] == ["netbox-demo"]
    assert [item["registry_version"] for item in versions.json()] == [1]


@pytest.mark.parametrize("path", ["/configs", "/configs/netbox-demo/versions"])
def test_registration_routes_point_to_infrahub(tmp_path: Path, store: _Store, path: str) -> None:
    response = _routes_client(tmp_path, store).post(
        path,
        headers={**HEADERS, "Idempotency-Key": "register"},
        json={"package": DECLARED, "reason": "register"},
    )

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "configurations-in-infrahub"


def test_reading_configurations_needs_the_infrahub_view_permission(tmp_path: Path, store: _Store) -> None:
    runs_only = Principal(actor="bob", permissions=frozenset({"object:Sync:Run:view:allow_all"}))

    response = _routes_client(tmp_path, store, runs_only).get("/configs", headers=HEADERS)

    assert response.status_code == 403
    assert "object:Sync:Configuration:view" in response.json()["error"]["message"]


class _TaggingOrchestration(_FakeOrchestration):
    def __init__(self) -> None:
        super().__init__()
        self.tagged: list[tuple[str, tuple[str, ...]]] = []

    async def tag_nodes(self, flow_run_id: str, node_ids: Any) -> None:  # noqa: ANN401
        self.tagged.append((flow_run_id, tuple(node_ids)))


def test_a_new_run_tags_its_flow_run_with_its_infrahub_nodes(tmp_path: Path, store: _Store) -> None:
    orchestration = _TaggingOrchestration()
    service = RunService(
        local_product_projection(tmp_path.resolve()),
        orchestration,
        configurations=InfrahubConfigurations(client=store, branch="main", version_lock=_no_lock),
        mirror=RunMirror(client=store, branch="main"),
    )
    client = TestClient(create_app(service, _Resolver()))

    response = _plan(client, "tagged")

    assert response.status_code == 202, response.text
    (run_node,) = store.filters(kind="SyncRun")
    (configuration,) = store.filters(kind="SyncConfiguration")
    ((flow_run_id, nodes),) = orchestration.tagged
    assert nodes == (run_node.id, configuration.id)
    assert run_node.flow_run_id.value == flow_run_id


@pytest.mark.parametrize("document", ["unrelated: mapping\n", "format_version: 1\n"])
def test_a_malformed_package_is_refused_as_invalid_content(tmp_path: Path, store: _Store, document: str) -> None:
    store.filters(kind="SyncConfiguration")[0].document.value = document

    response = _plan(_client(tmp_path, store), "malformed")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "configuration-invalid"
    assert store.filters(kind="SyncConfigurationVersion") == []


class _SubmitFailsOnce(_FakeOrchestration):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Any:  # noqa: ANN401
        if not self.failed:
            self.failed = True
            msg = "the task manager is unreachable"
            raise ConnectionError(msg)
        return await super().submit(parameters, idempotency_key=idempotency_key)


def test_a_retry_after_an_edit_submits_the_version_its_reservation_recorded(tmp_path: Path, store: _Store) -> None:
    projection = local_product_projection(tmp_path.resolve())
    orchestration = _SubmitFailsOnce()
    service = RunService(
        projection,
        orchestration,
        configurations=InfrahubConfigurations(client=store, branch="main", version_lock=_no_lock),
        mirror=RunMirror(client=store, branch="main"),
    )
    client = TestClient(create_app(service, _Resolver()), raise_server_exceptions=False)
    assert _plan(client, "same-key").status_code >= 500
    edited = json.loads(json.dumps(DECLARED))
    edited["configuration"]["order"] = ["InfraDevice"]
    store.filters(kind="SyncConfiguration")[0].document.value = yaml.safe_dump(edited)

    retried = _plan(client, "same-key")

    assert retried.status_code == 202, retried.text
    stored = projection.lookup_run(retried.json()["run"]["run_id"]).value
    assert stored is not None
    submitted = orchestration.submissions[-1][0]
    assert (submitted["registry_version"], submitted["package_checksum"]) == (
        stored.registry_version,
        stored.package_checksum,
    )
    assert [node.number.value for node in store.filters(kind="SyncConfigurationVersion")] == [1]


def test_an_unconfirmed_sync_is_refused_before_a_version_is_recorded(tmp_path: Path, store: _Store) -> None:
    response = _client(tmp_path, store).post(
        "/runs",
        headers={**HEADERS, "Idempotency-Key": "unconfirmed"},
        json={"operation": "sync", "config_id": "netbox-demo", "reason": "infrahub records"},
    )

    assert response.status_code == 409, response.text
    assert store.filters(kind="SyncConfigurationVersion") == []


def test_validating_another_branch_needs_the_view_permission_on_other_branches(tmp_path: Path) -> None:
    store = _BranchStore(yaml.safe_dump(DECLARED))
    store.configuration("netbox-demo", yaml.safe_dump(DECLARED))
    default_only = Principal(actor="dave", permissions=frozenset({"object:Sync:Configuration:view:allow_default"}))
    client = _routes_client(tmp_path, store, default_only)

    on_default = client.post("/configs/netbox-demo/validate", headers=HEADERS)
    on_branch = client.post("/configs/netbox-demo/validate", params={"branch": "my-change"}, headers=HEADERS)

    assert on_default.status_code == 200, on_default.text
    assert on_branch.status_code == 403
    assert "other than the default" in on_branch.json()["error"]["message"]


def _infrahub_service(tmp_path: Path, store: _Store) -> RunService:
    return RunService(
        local_product_projection(tmp_path.resolve()),
        _FakeOrchestration(),
        configurations=InfrahubConfigurations(client=store, branch="main", version_lock=_no_lock),
        mirror=RunMirror(client=store, branch="main"),
    )


def _approving_client(tmp_path: Path, store: _Store) -> tuple[TestClient, Any]:
    service = _infrahub_service(tmp_path, store)
    return TestClient(create_app(service, _Resolver())), service._projection


def test_an_apply_records_one_approval_however_often_it_is_replayed(tmp_path: Path, store: _Store) -> None:
    client, projection = _approving_client(tmp_path, store)
    run_id = _plan(client, "plan").json()["run"]["run_id"]
    plan = _publish_plan(projection, run_id)
    _settle_open_executions(projection, run_id)
    body = {"expected_checksum": plan.checksum, "confirm_writes": True, "reason": "reviewed"}
    headers = {**HEADERS, "Idempotency-Key": "apply"}

    applied = client.post(f"/runs/{run_id}/apply", headers=headers, json=body)
    replayed = client.post(f"/runs/{run_id}/apply", headers=headers, json=body)

    assert applied.status_code == 202, applied.text
    assert replayed.json() == applied.json()
    (approval,) = store.filters(kind="SyncApproval")
    assert (approval.approved_by.value, approval.approved_checksum.value) == ("alice", plan.checksum)
    (mirrored,) = store.filters(kind="SyncRun")
    assert mirrored.plan_checksum.value == plan.checksum


def test_a_cancelled_run_is_mirrored(tmp_path: Path, store: _Store) -> None:
    client, projection = _approving_client(tmp_path, store)
    run_id = _plan(client, "plan").json()["run"]["run_id"]

    cancelled = client.post(
        f"/runs/{run_id}/cancel", headers={**HEADERS, "Idempotency-Key": "cancel"}, json={"reason": "stop"}
    )

    assert cancelled.status_code == 202, cancelled.text
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    (mirrored,) = store.filters(kind="SyncRun")
    assert mirrored.phase.value == stored.phase
    assert mirrored.saved >= 2


@pytest.mark.parametrize(
    "route",
    [
        ("get", "/runs/{run_id}", None, "object:Sync:Run:view"),
        ("post", "/runs/{run_id}/verify", {"reason": "verify"}, "object:Sync:Run:create"),
        ("post", "/runs/{run_id}/cancel", {"reason": "cancel"}, "object:Sync:Run:update"),
    ],
)
def test_run_routes_need_their_infrahub_permission(tmp_path: Path, store: _Store, route: tuple[Any, ...]) -> None:
    method, path, body, permission = route
    service = _infrahub_service(tmp_path, store)
    run_id = _plan(TestClient(create_app(service, _Resolver())), "plan").json()["run"]["run_id"]
    nobody = Principal(actor="nobody", permissions=frozenset())
    refused_client = TestClient(create_app(service, _Fixed(nobody)))

    response = getattr(refused_client, method)(
        path.format(run_id=run_id),
        headers={**HEADERS, "Idempotency-Key": "refused"},
        **({"json": body} if body else {}),
    )

    assert response.status_code == 403, response.text
    assert permission in response.json()["error"]["message"]


def test_a_refused_service_token_is_named_when_a_run_cannot_record_its_version(tmp_path: Path, store: _Store) -> None:
    def refused(*_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
        message = "401"
        raise AuthenticationError(message)

    store.filters = refused  # ty: ignore[invalid-assignment]

    response = _plan(_client(tmp_path, store), "refused")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "infrahub-unavailable"
    assert "INFRAHUB_SYNC_INFRAHUB_TOKEN" in response.json()["error"]["message"]
