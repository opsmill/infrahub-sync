"""The Sync API's and worker's startup checks against Infrahub."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any, cast

import pytest
from infrahub_sdk.exceptions import AuthenticationError, ServerNotReachableError

from infrahub_sync.platform.client import ServiceAccountRefusedError
from infrahub_sync.platform.schema_check import SyncSchemaMissingError
from infrahub_sync.service import infrahub_records, serve, worker
from infrahub_sync.service.auth import InfrahubPrincipalResolver

ENVIRONMENT = {
    "INFRAHUB_SYNC_INFRAHUB_ADDRESS": "http://infrahub-server:8000",
    "INFRAHUB_SYNC_INFRAHUB_TOKEN": "service-token-0123456789",
}


async def _no_prefect_check() -> None:  # noqa: RUF029 - async check seam
    return None


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(serve, "api_startup_check", _no_prefect_check)


async def test_the_schema_is_checked_on_the_servers_default_branch(
    environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del environment
    checked: list[str] = []

    async def branch(_client: Any) -> str:  # noqa: ANN401, RUF029 - async seam
        return "trunk"

    async def require(_client: Any, name: str) -> None:  # noqa: ANN401, RUF029 - async seam
        checked.append(name)

    monkeypatch.setattr(serve, "default_branch", branch)
    monkeypatch.setattr(serve, "require_sync_schema", require)

    await serve.service_startup_check()

    assert checked == ["trunk"]


async def test_a_missing_schema_stops_the_api(environment: None, monkeypatch: pytest.MonkeyPatch) -> None:
    del environment

    async def branch(_client: Any) -> str:  # noqa: ANN401, RUF029 - async seam
        return "main"

    async def missing(_client: Any, _name: str) -> None:  # noqa: ANN401, RUF029 - async seam
        raise SyncSchemaMissingError(["SyncRun"])

    monkeypatch.setattr(serve, "default_branch", branch)
    monkeypatch.setattr(serve, "require_sync_schema", missing)

    with pytest.raises(SyncSchemaMissingError):
        await serve.service_startup_check()


async def test_an_unreachable_infrahub_is_tolerated_at_startup(
    environment: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    del environment
    caplog.set_level(logging.WARNING, logger=serve.__name__)

    async def unreachable(_client: Any) -> str:  # noqa: ANN401, RUF029 - async seam
        address = "http://infrahub-server:8000"
        raise ServerNotReachableError(address)

    monkeypatch.setattr(serve, "default_branch", unreachable)
    monkeypatch.setattr(serve, "require_sync_schema", lambda *_args: pytest.fail("checked an unreachable server"))

    await serve.service_startup_check()

    assert "could not be reached at startup" in caplog.text
    assert "service-token" not in caplog.text


def test_the_default_resolver_identifies_callers_through_infrahub(environment: None) -> None:
    del environment
    resolver = serve.infrahub_principal_resolver()

    assert isinstance(resolver, InfrahubPrincipalResolver)
    assert resolver.secret_values == ("service-token-0123456789",)


def test_the_default_resolver_needs_the_infrahub_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="INFRAHUB_SYNC_INFRAHUB_ADDRESS"):
        serve.infrahub_principal_resolver()


def test_a_missing_schema_stops_the_worker(environment: None, monkeypatch: pytest.MonkeyPatch) -> None:
    del environment

    def missing(_client: Any, _name: str) -> None:  # noqa: ANN401
        raise SyncSchemaMissingError(["SyncRun"])

    monkeypatch.setattr(worker, "default_branch_sync", lambda _client: "main")
    monkeypatch.setattr(worker, "require_sync_schema_sync", missing)

    with pytest.raises(SystemExit, match="SyncRun"):
        worker.refuse_start_without_sync_schema()


def test_an_unreachable_infrahub_is_tolerated_by_the_worker(
    environment: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    del environment
    caplog.set_level(logging.WARNING, logger=worker.__name__)

    def unreachable(_client: Any) -> str:  # noqa: ANN401
        address = "http://infrahub-server:8000"
        raise ServerNotReachableError(address)

    monkeypatch.setattr(worker, "default_branch_sync", unreachable)

    worker.refuse_start_without_sync_schema()

    assert "the Sync schema was not checked" in caplog.text
    assert "service-token" not in caplog.text


def test_a_worker_without_infrahub_skips_the_schema_check(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(worker, "service_client_sync", lambda _settings: pytest.fail("contacted Infrahub"))

    worker.refuse_start_without_sync_schema()


async def test_a_refused_service_token_stops_the_api(environment: None, monkeypatch: pytest.MonkeyPatch) -> None:
    del environment

    async def refused(_client: Any) -> str:  # noqa: ANN401, RUF029 - async seam
        message = "401"
        raise AuthenticationError(message)

    monkeypatch.setattr(serve, "default_branch", refused)

    with pytest.raises(ServiceAccountRefusedError, match="INFRAHUB_SYNC_INFRAHUB_TOKEN"):
        await serve.service_startup_check()


def test_a_refused_service_token_stops_the_worker(environment: None, monkeypatch: pytest.MonkeyPatch) -> None:
    del environment

    def refused(_client: Any) -> str:  # noqa: ANN401
        message = "401"
        raise AuthenticationError(message)

    monkeypatch.setattr(worker, "default_branch_sync", refused)

    with pytest.raises(SystemExit, match="INFRAHUB_SYNC_INFRAHUB_TOKEN"):
        worker.refuse_start_without_sync_schema()


def test_a_finished_run_is_mirrored_when_the_worker_has_infrahub(
    environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del environment
    mirrored: list[tuple[str, Any]] = []

    class _Mirror:
        @staticmethod
        def mirror(run: Any, extra: Any = None, **_kwargs: Any) -> tuple[str, ...]:  # noqa: ANN401
            mirrored.append((run.run_id, extra))
            return ()

    plan = json.dumps({"checksum": "c" * 64})

    class _Projection:
        @staticmethod
        def lookup_run(run_id: str) -> Any:  # noqa: ANN401
            return SimpleNamespace(value=SimpleNamespace(run_id=run_id))

        @staticmethod
        def lookup_artifact(_run_id: str, _artifact_id: str) -> Any:  # noqa: ANN401
            return SimpleNamespace(value=plan.encode())

    monkeypatch.setattr(
        infrahub_records.EmittedPlanResource,
        "model_validate_json",
        staticmethod(lambda data: SimpleNamespace(checksum=json.loads(data)["checksum"])),
    )

    monkeypatch.setattr(infrahub_records, "optional_infrahub_records", lambda: (None, _Mirror()))

    infrahub_records.mirror_finished(cast("Any", _Projection()), "run-0001")

    assert mirrored == [("run-0001", {"plan_checksum": "c" * 64})], "the worker's mirror lost the plan checksum"


def test_a_worker_without_infrahub_mirrors_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)

    infrahub_records.mirror_finished(cast("Any", None), "run-0001")
