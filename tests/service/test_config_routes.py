"""Focused transport checks for configuration HTTP resources."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING, cast

import pytest
import yaml

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.client import RegisteredConfigurationResource, RegisteredVersionResource
from infrahub_sync.configuration import schema_validation
from infrahub_sync.configuration.capabilities import BUILTIN_ADAPTER_CAPABILITIES, DestinationSchemaReadError
from infrahub_sync.configuration.models import ValidationFinding
from infrahub_sync.configuration.validation import collect_findings
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store import (
    ConfigurationPage,
    PrimaryResource,
    ProductRun,
    RelatedResource,
    local_product_projection,
)
from infrahub_sync.product_store import configs as configs_service
from infrahub_sync.product_store.configs import ValidationReport
from infrahub_sync.service import serve
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import Principal, Resolution
from infrahub_sync.service.config_routes import ConfigurationAPIError, ConfigurationRoutes
from infrahub_sync.service.orchestration import CancellationResult, Observation, PoolStatus, RunLogRead, Submission
from infrahub_sync.service.paging import timestamp_cursor
from infrahub_sync.service.serve import build_app
from infrahub_sync.service.service import RunService, ServiceAPIError
from tests.configuration.validation_packages import package, package_data
from tests.service.accounts import account_identifier, seed_accounts
from tests.service.schedule_fixtures import NoDeploymentSchedules

ADMIN_TOKEN = "admin-token-canary-0003"  # noqa: S105 - deliberate non-secret boundary canary.
OTHER_TOKEN = "reader-token-canary-0004"  # noqa: S105 - deliberate non-secret boundary canary.

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import NoReturn

    import httpx

    from infrahub_sync.configuration.models import ConfigurationPackage
    from infrahub_sync.product_store.models import ConfigurationVersion
    from infrahub_sync.product_store.store import ProductProjection
    from infrahub_sync.service.auth import PrincipalResolver
    from infrahub_sync.service.liveness import RunLivenessReconciler


class _Orchestration(NoDeploymentSchedules):  # pylint: disable=too-few-public-methods
    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: ARG002, PLR6301
        return Submission(flow_run_id="test-flow", state="pending")

    async def observe(self, flow_run_id: str) -> Observation:  # noqa: ARG002, PLR6301
        return Observation(available=True, state="running")

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:  # noqa: ARG002, PLR6301
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:  # noqa: ARG002, PLR6301
        return CancellationResult(acknowledged=True)

    async def read_logs(self, flow_run_ids: Sequence[str]) -> RunLogRead:  # noqa: PLR6301
        del flow_run_ids
        return RunLogRead(expired=False)


class _UnknownConfigsError(configs_service.ConfigsError):
    """An unrecognized service error remains conservatively non-retryable."""


def test_configuration_routes_register_then_read(tmp_path: Path) -> None:
    """A configuration resource delegates registration and its scoped reads."""
    projection = local_product_projection(tmp_path)
    resolver = seed_accounts(
        projection, {"admin": {"token": "admin-token-canary-0003", "administrator": True}}
    ).resolver
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


def test_configuration_routes_use_the_injected_projection_for_services_receipts_and_audit(tmp_path: Path) -> None:
    """Every configuration path uses the one projection supplied at composition."""

    bearer = "admin-token-canary-0003"
    delegate = local_product_projection(tmp_path)
    resolver = seed_accounts(delegate, {"admin": {"token": bearer, "administrator": True}}).resolver

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
        "list_configuration_page",
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


def test_configuration_http_provider_errors_preserve_storage_and_internal_families(tmp_path: Path) -> None:
    """Marked projection failures are storage responses at every configuration HTTP boundary."""
    from infrahub_sync.product_store import ProductStoreProviderError

    admin_token = ADMIN_TOKEN
    reader_token = OTHER_TOKEN
    run_projection = local_product_projection(tmp_path)
    resolver = seed_accounts(
        run_projection,
        {
            "admin": {"token": admin_token, "administrator": True},
            "reader": {"token": reader_token},
        },
    ).resolver

    class StorageProjection:
        @staticmethod
        def list_configuration_page(**_kwargs: object) -> NoReturn:
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
        def list_configuration_page(**_kwargs: object) -> NoReturn:
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
        "list_configuration_page",
        "lookup_configuration",
        "list_configuration_version_page",
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
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each projection call contains provider details and classifies unmarked defects."""
    from infrahub_sync.product_store import ProductStoreProviderError

    admin_token = ADMIN_TOKEN
    reader_token = OTHER_TOKEN
    delegate = local_product_projection(tmp_path)
    resolver = seed_accounts(
        delegate,
        {
            "admin": {"token": admin_token, "administrator": True},
            "reader": {"token": reader_token, "administrator": False},
        },
    ).resolver
    # Another name than the package each request registers, which is unique among configurations.
    version = delegate.create_configuration(package(name="registered-before"))
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

    if boundary == "list_configuration_page":
        response = client.get("/configs", headers=admin_headers)
    elif boundary == "lookup_configuration":
        response = client.get(f"/configs/{version.config_id}", headers=admin_headers)
    elif boundary == "list_configuration_version_page":
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


def test_configuration_mutation_replays_exact_response_and_rejects_changed_content(tmp_path: Path) -> None:
    """A configuration mutation uses the one durable receipt rather than a second write."""
    projection = local_product_projection(tmp_path)
    resolver = seed_accounts(
        projection, {"admin": {"token": "admin-token-canary-0003", "administrator": True}}
    ).resolver
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
    receipt = projection.lookup_mutation(account_identifier(projection, "admin"), sha256(b"register-once").hexdigest())
    assert receipt.value is not None
    assert receipt.value.state == "accepted"


def test_duplicate_configuration_version_checksum_returns_existing_version_without_a_second_write(
    tmp_path: Path,
) -> None:
    """A checksum already stored for a configuration is a 200 replay, not a new version."""
    bearer = "admin-token-canary-0003"
    projection = local_product_projection(tmp_path)
    resolver = seed_accounts(projection, {"admin": {"token": bearer, "administrator": True}}).resolver
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
    tmp_path: Path,
) -> None:
    """Every mutation decision is durable evidence and contains no bearer secret."""
    bearer = "admin-token-canary-0003"
    projection = local_product_projection(tmp_path)
    resolver = seed_accounts(projection, {"admin": {"token": bearer, "administrator": True}}).resolver
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
    error_type: type[configs_service.ConfigsError],
    *,
    status: int,
    released: bool,
) -> None:
    """Only declared pre-effect families make a configuration receipt retryable."""
    bearer = "admin-token-canary-0003"
    resolver = seed_accounts(
        local_product_projection(tmp_path), {"admin": {"token": bearer, "administrator": True}}
    ).resolver

    class Service:
        ConfigsRequestError = configs_service.ConfigsRequestError
        ConfigsValidationError = configs_service.ConfigsValidationError
        ConfigsNotFoundError = configs_service.ConfigsNotFoundError
        ConfigsStorageError = configs_service.ConfigsStorageError
        ConfigsInternalError = configs_service.ConfigsInternalError
        ConfigsNameInUseError = configs_service.ConfigsNameInUseError
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
    reserved = projection.lookup_mutation(
        account_identifier(projection, "admin"), sha256(headers["Idempotency-Key"].encode()).hexdigest()
    ).value
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

        def create_configuration(  # noqa: PLR6301
            self, package: ConfigurationPackage, **registration: object
        ) -> ConfigurationVersion:
            nonlocal writes
            writes += 1
            return projection.create_configuration(package, **registration)  # ty: ignore[invalid-argument-type]

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
        ConfigsNameInUseError = configs_service.ConfigsNameInUseError
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
        ConfigsNameInUseError = configs_service.ConfigsNameInUseError
        ConfigsError = configs_service.ConfigsError

        @staticmethod
        def list_configs(**_kwargs: object) -> None:
            raise AssertionError("unrelated defect")  # noqa: EM101, TRY003 - the assertion is the transport boundary probe.

    with pytest.raises(AssertionError, match="unrelated defect"):
        ConfigurationRoutes(product_projection=local_product_projection(tmp_path), service=Service()).list_configs()


def test_configuration_mutation_refuses_unauthenticated_and_non_admin_calls(tmp_path: Path) -> None:
    """Authorization refusal occurs before configuration reservation or service invocation."""
    projection = local_product_projection(tmp_path)
    resolver = seed_accounts(
        projection,
        {
            "admin": {"token": "admin-token-canary-0003", "administrator": True},
            "reader": {"token": "reader-token-canary-0002", "administrator": False},
        },
    ).resolver
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


def test_configuration_route_grammar_refuses_hostile_values_before_service_classification(tmp_path: Path) -> None:
    """Path, page, and body grammar failures use the fixed request envelope without a service call."""
    bearer = "admin-token-canary-0003"
    resolver = seed_accounts(
        local_product_projection(tmp_path), {"admin": {"token": bearer, "administrator": True}}
    ).resolver

    class Service:
        ConfigsRequestError = configs_service.ConfigsRequestError
        ConfigsValidationError = configs_service.ConfigsValidationError
        ConfigsNotFoundError = configs_service.ConfigsNotFoundError
        ConfigsStorageError = configs_service.ConfigsStorageError
        ConfigsInternalError = configs_service.ConfigsInternalError
        ConfigsNameInUseError = configs_service.ConfigsNameInUseError
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
        ("get", "/configs?limit=true", {}),
        ("get", "/configs?limit=1.0", {}),
        ("get", "/configs?limit=0", {}),
        ("get", "/configs?limit=201", {}),
        ("get", "/configs/good/versions?limit=201", {}),
        ("get", "/configs/not%20an%20id", {}),
        ("get", "/configs/good/versions/true", {}),
        ("get", "/configs/good/versions/1.0", {}),
        ("get", "/configs/good/versions/0", {}),
        ("get", "/configs/good/versions/" + "9" * 5000, {}),
        ("get", "/configs?limit=" + "1" * 5000, {}),
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


def test_validation_pages_are_bounded_and_concatenate_to_the_single_snapshot(tmp_path: Path) -> None:
    """Pages slice one ordered validation report without changing its snapshot fields."""
    bearer = "admin-token-canary-0003"
    resolver = seed_accounts(
        local_product_projection(tmp_path), {"admin": {"token": bearer, "administrator": True}}
    ).resolver
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
        ConfigsNameInUseError = configs_service.ConfigsNameInUseError
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
    resolver = seed_accounts(
        local_product_projection(tmp_path), {"admin": {"token": "admin-token-canary-0003", "administrator": True}}
    ).resolver
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
        ConfigsNameInUseError = configs_service.ConfigsNameInUseError
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
        ConfigsNameInUseError = configs_service.ConfigsNameInUseError
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
        ConfigsNameInUseError = configs_service.ConfigsNameInUseError
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
        ConfigsNameInUseError = configs_service.ConfigsNameInUseError
        ConfigsError = configs_service.ConfigsError

        def __init__(self) -> None:
            self.calls: list[str] = []

        def list_configs(self, **_kwargs: object) -> ConfigurationPage:
            self.calls.append("list_configs")
            return ConfigurationPage(items=(), has_more=False)

    class Resolver:
        @staticmethod
        def resolve(_token: str) -> object:
            return Resolution.accepted(Principal(actor="operator", administrator=True))

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


def _record_matches(_work_pool: str) -> None:
    """Stand in for a bootstrap record that names this version and work pool."""


def test_build_app_binds_one_projection_and_passes_configuration_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime construction supplies one durable projection to both families."""
    monkeypatch.setattr(serve, "collect_secret_values", tuple)
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

    def application(run: object, resolver: object, routes: object, reconciler: object, **_schedules: object) -> object:
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
            start_check=_record_matches,
        )
        is not None
    )
    assert received[0] == "projection"
    assert received[-1] is route_dependency


def test_build_app_composes_one_service_projection_for_runs_and_configurations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deployed API has one environment-owned product projection."""
    monkeypatch.setattr(serve, "collect_secret_values", tuple)
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

    def app_factory(
        run_service: object, resolver: object, routes: object, reconciler: object, **_schedules: object
    ) -> object:
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
            start_check=_record_matches,
        )
        is not None
    )
    assert received == ["storage", "routes", projection, "app"]


def test_build_app_uses_the_prefect_worker_query_setting_for_liveness_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server and the development stack can use one Prefect query cadence for liveness."""
    monkeypatch.setenv("PREFECT_WORKER_QUERY_SECONDS", "20")
    captured: list[RunLivenessReconciler] = []

    class Resolver:
        secret_values: tuple[str, ...] = ()

    def app_factory(
        _service: object, _resolver: object, _routes: object, reconciler: RunLivenessReconciler, **_schedules: object
    ) -> object:
        captured.append(reconciler)
        return object()

    build_app(
        projection_factory=object,
        resolver_factory=Resolver,
        run_service_factory=lambda *_args, **_kwargs: object(),
        configuration_routes_factory=lambda **_kwargs: object(),
        app_factory=app_factory,
        start_check=_record_matches,
    )

    assert captured[0]._policy.stall_threshold_seconds == 60


@pytest.mark.parametrize(
    ("value", "expected"),
    [("http://127.0.0.1:4230/", "http://127.0.0.1:4230"), ("", None)],
    ids=("set", "empty"),
)
def test_build_app_passes_the_validated_prefect_ui_url_to_the_run_service(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: str | None
) -> None:
    """US3: the run service builds Prefect UI links from the validated setting, or none when it is empty."""
    monkeypatch.setenv("INFRAHUB_SYNC_PREFECT_UI_URL", value)
    received: list[object] = []

    class Resolver:
        secret_values: tuple[str, ...] = ()

    def run_service_factory(*_args: object, **kwargs: object) -> object:
        received.append(kwargs["prefect_ui_url"])
        return object()

    build_app(
        projection_factory=object,
        resolver_factory=Resolver,
        run_service_factory=run_service_factory,
        configuration_routes_factory=lambda **_kwargs: object(),
        app_factory=lambda *_args, **_kwargs: object(),
        start_check=_record_matches,
    )

    assert received == [expected]


def test_build_app_refuses_an_invalid_prefect_ui_url_before_constructing_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid Prefect UI address stops start-up with a message that does not print the value."""
    monkeypatch.setenv("INFRAHUB_SYNC_PREFECT_UI_URL", "http://user:secret@prefect")
    constructed: list[str] = []

    def construct(*_args: object, **_kwargs: object) -> object:
        constructed.append("dependency")
        return object()

    with pytest.raises(ValueError, match="INFRAHUB_SYNC_PREFECT_UI_URL") as refused:
        build_app(
            projection_factory=construct,
            resolver_factory=construct,
            run_service_factory=construct,
            configuration_routes_factory=construct,
            app_factory=construct,
        )

    assert "secret" not in str(refused.value)
    assert constructed == []


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


@pytest.mark.parametrize(
    ("retry_reason", "replayed"),
    [
        pytest.param({}, True, id="absent-then-absent"),
        pytest.param({"reason": None}, True, id="absent-then-null"),
        pytest.param({"reason": "now with a reason"}, False, id="absent-then-present"),
    ],
)
def test_configuration_mutation_without_a_reason_replays_by_reason(
    tmp_path: Path, retry_reason: dict[str, object], *, replayed: bool
) -> None:
    """A registration may omit its reason; an omitted reason is one value for idempotency."""
    projection = local_product_projection(tmp_path)
    resolver = seed_accounts(
        projection, {"admin": {"token": "admin-token-canary-0003", "administrator": True}}
    ).resolver
    client = TestClient(
        create_app(
            RunService(projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(product_projection=local_product_projection(tmp_path), secrets=resolver.secret_values),
        )
    )
    headers = {"Authorization": "Bearer admin-token-canary-0003", "Idempotency-Key": "register-without-reason"}

    first = client.post("/configs", headers=headers, json={"package": package_data()})
    retry = client.post("/configs", headers=headers, json={"package": package_data(), **retry_reason})

    assert first.status_code == 201, first.text
    assert retry.status_code == (201 if replayed else 409)
    if replayed:
        assert retry.json() == first.json()
    assert len(projection.list_configurations()) == 1
    receipt = projection.lookup_mutation(
        account_identifier(projection, "admin"), sha256(b"register-without-reason").hexdigest()
    ).value
    assert receipt is not None
    assert receipt.reason is None
    accepted = [event for event in projection.audit_events() if event.outcome == "accepted"]
    assert [event.reason for event in accepted] == [None]


# Discoverable configurations (US2): paged lists with names, versions and their latest runs.

_DISCOVERY_T0 = datetime(2026, 11, 4, 9, tzinfo=timezone.utc)


def _discovery_client(tmp_path: Path) -> tuple[TestClient, ProductProjection]:
    """Return an administrator client and the one projection both route families use."""
    projection = local_product_projection(tmp_path)
    resolver = seed_accounts(projection, {"admin": {"token": ADMIN_TOKEN, "administrator": True}}).resolver
    client = TestClient(
        create_app(
            RunService(projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(product_projection=projection, secrets=resolver.secret_values),
        )
    )
    client.headers.update({"Authorization": f"Bearer {ADMIN_TOKEN}"})
    return client, projection


def _named_package(name: str, url: str = "https://demo.netbox.dev") -> dict[str, object]:
    data = package_data()
    data["configuration"]["name"] = name
    data["configuration"]["source"]["settings"]["url"] = url
    return data


def _register(client: TestClient, data: dict[str, object], key: str) -> str:
    response = client.post("/configs", headers={"Idempotency-Key": key}, json={"package": data, "reason": key})
    assert response.status_code == 201, response.text
    return str(response.json()["configuration"]["config_id"])


def _add_version(client: TestClient, config_id: str, data: dict[str, object], key: str) -> None:
    response = client.post(
        f"/configs/{config_id}/versions", headers={"Idempotency-Key": key}, json={"package": data, "reason": key}
    )
    assert response.status_code == 201, response.text


def _bound_run(run_id: str, minutes: int, config_id: str, registry_version: int, phase: str) -> ProductRun:
    return ProductRun(
        run_id=run_id,
        operation="plan",
        configuration_reference=f"{config_id}@{registry_version}",
        config_id=config_id,
        registry_version=registry_version,
        package_checksum="a" * 64,
        started_at=_DISCOVERY_T0 + timedelta(minutes=minutes),
        phase=phase,
    )


def test_configuration_list_is_a_page_newest_first_with_name_and_latest_version(tmp_path: Path) -> None:
    client, _projection = _discovery_client(tmp_path)
    netbox = _register(client, _named_package("netbox-inventory"), "register-netbox")
    _add_version(client, netbox, _named_package("netbox-inventory", "https://second.netbox.test"), "version-2")
    other = _register(client, _named_package("other-inventory"), "register-other")

    response = client.get("/configs")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["next_cursor"] is None
    keys = [(item["created_at"], item["config_id"]) for item in body["items"]]
    assert keys == sorted(keys, reverse=True)
    assert {item["config_id"]: (item["name"], item["latest_version"]) for item in body["items"]} == {
        netbox: ("netbox-inventory", 2),
        other: ("other-inventory", 1),
    }


def test_name_filter_returns_the_configuration_with_the_name(tmp_path: Path) -> None:
    client, _projection = _discovery_client(tmp_path)
    named = _register(client, _named_package("named"), "register-named")
    _register(client, _named_package("other-name"), "register-other")

    response = client.get("/configs", params={"name": "named"})

    assert response.status_code == 200
    assert [item["config_id"] for item in response.json()["items"]] == [named]


def test_configuration_summary_includes_name_and_latest_version(tmp_path: Path) -> None:
    client, _projection = _discovery_client(tmp_path)
    config_id = _register(client, _named_package("netbox-inventory"), "register")
    _add_version(client, config_id, _named_package("renamed-inventory", "https://second.netbox.test"), "version-2")

    response = client.get(f"/configs/{config_id}")

    assert response.status_code == 200
    assert (response.json()["name"], response.json()["latest_version"]) == ("renamed-inventory", 2)


def test_version_list_returns_summaries_with_each_versions_latest_run_and_latest_applied_run(tmp_path: Path) -> None:
    client, projection = _discovery_client(tmp_path)
    config_id = _register(client, _named_package("netbox-inventory"), "register")
    _add_version(client, config_id, _named_package("netbox-inventory", "https://second.netbox.test"), "version-2")
    _add_version(client, config_id, _named_package("netbox-inventory", "https://third.netbox.test"), "version-3")
    for run in (
        _bound_run("run-v1-plan", 0, config_id, 1, "planned"),
        _bound_run("run-v1-apply", 1, config_id, 1, "applied"),
        _bound_run("run-v2-plan", 2, config_id, 2, "planned"),
    ):
        projection.create_run(run)

    response = client.get(f"/configs/{config_id}/versions")

    assert response.status_code == 200
    items = response.json()["items"]
    assert response.json()["next_cursor"] is None
    assert [item["registry_version"] for item in items] == [3, 2, 1]
    assert all("declared_content" not in item for item in items)
    assert all(item["registered_by"] == "admin" for item in items)
    assert [item["registration_reason"] for item in items] == ["version-3", "version-2", "register"]

    def run_ids(item: dict[str, object]) -> tuple[object, object]:
        latest, applied = item["latest_run"], item["latest_applied_run"]
        return (
            None if latest is None else cast("dict[str, object]", latest)["run_id"],
            None if applied is None else cast("dict[str, object]", applied)["run_id"],
        )

    assert [run_ids(item) for item in items] == [
        (None, None),
        ("run-v2-plan", None),
        ("run-v1-apply", "run-v1-apply"),
    ]
    assert items[2]["latest_applied_run"]["phase"] == "applied"


def test_limit_limits_each_page_and_cursor_returns_the_next_page(tmp_path: Path) -> None:
    """The paging parameters take effect on both lists; they were once accepted and ignored."""
    client, _projection = _discovery_client(tmp_path)
    config_ids = {_register(client, _named_package(f"config-{index}"), f"register-{index}") for index in range(3)}
    config_id = next(iter(config_ids))
    for index in (2, 3):
        _add_version(client, config_id, _named_package("config-v", f"https://v{index}.netbox.test"), f"version-{index}")

    first = client.get("/configs", params={"limit": 2}).json()
    rest = client.get("/configs", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    first_versions = client.get(f"/configs/{config_id}/versions", params={"limit": 2}).json()
    rest_versions = client.get(
        f"/configs/{config_id}/versions", params={"limit": 2, "cursor": first_versions["next_cursor"]}
    ).json()

    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None
    assert len(rest["items"]) == 1
    assert rest["next_cursor"] is None
    assert {item["config_id"] for item in first["items"] + rest["items"]} == config_ids
    assert [item["registry_version"] for item in first_versions["items"]] == [3, 2]
    assert [item["registry_version"] for item in rest_versions["items"]] == [1]
    assert rest_versions["next_cursor"] is None


def test_list_routes_declare_cursor_paging_and_no_offset(tmp_path: Path) -> None:
    client, _projection = _discovery_client(tmp_path)
    paths = client.get("/openapi.json").json()["paths"]

    def parameters(path: str) -> set[str]:
        return {parameter["name"] for parameter in paths[path]["get"]["parameters"] if parameter["in"] == "query"}

    assert parameters("/configs") == {"name", "limit", "cursor"}
    assert parameters("/configs/{config_id}/versions") == {"limit", "cursor"}


def test_version_list_of_an_unknown_configuration_is_404(tmp_path: Path) -> None:
    client, _projection = _discovery_client(tmp_path)

    response = client.get("/configs/config-unknown/versions")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "configs-not-found"
    assert response.json()["error"]["reason"] == "configuration-not-found"


def test_invalid_cursor_is_answered_with_422_in_the_error_envelope(tmp_path: Path) -> None:
    """A cursor that does not decode, or was issued for another list, is refused with 422."""
    client, _projection = _discovery_client(tmp_path)
    config_id = _register(client, _named_package("netbox-inventory"), "register")
    _add_version(client, config_id, _named_package("netbox-inventory", "https://second.netbox.test"), "version-2")
    version_cursor = client.get(f"/configs/{config_id}/versions", params={"limit": 1}).json()["next_cursor"]
    expected = {
        "error": {
            "code": "invalid-cursor",
            "message": "the cursor is not valid for this request",
            "status": 422,
            "run_id": None,
            "mutation_id": None,
        }
    }

    for response in (
        client.get("/configs", params={"cursor": "garbage"}),
        client.get("/configs", params={"cursor": version_cursor}),
        client.get(f"/configs/{config_id}/versions", params={"cursor": "garbage"}),
    ):
        assert response.status_code == 422
        assert response.json() == expected


def test_a_cursor_is_refused_by_another_filter_configuration_or_list(tmp_path: Path) -> None:
    """A cursor is bound to its list and its filters, even when both lists are unfiltered."""
    client, projection = _discovery_client(tmp_path)
    alpha = _register(client, _named_package("alpha"), "register-alpha-1")
    beta = _register(client, _named_package("beta"), "register-beta-1")
    _add_version(client, alpha, _named_package("alpha", "https://alpha-v2.netbox.test"), "alpha-version-2")
    for index in range(2):
        projection.create_run(_bound_run(f"bound-run-{index:03d}", index, alpha, 1, "accepted"))

    # A name is unique, so a name filter returns one page; the cursor is the one it would issue.
    alpha_created = client.get(f"/configs/{alpha}").json()["created_at"]
    name_cursor = timestamp_cursor(
        datetime.fromisoformat(alpha_created.replace("Z", "+00:00")), alpha, {"list": "configs", "name": "alpha"}
    )
    version_cursor = client.get(f"/configs/{alpha}/versions", params={"limit": 1}).json()["next_cursor"]
    configs_cursor = client.get("/configs", params={"limit": 1}).json()["next_cursor"]
    runs_cursor = client.get("/runs", params={"limit": 1}).json()["next_cursor"]
    assert None not in {name_cursor, version_cursor, configs_cursor, runs_cursor}
    # Each cursor is still accepted by the request it was issued for.
    assert client.get("/configs", params={"name": "alpha", "cursor": name_cursor}).status_code == 200
    assert client.get("/runs", params={"cursor": runs_cursor}).status_code == 200

    for response in (
        client.get("/configs", params={"name": "beta", "cursor": name_cursor}),
        client.get(f"/configs/{beta}/versions", params={"cursor": version_cursor}),
        client.get("/runs", params={"cursor": configs_cursor}),
        client.get("/configs", params={"cursor": runs_cursor}),
    ):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid-cursor"


def _event_client(tmp_path: Path) -> tuple[TestClient, ProductProjection]:
    """Return a client that holds no token, and the projection both route families use."""
    projection = local_product_projection(tmp_path)
    resolver = seed_accounts(
        projection, {"admin": {"token": ADMIN_TOKEN, "administrator": True}, "viewer": {"token": OTHER_TOKEN}}
    ).resolver
    client = TestClient(
        create_app(
            RunService(projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(product_projection=projection, secrets=resolver.secret_values),
        )
    )
    return client, projection


def _typed_config_events(projection: ProductProjection) -> list[tuple[str | None, str, str, str | None, int | None]]:
    return [
        (event.event_type, event.operation, event.outcome, event.config_id, event.registry_version)
        for event in projection.audit_events()
    ]


def test_configuration_decisions_record_typed_configuration_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-012: registration, a new version, a retrieved version and a replay each name the configuration and version."""
    client, projection = _event_client(tmp_path)
    admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    record_audit = projection.record_audit

    def guarded(event: object, *, secrets: tuple[str, ...] = ()) -> None:
        assert getattr(event, "outcome", None) != "accepted", "an accepted event must share the decision's transaction"
        record_audit(event, secrets=secrets)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(projection, "record_audit", guarded)

    registered = client.post(
        "/configs", headers={**admin, "Idempotency-Key": "register"}, json={"package": package_data(), "reason": "new"}
    )
    assert registered.status_code == 201
    config_id = registered.json()["configuration"]["config_id"]
    version = client.post(
        f"/configs/{config_id}/versions",
        headers={**admin, "Idempotency-Key": "version"},
        json={"package": _named_package("renamed"), "reason": "second"},
    )
    assert version.status_code == 201
    retrieved = client.post(
        f"/configs/{config_id}/versions",
        headers={**admin, "Idempotency-Key": "same-content"},
        json={"package": _named_package("renamed"), "reason": "again"},
    )
    assert retrieved.status_code == 200
    assert (
        client.post(
            "/configs",
            headers={**admin, "Idempotency-Key": "register"},
            json={"package": package_data(), "reason": "new"},
        ).status_code
        == 201
    )

    assert _typed_config_events(projection) == [
        ("infrahub_sync.config.registered", "register-config", "accepted", config_id, 1),
        ("infrahub_sync.config.version_registered", "create-config-version", "accepted", config_id, 2),
        ("infrahub_sync.config.version_retrieved", "create-config-version", "accepted", config_id, 2),
        ("infrahub_sync.config.replayed", "register-config", "replayed", config_id, 1),
    ]
    for event in projection.audit_events():
        assert event.run_id is None
        assert event.primary_resource == PrimaryResource(kind="config", id=config_id)
        assert event.related == (
            RelatedResource(kind="account", id=account_identifier(projection, "admin")),
            RelatedResource(kind="config", id=config_id),
            RelatedResource(kind="config_version", id=f"{config_id}@{event.registry_version}"),
        )


def test_configuration_refusals_record_configuration_refused_events(tmp_path: Path) -> None:
    """FR-012: an authorization or idempotency refusal is typed `config.refused`; a bad token gets no type."""
    client, projection = _event_client(tmp_path)
    admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    viewer = {"Authorization": f"Bearer {OTHER_TOKEN}"}
    body = {"package": package_data(), "reason": "new"}
    config_id = _register_with(client, admin, body)

    assert client.post("/configs", headers={**viewer, "Idempotency-Key": "v1"}, json=body).status_code == 403
    assert (
        client.post(
            f"/configs/{config_id}/versions", headers={**viewer, "Idempotency-Key": "v2"}, json=body
        ).status_code
        == 403
    )
    changed = {**body, "reason": "changed"}
    assert client.post("/configs", headers={**admin, "Idempotency-Key": "register"}, json=changed).status_code == 409
    assert (
        client.post("/configs", headers={"Authorization": "Bearer nope", "Idempotency-Key": "x"}, json=body).status_code
        == 401
    )

    refusals = [event for event in projection.audit_events() if event.outcome.startswith("refused-")]
    assert [(event.event_type, event.operation, event.outcome, event.config_id) for event in refusals] == [
        ("infrahub_sync.config.refused", "register-config", "refused-authorization", None),
        ("infrahub_sync.config.refused", "create-config-version", "refused-authorization", config_id),
        ("infrahub_sync.config.refused", "register-config", "refused-idempotency", None),
        (None, "/configs", "refused-authentication", None),
    ]
    version_refusal = refusals[1]
    assert version_refusal.primary_resource == PrimaryResource(kind="config", id=config_id)
    assert version_refusal.related == (
        RelatedResource(kind="account", id=account_identifier(projection, "viewer")),
        RelatedResource(kind="config", id=config_id),
    )


def _register_with(client: TestClient, headers: dict[str, str], body: Mapping[str, object]) -> str:
    response = client.post("/configs", headers={**headers, "Idempotency-Key": "register"}, json=body)
    assert response.status_code == 201, response.text
    return str(response.json()["configuration"]["config_id"])


# Spec 007 US5 (T061, T065): `configs validate --branch` and the registration responses.

_BRANCH_SNAPSHOT: dict[str, object] = {
    "InfraDevice": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {"name": {"kind": "Text", "optional": False, "default_value": None, "unique": True}},
        "relationships": {
            "site": {"peer": "LocationSite", "cardinality": "one", "optional": True, "kind": "Attribute"},
        },
    },
    "LocationSite": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {"name": {"kind": "Text", "optional": False, "default_value": None, "unique": True}},
        "relationships": {},
    },
}


def _spied_schema_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the destination schema read with a fixed snapshot; return the branches it is asked for."""
    branches: list[str] = []

    def accessor(_package: object, branch: str) -> dict[str, object]:
        branches.append(branch)
        return _BRANCH_SNAPSHOT

    table = dict(BUILTIN_ADAPTER_CAPABILITIES)
    table["infrahub"] = replace(
        table["infrahub"], destination_schema_accessor=accessor, destination_access_accessor=None
    )
    monkeypatch.setattr(schema_validation, "BUILTIN_ADAPTER_CAPABILITIES", table)
    return branches


def _package_with_missing_kind_attribute_and_relationship() -> dict[str, object]:
    """A package whose declared branch is `staging`, mapping one missing kind, attribute and relationship."""
    data = package_data()
    data["configuration"]["destination"]["settings"]["branch"] = "staging"
    data["configuration"]["schema_mapping"] = [
        {
            "name": "InfraDevice",
            "mapping": "dcim.devices",
            "identifiers": ["name"],
            "fields": [
                {"name": "name", "mapping": "name"},
                {"name": "serial", "mapping": "serial"},
                {"name": "rack", "mapping": "rack.name", "reference": "LocationSite"},
            ],
        },
        {"name": "LocationSite", "mapping": "dcim.sites", "fields": [{"name": "name", "mapping": "name"}]},
        {"name": "InfraCircuit", "mapping": "circuits.circuits", "fields": [{"name": "cid", "mapping": "cid"}]},
    ]
    return data


def test_validate_with_a_branch_checks_the_mapping_against_that_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-034: the named branch, not the declared one, is read; each missing kind and field is named."""
    branches = _spied_schema_reads(monkeypatch)
    client, _projection = _discovery_client(tmp_path)
    config_id = _register(client, _package_with_missing_kind_attribute_and_relationship(), "branch-validate")

    response = client.post(f"/configs/{config_id}/versions/1/validate", params={"branch": "review"})

    assert response.status_code == 200, response.text
    assert branches == ["review"]
    body = response.json()
    mismatches = {
        (finding["location"], finding["message"])
        for finding in body["findings"]
        if finding["code"] == "destination-schema-mismatch"
    }
    locations = {location for location, _message in mismatches}
    assert {
        "/configuration/schema_mapping/0/fields/1/name",
        "/configuration/schema_mapping/0/fields/2/name",
        "/configuration/schema_mapping/2/name",
    } <= locations
    named = " ".join(message for _location, message in mismatches)
    for name in ("serial", "rack", "InfraCircuit"):
        assert name in named
    assert body["destination_schema_fingerprint"] is None or isinstance(body["destination_schema_fingerprint"], str)


@pytest.mark.parametrize("authorization", [None, "Bearer not-a-token"])
def test_validate_with_a_branch_refuses_a_caller_without_a_valid_token_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorization: str | None
) -> None:
    """FR-034: a branch validation reads Infrahub with the registered credential, so it needs a valid token."""
    branches = _spied_schema_reads(monkeypatch)
    client, _projection = _discovery_client(tmp_path)
    config_id = _register(client, _package_with_missing_kind_attribute_and_relationship(), "branch-validate-auth")
    del client.headers["Authorization"]
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.post(f"/configs/{config_id}/versions/1/validate", params={"branch": "review"}, headers=headers)

    assert response.status_code == 401, response.text
    assert branches == []


def test_validate_without_a_branch_reads_no_schema_and_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-034: without a branch, validation judges declared content only, as before."""
    branches = _spied_schema_reads(monkeypatch)
    client, _projection = _discovery_client(tmp_path)
    data = _package_with_missing_kind_attribute_and_relationship()
    config_id = _register(client, data, "plain-validate")

    response = client.post(f"/configs/{config_id}/versions/1/validate")

    assert response.status_code == 200, response.text
    assert branches == []
    body = response.json()
    assert body["destination_schema_fingerprint"] is None
    assert body["findings"] == [finding.model_dump(mode="json") for finding in collect_findings(package(data))]


def _without_content(body: object) -> bool:
    """Whether no mapping anywhere in a JSON body has a `declared_content` key."""
    if isinstance(body, dict):
        return "declared_content" not in body and all(_without_content(value) for value in body.values())
    if isinstance(body, list):
        return all(_without_content(item) for item in body)
    return True


def test_the_register_response_shows_the_checksum_and_no_declared_content(tmp_path: Path) -> None:
    """FR-038 (SYNC-107): the response holds `package_checksum` and no redacted content beside it."""
    client, projection = _discovery_client(tmp_path)
    body = {"package": package_data(), "reason": "register"}
    headers = {"Idempotency-Key": "register-reference"}

    first = client.post("/configs", headers=headers, json=body)
    replay = client.post("/configs", headers=headers, json=body)

    assert first.status_code == replay.status_code == 201, first.text
    config_id = first.json()["configuration"]["config_id"]
    stored = projection.lookup_configuration_version(config_id, 1).value
    assert stored is not None
    assert first.json()["version"]["package_checksum"] == stored.package_checksum
    assert _without_content(first.json())
    assert replay.json() == first.json()
    RegisteredConfigurationResource.model_validate(first.json())


def test_the_new_version_response_shows_the_checksum_and_no_declared_content(tmp_path: Path) -> None:
    """FR-038: a created version (201), an identical version (200) and a replay hold no content."""
    client, projection = _discovery_client(tmp_path)
    config_id = _register(client, package_data(), "register-first")
    changed = _named_package("from-netbox", url="https://netbox.example.test")
    body = {"package": changed, "reason": "second version"}

    created = client.post(f"/configs/{config_id}/versions", headers={"Idempotency-Key": "v2"}, json=body)
    replay = client.post(f"/configs/{config_id}/versions", headers={"Idempotency-Key": "v2"}, json=body)
    same = client.post(f"/configs/{config_id}/versions", headers={"Idempotency-Key": "v2-again"}, json=body)

    assert (created.status_code, replay.status_code, same.status_code) == (201, 201, 200), created.text
    stored = projection.lookup_configuration_version(config_id, 2).value
    assert stored is not None
    for response in (created, replay, same):
        assert response.json()["version"]["package_checksum"] == stored.package_checksum
        assert _without_content(response.json())
        RegisteredVersionResource.model_validate(response.json())
    assert replay.json() == created.json()


def test_reading_a_version_still_returns_its_declared_content(tmp_path: Path) -> None:
    """FR-038 changes the registration responses only; `GET` of a version keeps the content."""
    client, _projection = _discovery_client(tmp_path)
    config_id = _register(client, package_data(), "register-read")

    response = client.get(f"/configs/{config_id}/versions/1")

    assert response.status_code == 200, response.text
    assert response.json()["declared_content"]["configuration"]["name"] == "from-netbox"


# Spec 025 User Story 1 (T006): `POST /configs/validate` checks a package without registering it.

_EXAMPLES = Path(__file__).parents[2] / "examples"


def _check(
    client: TestClient, body: Mapping[str, object], token: str = ADMIN_TOKEN, params: Mapping[str, str] | None = None
) -> httpx.Response:
    return client.post(
        "/configs/validate", json=body, params=params or {}, headers={"Authorization": f"Bearer {token}"}
    )


def test_an_administrator_checks_a_package_and_registers_nothing(tmp_path: Path) -> None:
    client, projection = _event_client(tmp_path)
    data = _package_with_missing_kind_attribute_and_relationship()

    response = _check(client, {"package": data})

    assert response.status_code == 200, response.text
    body = response.json()
    checked = package(data)
    assert body["package_checksum"] == checked.checksum()
    assert body["declared_content"] == checked.declared_content()
    assert body["name"] == "from-netbox"
    assert body["destination_read"] == "not_requested"
    assert body["destination_schema_fingerprint"] is None
    assert body["findings"] == [finding.model_dump(mode="json") for finding in collect_findings(checked)]
    assert body["offset"] == 0
    assert body["total_findings"] == len(body["findings"])
    assert body["next_offset"] is None
    assert projection.list_configuration_page().items == ()
    assert projection.audit_events() == ()


@pytest.mark.parametrize(
    "document",
    [
        yaml.safe_dump(_package_with_missing_kind_attribute_and_relationship()),
        json.dumps(_package_with_missing_kind_attribute_and_relationship()),
    ],
    ids=["yaml", "json"],
)
def test_an_administrator_checks_a_document_in_yaml_or_json(tmp_path: Path, document: str) -> None:
    client, _projection = _event_client(tmp_path)
    expected = _check(client, {"package": _package_with_missing_kind_attribute_and_relationship()}).json()

    response = _check(client, {"document": document})

    assert response.status_code == 200, response.text
    assert response.json() == expected


def test_the_registered_example_package_document_is_checked(tmp_path: Path) -> None:
    client, _projection = _event_client(tmp_path)
    document = (_EXAMPLES / "netbox_to_infrahub" / "package.yml").read_text(encoding="utf-8")

    response = _check(client, {"document": document})

    assert response.status_code == 200, response.text
    assert response.json()["package_checksum"] == package(yaml.safe_load(document)).checksum()


@pytest.mark.parametrize(
    "body",
    [{}, {"package": package_data(), "document": "format_version: 1\n"}, {"package": None, "document": None}],
    ids=["neither", "both", "both-null"],
)
def test_a_check_needs_exactly_one_of_package_and_document(tmp_path: Path, body: dict[str, object]) -> None:
    client, _projection = _event_client(tmp_path)

    response = _check(client, body)

    assert response.status_code == 400, response.text
    assert response.json()["error"]["family"] == "request"
    assert response.json()["error"].get("reason") is None


@pytest.mark.parametrize(
    "document",
    ["", "   \n", "# only a comment\n", "- a\n- b\n", "just text", "42", "{1: 2}", "{}", "key: [unclosed"],
    ids=["empty", "blank", "comment", "list", "scalar", "number", "integer-key", "empty-mapping", "invalid-yaml"],
)
def test_a_document_that_is_not_a_mapping_is_an_invalid_package(tmp_path: Path, document: str) -> None:
    client, _projection = _event_client(tmp_path)

    response = _check(client, {"document": document})

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert (error["family"], error["reason"]) == ("request", "invalid_package")


def _alias_expansion_document() -> str:
    """Return a document of under 1 KB whose nested aliases expand to 10**9 values."""
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    lines.extend(f"a{level}: &a{level} [{', '.join([f'*a{level - 1}'] * 10)}]" for level in range(1, 9))
    lines.append("configuration: *a8")
    return "\n".join(lines)


@pytest.mark.parametrize(
    "document",
    [_alias_expansion_document(), "format_version: 1\nconfiguration: &loop [*loop]\n"],
    ids=["alias-expansion", "self-reference"],
)
def test_a_document_whose_aliases_expand_past_the_limit_is_an_invalid_package(tmp_path: Path, document: str) -> None:
    client, _projection = _event_client(tmp_path)

    response = _check(client, {"document": document})

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert (error["family"], error["reason"]) == ("request", "invalid_package")


@pytest.mark.parametrize("field", ["document", "package"])
def test_a_v2_configuration_is_refused_with_the_conversion_command(tmp_path: Path, field: str) -> None:
    client, _projection = _event_client(tmp_path)
    document = (_EXAMPLES / "netbox_to_infrahub" / "config.yml").read_text(encoding="utf-8")
    body = {"document": document} if field == "document" else {"package": yaml.safe_load(document)}

    response = _check(client, body)

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert (error["family"], error["reason"]) == ("validation", "v2_configuration")
    assert "infrahub-sync configs convert" in error["message"]


@pytest.mark.parametrize(
    "body",
    [
        {"package": _package_with_missing_kind_attribute_and_relationship()},
        {"document": "not: [a package"},
        {"package": package_data(), "document": "both"},
        {},
        {"package": "not a mapping", "undeclared": True},
    ],
    ids=["valid-package", "invalid-document", "both-fields", "neither-field", "schema-invalid"],
)
def test_a_check_by_a_non_administrator_is_refused_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, object]
) -> None:
    """Critique E1: with a branch, the service sends its destination credential to the package's URL."""
    branches = _spied_schema_reads(monkeypatch)
    client, projection = _event_client(tmp_path)

    response = _check(client, body, token=OTHER_TOKEN, params={"branch": "main"})

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"
    assert branches == []
    refusals = [event for event in projection.audit_events() if event.outcome.startswith("refused-")]
    assert [(event.event_type, event.operation, event.outcome, event.config_id) for event in refusals] == [
        ("infrahub_sync.config.refused", "check-config-package", "refused-authorization", None),
    ]


def test_a_check_without_a_token_is_refused_before_any_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branches = _spied_schema_reads(monkeypatch)
    client, _projection = _event_client(tmp_path)

    response = client.post("/configs/validate", json={"package": package_data()}, params={"branch": "main"})

    assert response.status_code == 401, response.text
    assert branches == []


def test_a_check_with_a_branch_reads_that_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branches = _spied_schema_reads(monkeypatch)
    client, _projection = _event_client(tmp_path)

    response = _check(
        client, {"package": _package_with_missing_kind_attribute_and_relationship()}, params={"branch": "review"}
    )

    assert response.status_code == 200, response.text
    assert branches == ["review"]
    body = response.json()
    assert body["destination_read"] == "read"
    assert isinstance(body["destination_schema_fingerprint"], str)
    assert "destination-schema-mismatch" in [finding["code"] for finding in body["findings"]]


def test_a_check_finds_what_validating_the_registered_version_finds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-002, over HTTP: the check and the registered version's validation agree."""
    _spied_schema_reads(monkeypatch)
    client, _projection = _discovery_client(tmp_path)
    data = _package_with_missing_kind_attribute_and_relationship()

    checked = client.post("/configs/validate", json={"package": data}, params={"branch": "review"}).json()
    config_id = _register(client, data, "check-then-register")
    validated = client.post(f"/configs/{config_id}/versions/1/validate", params={"branch": "review"}).json()

    assert checked["findings"] == validated["findings"]
    assert checked["package_checksum"] == validated["package_checksum"]
    assert checked["destination_schema_fingerprint"] == validated["destination_schema_fingerprint"]


_READ_FAILURE_REASONS = (
    "timeout",
    "unreachable",
    "unauthorized",
    "forbidden",
    "nobranch",
    "rejected",
    "unconfigured",
    "credentials",
)


def _failing_schema_reads(monkeypatch: pytest.MonkeyPatch, reason: str, detail: str | None = None) -> None:
    def accessor(_package: object, _branch: str) -> NoReturn:
        msg = "the destination schema could not be read"
        raise DestinationSchemaReadError(msg, reason=reason, detail=detail)

    table = dict(BUILTIN_ADAPTER_CAPABILITIES)
    table["infrahub"] = replace(
        table["infrahub"], destination_schema_accessor=accessor, destination_access_accessor=None
    )
    monkeypatch.setattr(schema_validation, "BUILTIN_ADAPTER_CAPABILITIES", table)


@pytest.mark.parametrize("reason", _READ_FAILURE_REASONS)
def test_a_failed_destination_read_is_returned_by_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    _failing_schema_reads(monkeypatch, reason)
    client, _projection = _event_client(tmp_path)

    response = _check(client, {"package": package_data()}, params={"branch": "main"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["destination_read"] == reason
    assert body["destination_schema_fingerprint"] is None
    assert "destination-schema-read-failed" in [finding["code"] for finding in body["findings"]]


def test_a_check_pages_findings_like_the_validation_of_a_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spied_schema_reads(monkeypatch)
    client, _projection = _event_client(tmp_path)
    body = {"package": _package_with_missing_kind_attribute_and_relationship()}
    whole = _check(client, body, params={"branch": "review"}).json()
    assert whole["total_findings"] >= 2

    first = _check(client, body, params={"branch": "review", "limit": "1"}).json()
    second = _check(client, body, params={"branch": "review", "offset": "1", "limit": "1"}).json()

    assert first["findings"] == whole["findings"][:1]
    assert first["next_offset"] == 1
    assert second["findings"] == whole["findings"][1:2]
    for params in ({"limit": "0"}, {"limit": "257"}, {"offset": "-1"}, {"branch": ""}, {"limit": "1.0"}):
        assert _check(client, body, params=params).status_code == 422


def test_a_check_discloses_no_collected_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret the service holds is redacted from every finding, wherever the text came from.

    The store keeps account tokens as digests, so the secrets the service holds are the `admin`
    token of the environment and the values it collects at start, such as credentials.
    """
    credential = "credential-secret-canary-0005"
    _failing_schema_reads(monkeypatch, "unauthorized", detail=f"token {credential} was refused")
    projection = local_product_projection(tmp_path)
    resolver = seed_accounts(
        projection, {"admin": {"token": ADMIN_TOKEN, "administrator": True}}, environment_token=True
    ).resolver
    secrets = (*resolver.secret_values, credential)
    client = TestClient(
        create_app(
            RunService(projection, _Orchestration(), secrets=secrets),
            resolver,
            ConfigurationRoutes(product_projection=projection, secrets=secrets),
        )
    )

    response = _check(client, {"package": package_data()}, params={"branch": ADMIN_TOKEN})

    assert response.status_code == 200, response.text
    assert response.json()["destination_read"] == "unauthorized"
    assert ADMIN_TOKEN not in response.text
    assert credential not in response.text


def test_the_check_route_is_not_taken_for_a_configuration_id(tmp_path: Path) -> None:
    """`validate` is a valid configuration ID; the check route is registered before the ID routes."""
    client, _projection = _discovery_client(tmp_path)

    assert client.get("/configs/validate").status_code == 404
    assert client.post("/configs/validate", json={"package": package_data()}).status_code == 200


# The "registered without validation" mark (spec 025, User Story 4).


def _version_marks(client: TestClient, config_id: str) -> dict[str, dict[int, bool]]:
    """Return each version's mark from the version list and from each version's own resource."""
    listed = client.get(f"/configs/{config_id}/versions").json()["items"]
    single: dict[int, bool] = {}
    for item in listed:
        response = client.get(f"/configs/{config_id}/versions/{item['registry_version']}")
        assert response.status_code == 200, response.text
        single[item["registry_version"]] = response.json()["registered_without_validation"]
    return {"list": {item["registry_version"]: item["registered_without_validation"] for item in listed}, "get": single}


def test_registration_accepts_the_mark_and_every_version_resource_returns_it(tmp_path: Path) -> None:
    client, _projection = _discovery_client(tmp_path)

    registered = client.post(
        "/configs",
        headers={"Idempotency-Key": "register-unvalidated"},
        json={"package": package_data(), "registered_without_validation": True},
    )
    assert registered.status_code == 201, registered.text
    config_id = registered.json()["configuration"]["config_id"]
    added = client.post(
        f"/configs/{config_id}/versions",
        headers={"Idempotency-Key": "version-unvalidated"},
        json={"package": _named_package("second", "https://second.netbox.test"), "registered_without_validation": True},
    )

    assert added.status_code == 201, added.text
    assert registered.json()["version"]["registered_without_validation"] is True
    assert added.json()["version"]["registered_without_validation"] is True
    assert _version_marks(client, config_id) == {"list": {1: True, 2: True}, "get": {1: True, 2: True}}


def test_the_mark_defaults_to_false(tmp_path: Path) -> None:
    client, _projection = _discovery_client(tmp_path)

    config_id = _register(client, package_data(), "register-validated")
    added = client.post(
        f"/configs/{config_id}/versions",
        headers={"Idempotency-Key": "version-validated"},
        json={"package": _named_package("second", "https://second.netbox.test")},
    )

    assert added.status_code == 201, added.text
    assert added.json()["version"]["registered_without_validation"] is False
    assert _version_marks(client, config_id) == {"list": {1: False, 2: False}, "get": {1: False, 2: False}}


def test_an_identical_version_request_with_the_mark_returns_the_existing_version_unchanged(tmp_path: Path) -> None:
    client, _projection = _discovery_client(tmp_path)
    config_id = _register(client, package_data(), "register-validated")

    again = client.post(
        f"/configs/{config_id}/versions",
        headers={"Idempotency-Key": "identical-unvalidated"},
        json={"package": package_data(), "registered_without_validation": True},
    )

    assert again.status_code == 200, again.text
    assert again.json()["created"] is False
    assert again.json()["version"]["registered_without_validation"] is False
    assert _version_marks(client, config_id) == {"list": {1: False}, "get": {1: False}}


@pytest.mark.parametrize(
    ("first_mark", "retry_mark"),
    [
        pytest.param({"registered_without_validation": True}, {}, id="true-then-absent"),
        pytest.param({}, {"registered_without_validation": True}, id="absent-then-true"),
        pytest.param(
            {"registered_without_validation": False}, {"registered_without_validation": True}, id="false-then-true"
        ),
    ],
)
def test_a_key_reused_with_a_different_mark_is_refused(
    tmp_path: Path, first_mark: dict[str, object], retry_mark: dict[str, object]
) -> None:
    client, projection = _discovery_client(tmp_path)
    headers = {"Idempotency-Key": "register-mark-conflict"}

    first = client.post("/configs", headers=headers, json={"package": package_data(), **first_mark})
    retry = client.post("/configs", headers=headers, json={"package": package_data(), **retry_mark})

    assert first.status_code == 201, first.text
    assert retry.status_code == 409, retry.text
    assert retry.json()["error"]["code"] == "idempotency-conflict"
    assert len(projection.list_configurations()) == 1


@pytest.mark.parametrize(
    "body_mark",
    [pytest.param({}, id="absent"), pytest.param({"registered_without_validation": False}, id="explicit-false")],
)
def test_an_unmarked_request_keeps_the_fingerprint_of_receipts_stored_before_the_mark(
    tmp_path: Path, body_mark: dict[str, object]
) -> None:
    """A receipt stored by an earlier build fingerprinted five keys; an unmarked retry must still match it."""
    client, projection = _discovery_client(tmp_path)

    response = client.post(
        "/configs", headers={"Idempotency-Key": "pre-mark-receipt"}, json={"package": package_data(), **body_mark}
    )

    assert response.status_code == 201, response.text
    admin = projection.lookup_account_by_name("admin")
    assert admin is not None
    receipt = projection.lookup_mutation(admin.account_id, sha256(b"pre-mark-receipt").hexdigest()).value
    assert receipt is not None
    pre_mark_fingerprint = sha256(
        canonical_json_bytes(
            {
                "resource_kind": "configuration-registry",
                "resource_id": "configs",
                "operation": "register-config",
                "package": package_data(),
                "reason": None,
            }
        )
    ).hexdigest()
    assert receipt.request_fingerprint == pre_mark_fingerprint


# Registering from the text of a package file (spec 025, decision D-12).

# More than 2**53, so a JavaScript number cannot hold it exactly.
_LARGE_INTEGER = 2**60 + 1


def _document_with_numbers(name: str, timeout: float = 30.0) -> str:
    """Return a YAML package whose Prometheus source has the `timeout` setting ``timeout``.

    The package checks accept any number for that setting.
    """
    data = package_data()
    data["configuration"]["name"] = name
    data["configuration"]["source"]["name"] = "prometheus"
    data["configuration"]["source"]["settings"]["timeout"] = timeout
    return yaml.safe_dump(data)


def _register_document(client: TestClient, route: str, body: Mapping[str, object], key: str) -> httpx.Response:
    """Send ``body`` to the registration route: a new configuration, or a version of a registered one."""
    if route == "register":
        return client.post("/configs", headers={"Idempotency-Key": key}, json=body)
    config_id = _register(client, package_data(), f"{key}-first")
    return client.post(f"/configs/{config_id}/versions", headers={"Idempotency-Key": key}, json=body)


@pytest.mark.parametrize("route", ["register", "version"])
@pytest.mark.parametrize("timeout", [30.0, _LARGE_INTEGER], ids=["zero-fraction-float", "large-integer"])
def test_a_document_is_registered_with_the_numbers_and_checksum_the_check_gave(
    tmp_path: Path, route: str, timeout: float
) -> None:
    client, _projection = _discovery_client(tmp_path)
    document = _document_with_numbers("numbers", timeout)
    checked = _check(client, {"document": document})
    assert checked.status_code == 200, checked.text

    response = _register_document(client, route, {"document": document, "reason": "from the file"}, "document")

    assert response.status_code == 201, response.text
    version = response.json()["version"]
    assert version["package_checksum"] == checked.json()["package_checksum"]
    stored = client.get(f"/configs/{version['config_id']}/versions/{version['registry_version']}")
    stored_timeout = stored.json()["declared_content"]["configuration"]["source"]["settings"]["timeout"]
    # `30.0 == 30` in Python, so the type is compared too.
    assert (type(stored_timeout), stored_timeout) == (type(timeout), timeout)


@pytest.mark.parametrize("route", ["register", "version"])
@pytest.mark.parametrize(
    "body",
    [{}, {"package": package_data(), "document": yaml.safe_dump(package_data())}, {"package": None, "document": None}],
    ids=["neither", "both", "both-null"],
)
def test_a_registration_needs_exactly_one_of_package_and_document(
    tmp_path: Path, route: str, body: dict[str, object]
) -> None:
    client, projection = _discovery_client(tmp_path)

    response = _register_document(client, route, body, "exactly-one")

    assert response.status_code == 400, response.text
    assert response.json()["error"]["family"] == "request"
    assert response.json()["error"].get("reason") is None
    assert len(projection.list_configurations()) == (0 if route == "register" else 1)


@pytest.mark.parametrize("route", ["register", "version"])
@pytest.mark.parametrize(
    "document",
    ["", "- a\n- b\n", "{1: 2}", "key: [unclosed", _alias_expansion_document()],
    ids=["empty", "list", "integer-key", "invalid-yaml", "alias-expansion"],
)
def test_a_registered_document_that_is_not_a_package_is_an_invalid_package(
    tmp_path: Path, route: str, document: str
) -> None:
    client, projection = _discovery_client(tmp_path)

    response = _register_document(client, route, {"document": document}, "invalid-document")

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert (error["family"], error["reason"]) == ("request", "invalid_package")
    assert len(projection.list_configurations()) == (0 if route == "register" else 1)


@pytest.mark.parametrize("route", ["register", "version"])
def test_a_registered_v2_document_is_refused_with_the_conversion_command(tmp_path: Path, route: str) -> None:
    client, _projection = _discovery_client(tmp_path)
    document = (_EXAMPLES / "netbox_to_infrahub" / "config.yml").read_text(encoding="utf-8")

    response = _register_document(client, route, {"document": document}, "v2-document")

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert (error["family"], error["reason"]) == ("validation", "v2_configuration")
    assert "infrahub-sync configs convert" in error["message"]


def test_a_refused_document_retried_with_its_key_is_refused_again_not_held(tmp_path: Path) -> None:
    """A refused document does not hold its key, as for a refused package: the same text is refused again."""
    client, _projection = _discovery_client(tmp_path)
    headers = {"Idempotency-Key": "refused-document"}

    refused = client.post("/configs", headers=headers, json={"document": "key: [unclosed"})
    retried = client.post("/configs", headers=headers, json={"document": "key: [unclosed"})

    assert refused.status_code == 400, refused.text
    assert (retried.status_code, retried.json()) == (refused.status_code, refused.json())


@pytest.mark.parametrize("route", ["register", "version"])
def test_a_document_retried_with_its_key_replays_and_a_different_body_conflicts(tmp_path: Path, route: str) -> None:
    client, projection = _discovery_client(tmp_path)
    document = _document_with_numbers("retried")
    body = {"document": document, "reason": "retry"}

    first = _register_document(client, route, body, "document-retry")
    path = "/configs" if route == "register" else f"/configs/{first.json()['version']['config_id']}/versions"
    headers = {"Idempotency-Key": "document-retry"}
    replay = client.post(path, headers=headers, json=body)
    # The same package, sent as different text or as JSON, is a different request.
    reformatted = client.post(path, headers=headers, json={**body, "document": f"# comment\n{document}"})
    as_package = client.post(path, headers=headers, json={"package": yaml.safe_load(document), "reason": "retry"})

    assert first.status_code == 201, first.text
    assert (replay.status_code, replay.json()) == (first.status_code, first.json())
    for conflict in (reformatted, as_package):
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "idempotency-conflict"
    assert len(projection.list_configurations()) == 1


@pytest.mark.parametrize("route", ["check", "register", "version"])
def test_a_document_with_a_lone_surrogate_is_an_invalid_package_on_every_route(tmp_path: Path, route: str) -> None:
    """JSON can carry a lone surrogate; it is refused as text, not as a failure of the fingerprint."""
    client, _projection = _discovery_client(tmp_path)
    content = b'{"document": "format_version: 1\\nname: \\ud800\\n"}'
    if route == "check":
        response = client.post("/configs/validate", content=content, headers={"Content-Type": "application/json"})
    else:
        path = "/configs"
        if route == "version":
            path = f"/configs/{_register(client, package_data(), 'surrogate-first')}/versions"
        headers = {"Content-Type": "application/json", "Idempotency-Key": "surrogate"}
        response = client.post(path, content=content, headers=headers)

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert (error["family"], error["reason"]) == ("request", "invalid_package")


# --- A name is unique among configurations ------------------------------------------------------


def _name_in_use_body(config_id: str, name: str = "from-netbox") -> dict[str, object]:
    return {
        "code": "configuration-name-in-use",
        "message": (
            f"A configuration named '{name}' already exists ({config_id}). Did you mean to add a new version "
            "of it? Register the package as a new version of that configuration."
        ),
        "status": 409,
        "run_id": None,
        "mutation_id": None,
        "details": {"config_id": config_id, "name": name},
    }


def test_registering_a_name_another_configuration_has_is_refused_with_that_configuration(tmp_path: Path) -> None:
    client, projection = _discovery_client(tmp_path)
    existing = _register(client, package_data(), "register-first")
    body = {"package": _named_package("from-netbox", "https://second.netbox.test"), "reason": "second"}

    refused = client.post("/configs", headers={"Idempotency-Key": "register-second"}, json=body)
    # The refusal stores nothing, so the same request with the same key is decided again.
    again = client.post("/configs", headers={"Idempotency-Key": "register-second"}, json=body)

    for response in (refused, again):
        assert response.status_code == 409, response.text
        assert response.json()["error"] == _name_in_use_body(existing)
    assert [summary.config_id for summary in projection.list_configurations()] == [existing]
    refusals = [event for event in projection.audit_events() if event.outcome.startswith("refused-")]
    assert [(event.event_type, event.outcome) for event in refusals] == [
        ("infrahub_sync.config.refused", "refused-configuration-name-in-use")
    ] * 2


def test_a_new_version_with_the_name_of_another_configuration_is_refused(tmp_path: Path) -> None:
    client, projection = _discovery_client(tmp_path)
    existing = _register(client, package_data(), "register-first")
    other = _register(client, _named_package("other-inventory"), "register-other")

    response = client.post(
        f"/configs/{other}/versions",
        headers={"Idempotency-Key": "version-taking-the-name"},
        json={"package": _named_package("from-netbox", "https://second.netbox.test"), "reason": "rename"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"] == _name_in_use_body(existing)
    assert [version.registry_version for version in projection.list_configuration_versions(other)] == [1]


def test_a_new_version_may_keep_its_name_or_take_a_free_one(tmp_path: Path) -> None:
    client, _projection = _discovery_client(tmp_path)
    config_id = _register(client, package_data(), "register-first")

    _add_version(client, config_id, _named_package("from-netbox", "https://second.netbox.test"), "same-name")
    _add_version(client, config_id, _named_package("renamed-inventory", "https://second.netbox.test"), "rename")
    # The name it gave up is free for another configuration.
    other = _register(client, package_data(), "register-the-free-name")

    assert client.get(f"/configs/{config_id}").json()["name"] == "renamed-inventory"
    assert client.get(f"/configs/{other}").json()["name"] == "from-netbox"


def test_configurations_without_a_name_are_not_refused(tmp_path: Path) -> None:
    client, _projection = _discovery_client(tmp_path)

    first = _register(client, _named_package(""), "register-unnamed-1")
    second = _register(client, _named_package("", "https://second.netbox.test"), "register-unnamed-2")

    assert first != second


def test_a_replayed_registration_keeps_its_stored_answer_after_the_name_is_taken(tmp_path: Path) -> None:
    """The stored answer of an accepted request is replayed; the name check does not run again."""
    client, _projection = _discovery_client(tmp_path)
    body = {"package": package_data(), "reason": "register"}

    first = client.post("/configs", headers={"Idempotency-Key": "register-once"}, json=body)
    replay = client.post("/configs", headers={"Idempotency-Key": "register-once"}, json=body)

    assert (first.status_code, replay.status_code) == (201, 201), replay.text
    assert replay.json() == first.json()


def test_the_registration_routes_declare_the_conflict_refusal(tmp_path: Path) -> None:
    client, _projection = _discovery_client(tmp_path)
    paths = client.get("/openapi.json").json()["paths"]

    for path in ("/configs", "/configs/{config_id}/versions"):
        responses = paths[path]["post"]["responses"]
        assert {"401", "403", "409", "422"} <= set(responses), path
        assert responses["409"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorEnvelope"
        }


def test_the_registration_routes_declare_both_bodies_of_a_422(tmp_path: Path) -> None:
    """A schema refusal is an `ErrorEnvelope`; a package refused by validation is a `ConfigErrorEnvelope`."""
    client, _projection = _discovery_client(tmp_path)
    paths = client.get("/openapi.json").json()["paths"]

    for path in ("/configs", "/configs/{config_id}/versions"):
        schema = paths[path]["post"]["responses"]["422"]["content"]["application/json"]["schema"]
        assert schema["anyOf"] == [
            {"$ref": "#/components/schemas/ErrorEnvelope"},
            {"$ref": "#/components/schemas/ConfigErrorEnvelope"},
        ], path


def test_the_registration_routes_declare_the_configuration_error_body_of_a_400(tmp_path: Path) -> None:
    """A request refused before validation, such as a `document` that is not a mapping, is a `ConfigErrorEnvelope`."""
    client, _projection = _discovery_client(tmp_path)
    paths = client.get("/openapi.json").json()["paths"]

    for path in ("/configs", "/configs/{config_id}/versions"):
        schema = paths[path]["post"]["responses"]["400"]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ConfigErrorEnvelope"}, path
