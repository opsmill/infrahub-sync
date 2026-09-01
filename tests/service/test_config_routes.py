"""Focused transport checks for configuration HTTP resources."""

from __future__ import annotations

import ast
import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING, cast

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.configuration.models import ValidationFinding
from infrahub_sync.product_store import configs as configs_service
from infrahub_sync.product_store import local_product_projection
from infrahub_sync.product_store.configs import ValidationReport
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.config_routes import ConfigurationAPIError, ConfigurationRoutes
from infrahub_sync.service.orchestration import CancellationResult, Observation, PoolStatus, Submission
from infrahub_sync.service.serve import build_app
from infrahub_sync.service.service import RunService, ServiceAPIError
from tests.configuration.validation_packages import package, package_data

ADMIN_TOKEN = "admin-token-canary-0003"  # noqa: S105 - deliberate non-secret boundary canary.
OTHER_TOKEN = "reader-token-canary-0004"  # noqa: S105 - deliberate non-secret boundary canary.

if TYPE_CHECKING:
    from typing import NoReturn

    from infrahub_sync.configuration.models import ConfigurationPackage
    from infrahub_sync.product_store.models import ConfigurationVersion
    from infrahub_sync.product_store.store import ProductProjection
    from infrahub_sync.service.auth import PrincipalResolver
    from infrahub_sync.service.liveness import RunLivenessReconciler


class _Orchestration:  # pylint: disable=too-few-public-methods
    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: ARG002, PLR6301
        return Submission(flow_run_id="test-flow", state="pending")

    async def observe(self, flow_run_id: str) -> Observation:  # noqa: ARG002, PLR6301
        return Observation(available=True, state="running")

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:  # noqa: ARG002, PLR6301
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:  # noqa: ARG002, PLR6301
        return CancellationResult(acknowledged=True)


class _UnknownConfigsError(configs_service.ConfigsError):
    """An unrecognized service error remains conservatively non-retryable."""


def test_configuration_routes_register_then_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A configuration resource delegates registration and its scoped reads."""
    monkeypatch.setenv(
        PRINCIPALS_ENV, json.dumps({"admin": {"token": "admin-token-canary-0003", "administrator": True}})
    )
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path)
    runs = RunService(projection, _Orchestration(), secrets=resolver.secret_values)
    routes = ConfigurationRoutes(product_projection=local_product_projection(tmp_path), secrets=resolver.secret_values)
    client = TestClient(create_app(runs, resolver, routes))
    response = client.post(
        "/configs",
        headers={"Authorization": "Bearer admin-token-canary-0003", "Idempotency-Key": "register-once"},
        json={"package": package_data(), "reason": "register"},
    )
    assert response.status_code == 201, response.text
    config_id = response.json()["configuration"]["config_id"]
    assert client.get("/configs", headers={"Authorization": "Bearer admin-token-canary-0003"}).status_code == 200
    assert (
        client.get(f"/configs/{config_id}", headers={"Authorization": "Bearer admin-token-canary-0003"}).status_code
        == 200
    )


def test_configuration_routes_use_the_injected_projection_for_services_receipts_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every configuration path uses the one projection supplied at composition."""

    bearer = "admin-token-canary-0003"
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"admin": {"token": bearer, "administrator": True}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    delegate = local_product_projection(tmp_path)

    class ProjectionSentinel:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def __getattr__(self, name: str) -> object:
            target = getattr(delegate, name)
            if not callable(target):
                return target

            def record(*args: object, **kwargs: object) -> object:
                self.calls.append(name)
                return target(*args, **kwargs)

            return record

    projection = ProjectionSentinel()
    client = TestClient(
        create_app(
            RunService(cast("ProductProjection", projection), _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(
                product_projection=cast("ProductProjection", projection), secrets=resolver.secret_values
            ),
        )
    )

    headers = {"Authorization": f"Bearer {bearer}", "Idempotency-Key": "injected-projection"}
    body = {"package": package_data(), "reason": "register through one projection"}
    registered = client.post(
        "/configs",
        headers=headers,
        json=body,
    )
    replayed = client.post("/configs", headers=headers, json=body)
    config_id = registered.json()["configuration"]["config_id"]
    listed = client.get("/configs", headers={"Authorization": f"Bearer {bearer}"})
    validated = client.post(
        f"/configs/{config_id}/versions/1/validate",
        headers={"Authorization": f"Bearer {bearer}"},
    )

    assert registered.status_code == replayed.status_code == 201
    assert replayed.json() == registered.json()
    assert listed.status_code == validated.status_code == 200
    assert {
        "reserve_mutation",
        "claim_mutation",
        "create_configuration",
        "complete_mutation",
        "list_configurations",
        "lookup_configuration",
        "lookup_configuration_version",
        "record_audit",
    }.issubset(projection.calls)
    assert projection.calls.count("reserve_mutation") == 2
    assert projection.calls.count("create_configuration") == 1


def test_configuration_receipt_and_audit_provider_errors_are_storage_failures() -> None:
    """Provider failures after the service call retain the configuration storage family."""
    from infrahub_sync.product_store import ProductStoreProviderError

    class Projection:
        @staticmethod
        def reserve_mutation(*_args: object, **_kwargs: object) -> NoReturn:
            raise ProductStoreProviderError(sqlstate="08006")

        @staticmethod
        def record_audit(*_args: object, **_kwargs: object) -> NoReturn:
            raise ProductStoreProviderError(sqlstate="08006")

    routes = ConfigurationRoutes(product_projection=cast("ProductProjection", Projection()))
    with pytest.raises(ConfigurationAPIError) as receipt_error:
        routes.mutate(
            actor="admin",
            idempotency_key="provider-error",
            operation="register-config",
            resource_kind="configuration-registry",
            resource_id="configs",
            package=package_data(),
            reason="provider error",
        )
    with pytest.raises(ConfigurationAPIError) as audit_error:
        routes.audit_refusal("admin", "register-config", "provider error")
    assert receipt_error.value.family == audit_error.value.family == "storage"


def test_configuration_http_provider_errors_preserve_storage_and_internal_families(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Marked projection failures are storage responses at every configuration HTTP boundary."""
    from infrahub_sync.product_store import ProductStoreProviderError

    admin_token = ADMIN_TOKEN
    reader_token = OTHER_TOKEN
    monkeypatch.setenv(
        PRINCIPALS_ENV,
        json.dumps(
            {
                "admin": {"token": admin_token, "administrator": True},
                "reader": {"token": reader_token},
            }
        ),
    )
    resolver = EnvironmentPrincipalResolver.from_environment()
    run_projection = local_product_projection(tmp_path)

    class StorageProjection:
        @staticmethod
        def list_configurations() -> NoReturn:
            raise ProductStoreProviderError(sqlstate="08006")

        @staticmethod
        def reserve_mutation(*_args: object, **_kwargs: object) -> NoReturn:
            raise ProductStoreProviderError(sqlstate="08006")

        @staticmethod
        def record_audit(*_args: object, **_kwargs: object) -> NoReturn:
            raise ProductStoreProviderError(sqlstate="08006")

    client = TestClient(
        create_app(
            RunService(run_projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(product_projection=cast("ProductProjection", StorageProjection())),
        )
    )
    admin_headers = {"Authorization": f"Bearer {admin_token}", "Idempotency-Key": "provider-error"}
    reader_headers = {"Authorization": f"Bearer {reader_token}", "Idempotency-Key": "audit-provider-error"}
    responses = (
        client.get("/configs", headers=admin_headers),
        client.post("/configs", headers=admin_headers, json={"package": package_data(), "reason": "storage"}),
        client.post("/configs", headers=reader_headers, json={"package": package_data(), "reason": "audit"}),
    )
    for response in responses:
        payload = response.json()["error"]
        assert response.status_code == 503
        assert payload == {
            "code": "configs-storage",
            "message": "the configuration service refused the request",
            "status": 503,
            "family": "storage",
            "reason": None,
        }
        assert "08006" not in response.text

    class InternalProjection:
        @staticmethod
        def list_configurations() -> NoReturn:
            message = "projection-secret-canary"
            raise RuntimeError(message)

    internal_client = TestClient(
        create_app(
            RunService(run_projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(product_projection=cast("ProductProjection", InternalProjection())),
        )
    )
    internal = internal_client.get("/configs", headers=admin_headers)
    assert internal.status_code == 503
    assert internal.json()["error"]["family"] == "internal"
    assert "projection-secret-canary" not in internal.text


@pytest.mark.parametrize(
    "boundary",
    [
        "create_configuration",
        "list_configurations",
        "lookup_configuration",
        "list_configuration_versions",
        "lookup_configuration_version",
        "reserve_mutation",
        "claim_mutation",
        "release_mutation",
        "complete_mutation",
        "record_audit",
    ],
)
@pytest.mark.parametrize("failure_kind", ["provider", "runtime"])
def test_every_configuration_storage_boundary_has_a_value_free_http_family(
    boundary: str,
    failure_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each projection call contains provider details and classifies unmarked defects."""
    from infrahub_sync.product_store import ProductStoreProviderError

    admin_token = ADMIN_TOKEN
    reader_token = OTHER_TOKEN
    monkeypatch.setenv(
        PRINCIPALS_ENV,
        json.dumps(
            {
                "admin": {"token": admin_token, "administrator": True},
                "reader": {"token": reader_token, "administrator": False},
            }
        ),
    )
    resolver = EnvironmentPrincipalResolver.from_environment()
    delegate = local_product_projection(tmp_path)
    version = delegate.create_configuration(package())
    dsn_canary = "postgresql://dsn-secret-canary@db/sync"
    driver_canary = "driver-secret-canary"

    class FailingProjection:
        def __getattr__(self, name: str) -> object:
            target = getattr(delegate, name)
            if name != boundary:
                return target

            def fail(*_args: object, **_kwargs: object) -> NoReturn:
                if failure_kind == "provider":
                    error = ProductStoreProviderError(sqlstate=dsn_canary)
                    error.args = (driver_canary,)
                    raise error
                message = f"{driver_canary}: {dsn_canary}"
                raise RuntimeError(message)

            return fail

    projection = cast("ProductProjection", FailingProjection())
    client = TestClient(
        create_app(
            RunService(projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(product_projection=projection, secrets=resolver.secret_values),
        ),
        raise_server_exceptions=False,
    )
    admin_headers = {"Authorization": f"Bearer {admin_token}", "Idempotency-Key": f"failure-{boundary}"}
    reader_headers = {"Authorization": f"Bearer {reader_token}", "Idempotency-Key": f"failure-{boundary}"}
    valid_body = {"package": package_data(), "reason": "boundary evidence"}

    if boundary == "list_configurations":
        response = client.get("/configs", headers=admin_headers)
    elif boundary == "lookup_configuration":
        response = client.get(f"/configs/{version.config_id}", headers=admin_headers)
    elif boundary == "list_configuration_versions":
        response = client.get(f"/configs/{version.config_id}/versions", headers=admin_headers)
    elif boundary == "lookup_configuration_version":
        response = client.get(f"/configs/{version.config_id}/versions/1", headers=admin_headers)
    elif boundary == "release_mutation":
        response = client.post(
            "/configs",
            headers=admin_headers,
            json={"package": {}, "reason": "pre-effect refusal"},
        )
    elif boundary == "record_audit":
        response = client.post("/configs", headers=reader_headers, json=valid_body)
    else:
        response = client.post("/configs", headers=admin_headers, json=valid_body)

    expected_family = "storage" if failure_kind == "provider" else "internal"
    assert response.status_code == 503
    assert response.json()["error"]["family"] == expected_family
    rendered = response.text + "\n" + caplog.text
    for forbidden in (
        dsn_canary,
        driver_canary,
        "Traceback",
        "ProductStoreProviderError",
        "RuntimeError",
    ):
        assert forbidden not in rendered


def test_configuration_mutation_replays_exact_response_and_rejects_changed_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configuration mutation uses the one durable receipt rather than a second write."""
    monkeypatch.setenv(
        PRINCIPALS_ENV, json.dumps({"admin": {"token": "admin-token-canary-0003", "administrator": True}})
    )
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path)
    client = TestClient(
        create_app(
            RunService(projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(product_projection=local_product_projection(tmp_path), secrets=resolver.secret_values),
        )
    )
    headers = {"Authorization": "Bearer admin-token-canary-0003", "Idempotency-Key": "register-once"}
    body = {"package": package_data(), "reason": "register this package"}

    first = client.post("/configs", headers=headers, json=body)
    replay = client.post("/configs", headers=headers, json=body)
    changed = client.post("/configs", headers=headers, json={**body, "reason": "different reason"})

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert changed.status_code == 409
    assert len(projection.list_configurations()) == 1
    receipt = projection.lookup_mutation("admin", sha256(b"register-once").hexdigest())
    assert receipt.value is not None
    assert receipt.value.state == "accepted"


def test_duplicate_configuration_version_checksum_returns_existing_version_without_a_second_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checksum already stored for a configuration is a 200 replay, not a new version."""
    bearer = "admin-token-canary-0003"
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"admin": {"token": bearer, "administrator": True}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path)
    client = TestClient(
        create_app(
            RunService(projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(product_projection=local_product_projection(tmp_path), secrets=resolver.secret_values),
        )
    )
    package = package_data()
    registration = client.post(
        "/configs",
        headers={"Authorization": f"Bearer {bearer}", "Idempotency-Key": "register-config"},
        json={"package": package, "reason": "register"},
    )
    config_id = registration.json()["configuration"]["config_id"]
    before = projection.list_configuration_versions(config_id)

    response = client.post(
        f"/configs/{config_id}/versions",
        headers={"Authorization": f"Bearer {bearer}", "Idempotency-Key": "duplicate-version"},
        json={"package": package, "reason": "duplicate checksum"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["version"]["registry_version"] == before[0].registry_version
    assert projection.list_configuration_versions(config_id) == before
    assert len(projection.list_configurations()) == 1


def test_configuration_mutation_audits_accepted_replayed_and_refused_idempotency_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every mutation decision is durable evidence and contains no bearer secret."""
    bearer = "admin-token-canary-0003"
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"admin": {"token": bearer, "administrator": True}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path)
    client = TestClient(
        create_app(
            RunService(projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(product_projection=local_product_projection(tmp_path), secrets=resolver.secret_values),
        )
    )
    headers = {"Authorization": f"Bearer {bearer}", "Idempotency-Key": "audit-once"}
    body = {"package": package_data(), "reason": "register audit proof"}

    assert client.post("/configs", headers=headers, json=body).status_code == 201
    assert client.post("/configs", headers=headers, json=body).status_code == 201
    assert client.post("/configs", headers=headers, json={**body, "reason": "changed audit proof"}).status_code == 409

    events = projection.audit_events()
    assert [event.outcome for event in events] == ["accepted", "replayed", "refused-idempotency"]
    assert all(bearer not in event.model_dump_json() for event in events)


@pytest.mark.parametrize(
    ("error_type", "status", "released"),
    [
        (configs_service.ConfigsRequestError, 400, True),
        (configs_service.ConfigsValidationError, 422, True),
        (configs_service.ConfigsNotFoundError, 404, True),
        (configs_service.ConfigsStorageError, 503, False),
        (configs_service.ConfigsInternalError, 503, False),
        (configs_service.ConfigsError, 503, False),
        (_UnknownConfigsError, 503, False),
    ],
)
def test_configuration_mutation_releases_only_proven_pre_effect_error_families(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[configs_service.ConfigsError],
    *,
    status: int,
    released: bool,
) -> None:
    """Only declared pre-effect families make a configuration receipt retryable."""
    bearer = "admin-token-canary-0003"
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"admin": {"token": bearer, "administrator": True}}))
    resolver = EnvironmentPrincipalResolver.from_environment()

    class Service:
        ConfigsRequestError = configs_service.ConfigsRequestError
        ConfigsValidationError = configs_service.ConfigsValidationError
        ConfigsNotFoundError = configs_service.ConfigsNotFoundError
        ConfigsStorageError = configs_service.ConfigsStorageError
        ConfigsInternalError = configs_service.ConfigsInternalError
        ConfigsError = configs_service.ConfigsError

        def __init__(self) -> None:
            self.calls = 0

        def register(self, **_kwargs: object) -> dict[str, object]:
            self.calls += 1
            raise error_type("service failure")  # noqa: EM101, TRY003

    projection = local_product_projection(tmp_path)
    service = Service()
    client = TestClient(
        create_app(
            RunService(projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(
                product_projection=local_product_projection(tmp_path), service=service, secrets=resolver.secret_values
            ),
        )
    )
    headers = {"Authorization": f"Bearer {bearer}", "Idempotency-Key": "retry-after-service-failure"}
    body = {"package": package_data(), "reason": "retry after outage"}

    refused = client.post("/configs", headers=headers, json=body)
    reserved = projection.lookup_mutation("admin", sha256(headers["Idempotency-Key"].encode()).hexdigest()).value
    retry = client.post("/configs", headers=headers, json=body)

    assert refused.status_code == status
    assert reserved is not None
    assert reserved.state == ("reserved" if released else "processing")
    assert retry.status_code == (status if released else 409)
    assert service.calls == (2 if released else 1)
    assert [event.outcome for event in projection.audit_events()] == (
        ["unavailable", "unavailable"] if released else ["unavailable", "refused-idempotency-in-progress"]
    )
    assert all(bearer not in event.model_dump_json() for event in projection.audit_events())


def test_post_commit_readback_failure_blocks_same_key_retry_without_a_second_configuration(tmp_path: Path) -> None:
    """Contain an unknown post-commit outcome; this is not crash recovery or exactly-once execution."""
    projection = local_product_projection(tmp_path)
    writes = 0

    class PostCommitReadbackFailure:  # pylint: disable=too-few-public-methods,no-self-use
        def __getattr__(self, name: str) -> object:
            return getattr(projection, name)

        def create_configuration(self, package: ConfigurationPackage) -> ConfigurationVersion:  # noqa: PLR6301
            nonlocal writes
            writes += 1
            return projection.create_configuration(package)

        def lookup_configuration(self, _config_id: str) -> NoReturn:  # noqa: PLR6301
            message = "read-back failed after configuration commit"
            raise OSError(message)

    routes = ConfigurationRoutes(product_projection=cast("ProductProjection", PostCommitReadbackFailure()))
    request = {
        "actor": "admin",
        "idempotency_key": "post-commit-readback-failure",
        "operation": "register-config",
        "resource_kind": "configuration-registry",
        "resource_id": "configs",
        "package": package_data(),
        "reason": "prove conservative containment",
    }

    with pytest.raises(ConfigurationAPIError) as first:
        routes.mutate(**request)
    with pytest.raises(ServiceAPIError) as retry:
        routes.mutate(**request)

    receipt = projection.lookup_mutation("admin", sha256(b"post-commit-readback-failure").hexdigest()).value
    assert first.value.status == 503
    assert first.value.family == "storage"
    assert retry.value.code == "idempotency-in-progress"
    assert receipt is not None
    assert receipt.state == "processing"
    assert writes == 1
    assert len(projection.list_configurations()) == 1
    assert [event.outcome for event in projection.audit_events()] == ["unavailable", "refused-idempotency-in-progress"]


def test_concurrent_configuration_mutation_does_not_invoke_service_twice(tmp_path: Path) -> None:
    """An in-flight idempotency identity has one service owner, not two."""
    started = Event()
    release = Event()

    class Service:
        ConfigsRequestError = configs_service.ConfigsRequestError
        ConfigsValidationError = configs_service.ConfigsValidationError
        ConfigsNotFoundError = configs_service.ConfigsNotFoundError
        ConfigsStorageError = configs_service.ConfigsStorageError
        ConfigsInternalError = configs_service.ConfigsInternalError
        ConfigsError = configs_service.ConfigsError

        def __init__(self) -> None:
            self.calls = 0

        def register(self, **_kwargs: object) -> dict[str, object]:
            self.calls += 1
            started.set()
            assert release.wait(timeout=5)
            return {
                "configuration": {"config_id": "cfg", "created_at": datetime.now().astimezone()},
                "version": {
                    "config_id": "cfg",
                    "registry_version": 1,
                    "package_checksum": "a" * 64,
                    "declared_content": {},
                    "created_at": datetime.now().astimezone(),
                },
            }

    service = Service()
    routes = ConfigurationRoutes(product_projection=local_product_projection(tmp_path), service=service)

    def mutate() -> tuple[int, dict[str, object]]:
        return routes.mutate(
            actor="admin",
            idempotency_key="concurrent-key",
            operation="register-config",
            resource_kind="configuration-registry",
            resource_id="configs",
            package=package_data(),
            reason="test concurrent receipt",
        )

    first = Thread(target=mutate)
    first.start()
    assert started.wait(timeout=5)
    with pytest.raises(ServiceAPIError) as raised:
        mutate()
    release.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert raised.value.status == 409
    assert raised.value.code == "idempotency-in-progress"
    assert service.calls == 1


def test_configuration_receipts_use_semantic_resource_identities(tmp_path: Path) -> None:
    """Registration and version receipts are keyed by domain resource, not HTTP path."""
    routes = ConfigurationRoutes(product_projection=local_product_projection(tmp_path))
    register = routes.mutate(
        actor="admin",
        idempotency_key="semantic-registration",
        operation="register-config",
        resource_kind="configuration-registry",
        resource_id="configs",
        package=package_data(),
        reason="semantic registration",
    )
    config_id = register[1]["configuration"]["config_id"]
    routes.mutate(
        actor="admin",
        idempotency_key="semantic-version",
        operation="create-config-version",
        resource_kind="configuration",
        resource_id=config_id,
        package=package_data(),
        reason="semantic version",
    )

    projection = local_product_projection(tmp_path)
    registration = projection.lookup_mutation("admin", sha256(b"semantic-registration").hexdigest()).value
    version = projection.lookup_mutation("admin", sha256(b"semantic-version").hexdigest()).value
    assert registration is not None
    assert version is not None
    assert (registration.resource_kind, registration.resource_id) == ("configuration-registry", "configs")
    assert (version.resource_kind, version.resource_id) == ("configuration", config_id)


def test_unrelated_service_assertion_is_not_reclassified_as_configuration_refusal(tmp_path: Path) -> None:
    """Only the service boundary, rather than HTTP routing, classifies service failures."""

    class Service:
        ConfigsRequestError = configs_service.ConfigsRequestError
        ConfigsValidationError = configs_service.ConfigsValidationError
        ConfigsNotFoundError = configs_service.ConfigsNotFoundError
        ConfigsStorageError = configs_service.ConfigsStorageError
        ConfigsInternalError = configs_service.ConfigsInternalError
        ConfigsError = configs_service.ConfigsError

        @staticmethod
        def list_configs(**_kwargs: object) -> None:
            raise AssertionError("unrelated defect")  # noqa: EM101, TRY003 - the assertion is the transport boundary probe.

    with pytest.raises(AssertionError, match="unrelated defect"):
        ConfigurationRoutes(product_projection=local_product_projection(tmp_path), service=Service()).list_configs()


def test_configuration_mutation_refuses_unauthenticated_and_non_admin_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authorization refusal occurs before configuration reservation or service invocation."""
    monkeypatch.setenv(
        PRINCIPALS_ENV,
        json.dumps(
            {
                "admin": {"token": "admin-token-canary-0003", "administrator": True},
                "reader": {"token": "reader-token-canary-0002", "administrator": False},
            }
        ),
    )
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path)
    client = TestClient(
        create_app(
            RunService(projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(product_projection=local_product_projection(tmp_path), secrets=resolver.secret_values),
        )
    )
    body = {"package": package_data(), "reason": "register this package"}
    assert client.post("/configs", headers={"Idempotency-Key": "missing-key"}, json=body).status_code == 401
    assert (
        client.post(
            "/configs",
            headers={"Authorization": "Bearer reader-token-canary-0002", "Idempotency-Key": "reader-key"},
            json=body,
        ).status_code
        == 403
    )
    assert projection.list_configurations() == ()
    assert [event.outcome for event in projection.audit_events()] == ["refused-authentication", "refused-authorization"]


def test_configuration_route_grammar_refuses_hostile_values_before_service_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path, page, and body grammar failures use the fixed request envelope without a service call."""
    bearer = "admin-token-canary-0003"
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"admin": {"token": bearer, "administrator": True}}))
    resolver = EnvironmentPrincipalResolver.from_environment()

    class Service:
        ConfigsRequestError = configs_service.ConfigsRequestError
        ConfigsValidationError = configs_service.ConfigsValidationError
        ConfigsNotFoundError = configs_service.ConfigsNotFoundError
        ConfigsStorageError = configs_service.ConfigsStorageError
        ConfigsInternalError = configs_service.ConfigsInternalError
        ConfigsError = configs_service.ConfigsError

        def __init__(self) -> None:
            self.calls: list[str] = []

        def list_configs(self, **_kwargs: object) -> list[object]:
            self.calls.append("list_configs")
            return []

        def list_versions(self, **_kwargs: object) -> list[object]:
            self.calls.append("list_versions")
            return []

        def get_version(self, **_kwargs: object) -> object:
            self.calls.append("get_version")
            return {}

        def validate(self, **_kwargs: object) -> object:
            self.calls.append("validate")
            return {}

        def create_version(self, **_kwargs: object) -> object:
            self.calls.append("create_version")
            return {}

        def register(self, **_kwargs: object) -> object:
            self.calls.append("register")
            return {}

    service = Service()
    client = TestClient(
        create_app(
            RunService(local_product_projection(tmp_path), _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(
                product_projection=local_product_projection(tmp_path), service=service, secrets=resolver.secret_values
            ),
        )
    )
    headers = {
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
        "Idempotency-Key": "grammar-key",
    }
    probes = [
        ("post", "/configs", {"json": {"package": package_data(), "reason": "valid", "extra": True}}),
        ("post", "/configs", {"content": '{"package":{"nested":[NaN]},"reason":"valid"}'}),
        ("post", "/configs", {"json": {"package": package_data(), "reason": "invalid\nreason"}}),
        ("get", "/configs?offset=true", {}),
        ("get", "/configs?offset=1.0", {}),
        ("get", "/configs?limit=257", {}),
        ("get", "/configs/not%20an%20id", {}),
        ("get", "/configs/good/versions/true", {}),
        ("get", "/configs/good/versions/1.0", {}),
        ("get", "/configs/good/versions/0", {}),
        ("post", "/configs/good/versions/1/validate?limit=true", {}),
    ]
    for method, path, kwargs in probes:
        response = getattr(client, method)(path, headers=headers, **kwargs)
        assert response.status_code == 422
        assert response.json() == {
            "error": {
                "code": "request-invalid",
                "message": "the request does not match the API schema",
                "status": 422,
                "run_id": None,
                "mutation_id": None,
            }
        }
    assert service.calls == []


def test_validation_pages_are_bounded_and_concatenate_to_the_single_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pages slice one ordered validation report without changing its snapshot fields."""
    bearer = "admin-token-canary-0003"
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"admin": {"token": bearer, "administrator": True}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    findings = tuple(
        ValidationFinding(code="synthetic", severity="error", location=f"/item{i}", message=f"finding {i}")
        for i in range(600)
    )

    class Service:
        ConfigsRequestError = configs_service.ConfigsRequestError
        ConfigsValidationError = configs_service.ConfigsValidationError
        ConfigsNotFoundError = configs_service.ConfigsNotFoundError
        ConfigsStorageError = configs_service.ConfigsStorageError
        ConfigsInternalError = configs_service.ConfigsInternalError
        ConfigsError = configs_service.ConfigsError

        @staticmethod
        def validate(**_kwargs: object) -> ValidationReport:
            return ValidationReport("cfg", 1, "a" * 64, findings, "b" * 64)

    client = TestClient(
        create_app(
            RunService(local_product_projection(tmp_path), _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(
                product_projection=local_product_projection(tmp_path), service=Service(), secrets=resolver.secret_values
            ),
        )
    )
    headers = {"Authorization": f"Bearer {bearer}"}
    pages = [
        client.post(f"/configs/cfg/versions/1/validate?offset={offset}&limit=256", headers=headers).json()
        for offset in range(0, 600, 256)
    ]
    assert all(len(page["findings"]) <= 256 for page in pages)
    assert [page["findings"] for page in pages] == [
        [finding.model_dump(mode="json") for finding in findings[:256]],
        [finding.model_dump(mode="json") for finding in findings[256:512]],
        [finding.model_dump(mode="json") for finding in findings[512:]],
    ]
    assert all(
        {key: value for key, value in page.items() if key != "findings"}
        == {
            "config_id": "cfg",
            "registry_version": 1,
            "package_checksum": "a" * 64,
            "destination_schema_fingerprint": "b" * 64,
            "offset": [0, 256, 512][pages.index(page)],
            "limit": 256,
            "total_findings": 600,
            "next_offset": [256, 512, None][pages.index(page)],
        }
        for page in pages
    )


def test_configuration_and_version_lists_return_every_service_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only validation findings paginate; registry lists keep all service records."""
    bearer = "admin-token-canary-0003"
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"admin": {"token": bearer, "administrator": True}}))
    resolver = EnvironmentPrincipalResolver.from_environment()

    class Service:
        ConfigsRequestError = configs_service.ConfigsRequestError
        ConfigsValidationError = configs_service.ConfigsValidationError
        ConfigsNotFoundError = configs_service.ConfigsNotFoundError
        ConfigsStorageError = configs_service.ConfigsStorageError
        ConfigsInternalError = configs_service.ConfigsInternalError
        ConfigsError = configs_service.ConfigsError

        @staticmethod
        def list_configs(**_kwargs: object) -> list[dict[str, object]]:
            return [
                {"config_id": f"cfg-{position}", "created_at": datetime.now().astimezone()} for position in range(300)
            ]

        @staticmethod
        def list_versions(**_kwargs: object) -> list[dict[str, object]]:
            return [
                {
                    "config_id": "cfg",
                    "registry_version": position + 1,
                    "package_checksum": f"{position:064x}",
                    "declared_content": {},
                    "created_at": datetime.now().astimezone(),
                }
                for position in range(300)
            ]

    client = TestClient(
        create_app(
            RunService(local_product_projection(tmp_path), _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(
                product_projection=local_product_projection(tmp_path), service=Service(), secrets=resolver.secret_values
            ),
        )
    )
    headers = {"Authorization": f"Bearer {bearer}"}
    assert len(client.get("/configs", headers=headers).json()) == 300
    assert len(client.get("/configs/cfg/versions", headers=headers).json()) == 300


@pytest.mark.parametrize(
    ("failure", "status", "family", "reason"),
    [
        (configs_service.ConfigsRequestError("hostile"), 400, "request", None),
        (configs_service.ConfigsValidationError("hostile"), 422, "validation", None),
        (
            configs_service.ConfigsNotFoundError("hostile", reason="configuration-not-found"),
            404,
            "not-found",
            "configuration-not-found",
        ),
        (
            configs_service.ConfigsNotFoundError("hostile", reason="configuration-version-not-found"),
            404,
            "not-found",
            "configuration-version-not-found",
        ),
        (configs_service.ConfigsStorageError("hostile"), 503, "storage", None),
        (configs_service.ConfigsInternalError("hostile"), 503, "internal", None),
        (configs_service.ConfigsError("hostile"), 503, "configs", None),
    ],
)
def test_configuration_error_matrix_preserves_only_declared_fields(  # noqa: PLR0913, PLR0917
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    status: int,
    family: str,
    reason: str | None,
) -> None:
    """Each shared-service failure maps to the fixed configuration envelope."""
    monkeypatch.setenv(
        PRINCIPALS_ENV, json.dumps({"admin": {"token": "admin-token-canary-0003", "administrator": True}})
    )
    resolver = EnvironmentPrincipalResolver.from_environment()
    runs = RunService(local_product_projection(tmp_path), _Orchestration(), secrets=resolver.secret_values)
    routes = ConfigurationRoutes(product_projection=local_product_projection(tmp_path))

    def fail(**_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(configs_service, "list_configs", fail)
    response = TestClient(create_app(runs, resolver, routes)).get(
        "/configs", headers={"Authorization": "Bearer admin-token-canary-0003"}
    )
    assert response.status_code == status
    assert response.json()["error"]["family"] == family
    assert response.json()["error"].get("reason") == reason


def test_configuration_routes_do_not_import_storage_or_validation_internals() -> None:
    """The HTTP adapter depends only on the shared service facade."""
    source = Path(__file__).parents[2] / "infrahub_sync" / "service" / "config_routes.py"
    imports = [
        node.module or "" for node in ast.walk(ast.parse(source.read_text())) if isinstance(node, ast.ImportFrom)
    ]
    assert all("product_store.store" not in module and "configuration.validation" not in module for module in imports)


def test_unknown_configs_subclass_uses_fixed_base_family(tmp_path: Path) -> None:
    """An extension refusal cannot influence the public family or message."""

    class Hostile(configs_service.ConfigsError):
        family = property(lambda _self: (_ for _ in ()).throw(AssertionError("metadata read")))

    class Service:
        ConfigsRequestError = configs_service.ConfigsRequestError
        ConfigsValidationError = configs_service.ConfigsValidationError
        ConfigsNotFoundError = configs_service.ConfigsNotFoundError
        ConfigsStorageError = configs_service.ConfigsStorageError
        ConfigsInternalError = configs_service.ConfigsInternalError
        ConfigsError = configs_service.ConfigsError

        @staticmethod
        def list_configs(**_kwargs: object) -> None:
            message = "do not disclose"
            raise Hostile(message)

    with pytest.raises(ConfigurationAPIError) as raised:
        ConfigurationRoutes(product_projection=local_product_projection(tmp_path), service=Service()).list_configs()
    assert raised.value.family == "configs"


def test_configs_request_subclass_uses_base_fallback_without_reflecting_metadata(tmp_path: Path) -> None:
    """Only the exact shared refusal classes receive a public family."""

    class Hostile(configs_service.ConfigsRequestError):
        message = property(lambda _self: (_ for _ in ()).throw(AssertionError("message read")))

    class Service:
        ConfigsRequestError = configs_service.ConfigsRequestError
        ConfigsValidationError = configs_service.ConfigsValidationError
        ConfigsNotFoundError = configs_service.ConfigsNotFoundError
        ConfigsStorageError = configs_service.ConfigsStorageError
        ConfigsInternalError = configs_service.ConfigsInternalError
        ConfigsError = configs_service.ConfigsError

        @staticmethod
        def list_configs(**_kwargs: object) -> None:
            raise Hostile("do not disclose")  # noqa: EM101, TRY003

    with pytest.raises(ConfigurationAPIError) as raised:
        ConfigurationRoutes(product_projection=local_product_projection(tmp_path), service=Service()).list_configs()
    assert raised.value.status == 503
    assert raised.value.family == "configs"


def test_configs_not_found_subclass_cannot_leak_reason(tmp_path: Path) -> None:
    """A not-found extension gets the fixed base response without its reason property."""

    class Hostile(configs_service.ConfigsNotFoundError):
        reason = property(
            lambda _self: (_ for _ in ()).throw(AssertionError("reason read")), lambda _self, _value: None
        )

    class Service:
        ConfigsRequestError = configs_service.ConfigsRequestError
        ConfigsValidationError = configs_service.ConfigsValidationError
        ConfigsNotFoundError = configs_service.ConfigsNotFoundError
        ConfigsStorageError = configs_service.ConfigsStorageError
        ConfigsInternalError = configs_service.ConfigsInternalError
        ConfigsError = configs_service.ConfigsError

        @staticmethod
        def list_configs(**_kwargs: object) -> None:
            raise Hostile("do not disclose", reason="secret-reason")  # noqa: EM101, TRY003

    with pytest.raises(ConfigurationAPIError) as raised:
        ConfigurationRoutes(product_projection=local_product_projection(tmp_path), service=Service()).list_configs()
    assert raised.value.status == 503
    assert raised.value.family == "configs"
    assert raised.value.reason is None


def test_create_app_keeps_run_and_configuration_dependencies_separate(tmp_path: Path) -> None:
    """One route from each family touches only its explicitly supplied dependency."""

    class Runs:
        def __init__(self) -> None:
            self.calls: list[str] = []

        @staticmethod
        def record_authentication_refusal(_path: str, _reason: str) -> None:
            return None

        def get_artifact(self, run_id: str, artifact_id: str) -> tuple[bytes, str, str]:
            self.calls.append(run_id)
            return (artifact_id.encode(), "text/plain", "a" * 64)

    class ConfigService:
        ConfigsRequestError = configs_service.ConfigsRequestError
        ConfigsValidationError = configs_service.ConfigsValidationError
        ConfigsNotFoundError = configs_service.ConfigsNotFoundError
        ConfigsStorageError = configs_service.ConfigsStorageError
        ConfigsInternalError = configs_service.ConfigsInternalError
        ConfigsError = configs_service.ConfigsError

        def __init__(self) -> None:
            self.calls: list[str] = []

        def list_configs(self, **_kwargs: object) -> list[dict[str, str]]:
            self.calls.append("list_configs")
            return []

    class Resolver:
        @staticmethod
        def resolve(_token: str) -> object:
            return type("Principal", (), {"administrator": True})()

    runs = Runs()
    config_service = ConfigService()
    client = TestClient(
        create_app(
            cast("RunService", runs),
            cast("PrincipalResolver", Resolver()),
            ConfigurationRoutes(product_projection=local_product_projection(tmp_path), service=config_service),
        )
    )
    headers = {"Authorization": "Bearer accepted"}
    assert client.get("/runs/run-a/artifacts/one", headers=headers).status_code == 200
    assert client.get("/configs", headers=headers).status_code == 200
    assert runs.calls == ["run-a"]
    assert config_service.calls == ["list_configs"]


def test_build_app_binds_one_projection_and_passes_configuration_dependency() -> None:
    """Runtime construction supplies one durable projection to both families."""
    received: list[object] = []
    route_dependency = object()
    projection_dependency = object()

    def projection() -> object:
        received.append("projection")
        return projection_dependency

    class Resolver:
        secret_values: tuple[str, ...] = ()

    def route_factory(*, product_projection: object, secrets: tuple[str, ...]) -> object:
        assert secrets == ()
        assert product_projection is projection_dependency
        return route_dependency

    def application(run: object, resolver: object, routes: object, reconciler: object) -> object:
        del reconciler
        received.extend((run, resolver, routes))
        return object()

    assert (
        build_app(
            projection_factory=projection,
            resolver_factory=Resolver,
            run_service_factory=lambda *_args, **_kwargs: object(),
            configuration_routes_factory=route_factory,
            app_factory=application,
        )
        is not None
    )
    assert received[0] == "projection"
    assert received[-1] is route_dependency


def test_build_app_composes_one_managed_projection_for_runs_and_configurations() -> None:
    """The deployed API has one environment-owned product projection."""
    projection = object()
    received: list[object] = []

    class Resolver:
        secret_values: tuple[str, ...] = ()

    def storage_factory() -> object:
        received.append("storage")
        return projection

    def routes_factory(*, product_projection: object, secrets: tuple[str, ...]) -> object:
        assert product_projection is projection
        assert secrets == ()
        received.append("routes")
        return object()

    def app_factory(run_service: object, resolver: object, routes: object, reconciler: object) -> object:
        del run_service, resolver, routes, reconciler
        received.append("app")
        return object()

    assert (
        build_app(
            projection_factory=storage_factory,
            resolver_factory=Resolver,
            run_service_factory=lambda value, *_args, **_kwargs: received.append(value) or object(),
            configuration_routes_factory=routes_factory,
            app_factory=app_factory,
        )
        is not None
    )
    assert received == ["storage", "routes", projection, "app"]


def test_build_app_uses_the_prefect_worker_query_setting_for_liveness_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server and Preview can use one Prefect query cadence for liveness."""
    monkeypatch.setenv("PREFECT_WORKER_QUERY_SECONDS", "20")
    captured: list[RunLivenessReconciler] = []

    class Resolver:
        secret_values: tuple[str, ...] = ()

    def app_factory(_service: object, _resolver: object, _routes: object, reconciler: RunLivenessReconciler) -> object:
        captured.append(reconciler)
        return object()

    build_app(
        projection_factory=object,
        resolver_factory=Resolver,
        run_service_factory=lambda *_args, **_kwargs: object(),
        configuration_routes_factory=lambda **_kwargs: object(),
        app_factory=app_factory,
    )

    assert captured[0]._policy.stall_threshold_seconds == 60


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS", "0"),
        ("PREFECT_WORKER_QUERY_SECONDS", "nan"),
    ],
)
def test_build_app_validates_complete_liveness_policy_before_constructing_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    value: str,
) -> None:
    """Invalid startup policy has no projection, resolver, route, service, or app side effect."""
    monkeypatch.setenv(setting, value)
    constructed: list[str] = []

    def factory(name: str):
        def construct(*_args: object, **_kwargs: object) -> object:
            constructed.append(name)
            return object()

        return construct

    with pytest.raises(ValueError):
        build_app(
            projection_factory=factory("projection"),
            resolver_factory=factory("resolver"),
            run_service_factory=factory("service"),
            configuration_routes_factory=factory("routes"),
            app_factory=factory("app"),
        )

    assert constructed == []
