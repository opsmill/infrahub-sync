"""Prefect registration identity for the service worker and its one deployment."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

pytest.importorskip("prefect")

from prefect.workers.process import ProcessWorker

from infrahub_sync.service.deploy import CATALOGUE
from infrahub_sync.service.orchestration import (
    SERVICE_DEFINITION,
    SERVICE_DEPLOYMENT_NAME,
    SERVICE_FLOW_NAME,
)
from infrahub_sync.service.worker import ServiceProcessWorker, service_worker_name

_SERVICE_PACKAGE = Path(__file__).resolve().parents[2] / "infrahub_sync" / "service"
_LEGACY_PREFECT_IDENTITY = "infrahub-sync-managed"


def test_the_deployment_registers_the_service_flow_and_deployment_names() -> None:
    assert SERVICE_FLOW_NAME == "infrahub-sync-service"
    assert SERVICE_DEPLOYMENT_NAME == "run"
    assert SERVICE_DEFINITION.key == "infrahub-sync-service/run"


def test_the_deployment_carries_the_service_tag_set() -> None:
    assert SERVICE_DEFINITION.tags == ("infrahub-sync", "service")


def test_the_deployment_entrypoint_names_the_service_flow_function() -> None:
    assert SERVICE_DEFINITION.module == "infrahub_sync.service.flow"
    assert SERVICE_DEFINITION.function == "service_sync_run"
    assert SERVICE_DEFINITION.entrypoint is not None
    path_part, _, function_part = SERVICE_DEFINITION.entrypoint.rpartition(":")
    assert function_part == "service_sync_run"
    assert Path(path_part).parent.name == "service"


def test_exactly_one_deployment_is_registered() -> None:
    assert CATALOGUE.keys() == (SERVICE_DEFINITION.key,)


def test_the_registered_worker_name_carries_the_service_prefix() -> None:
    name = service_worker_name()

    prefix = "infrahub-sync-service-"
    assert name.startswith(prefix)
    suffix = name.removeprefix(prefix)
    assert str(UUID(suffix)) == suffix


def test_the_worker_dispatch_key_is_service_named_and_distinct_from_prefect() -> None:
    assert ServiceProcessWorker.__dispatch_key__() == "infrahub-sync-service-process"
    assert ServiceProcessWorker.__dispatch_key__() != ProcessWorker.__dispatch_key__()


def test_no_legacy_prefect_identity_string_survives_beside_the_service_one() -> None:
    """The identity is renamed, not duplicated: nothing live still says the old name."""
    offenders = [
        f"{path.name}:{number}"
        for path in sorted(_SERVICE_PACKAGE.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if _LEGACY_PREFECT_IDENTITY in line
    ]

    assert offenders == []
