"""Focused transport checks for configuration HTTP resources."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from fastapi.testclient import TestClient

from infrahub_sync.managed.app import create_app
from infrahub_sync.managed.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.managed.config_routes import ConfigurationAPIError, ConfigurationRoutes
from infrahub_sync.managed.orchestration import Observation, Submission
from infrahub_sync.managed.serve import build_app
from infrahub_sync.managed.service import ManagedRunService
from infrahub_sync.product_store import configs as configs_service
from infrahub_sync.product_store import local_product_projection
from tests.configuration.validation_packages import package_data

if TYPE_CHECKING:
    from infrahub_sync.managed.auth import PrincipalResolver


class _Orchestration:  # pylint: disable=too-few-public-methods
    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: ARG002, PLR6301
        return Submission(flow_run_id="test-flow", state="pending")

    async def observe(self, flow_run_id: str) -> Observation:  # noqa: ARG002, PLR6301
        return Observation(available=True, state="running")

    async def cancel(self, flow_run_id: str) -> Observation:  # noqa: ARG002, PLR6301
        return Observation(available=True, state="cancelled")


def test_configuration_routes_register_then_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A configuration resource delegates registration and its scoped reads."""
    monkeypatch.setenv(
        PRINCIPALS_ENV, json.dumps({"admin": {"token": "admin-token-canary-0003", "administrator": True}})
    )
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path)
    runs = ManagedRunService(projection, _Orchestration(), secrets=resolver.secret_values)
    routes = ConfigurationRoutes(tmp_path, secrets=resolver.secret_values)
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
            ManagedRunService(projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(tmp_path, secrets=resolver.secret_values),
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
    receipt = projection.lookup_mutation("admin", __import__("hashlib").sha256(b"register-once").hexdigest())
    assert receipt.value is not None and receipt.value.state == "accepted"


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
            ManagedRunService(projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(tmp_path, secrets=resolver.secret_values),
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
        (AssertionError("hostile"), 503, "internal", None),
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
    runs = ManagedRunService(local_product_projection(tmp_path), _Orchestration(), secrets=resolver.secret_values)
    routes = ConfigurationRoutes(tmp_path)

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
    source = Path(__file__).parents[2] / "infrahub_sync" / "managed" / "config_routes.py"
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
        ConfigurationRoutes(tmp_path, service=Service()).list_configs()
    assert raised.value.family == "configs"


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
            cast("ManagedRunService", runs),
            cast("PrincipalResolver", Resolver()),
            ConfigurationRoutes(tmp_path, service=config_service),
        )
    )
    headers = {"Authorization": "Bearer accepted"}
    assert client.get("/runs/run-a/artifacts/one", headers=headers).status_code == 200
    assert client.get("/configs", headers=headers).status_code == 200
    assert runs.calls == ["run-a"]
    assert config_service.calls == ["list_configs"]


def test_build_app_binds_one_cache_location_and_passes_configuration_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime construction supplies the one durable cache location to both families."""
    from infrahub_sync.managed._settings import PRODUCT_CACHE_ENV  # noqa: PLC2701

    monkeypatch.setenv(PRODUCT_CACHE_ENV, str(tmp_path))
    received: list[object] = []

    def projection(location: Path) -> object:
        received.append(location)
        return object()

    class Resolver:
        secret_values: tuple[str, ...] = ()

    def application(run: object, resolver: object, routes: object) -> object:
        received.extend((run, resolver, routes))
        return object()

    assert (
        build_app(
            projection_factory=projection,
            resolver_factory=Resolver,
            run_service_factory=lambda *_args, **_kwargs: object(),
            app_factory=application,
        )
        is not None
    )
    assert received[0] == tmp_path
