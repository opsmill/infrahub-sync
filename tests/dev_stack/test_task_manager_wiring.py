"""The development stack runs on its own Infrahub task manager.

One task manager holds one Sync deployment, because the service's Prefect
deployment name is fixed; the dev stack therefore never shares the preview's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from invoke import Context

from tasks import dev

DEV_COMPOSE = Path(__file__).resolve().parents[2] / "development" / "docker-compose.dev.yml"
INFRAHUB_COMPOSE = DEV_COMPOSE.parent / "docker-compose.infrahub.yml"
SYNC_SERVICES = ("sync-bootstrap", "sync-api", "sync-worker")


def _model() -> dict[str, Any]:
    return yaml.safe_load(DEV_COMPOSE.read_text(encoding="utf-8"))


def test_the_dev_stack_runs_its_own_task_manager_from_the_pinned_infrahub_image() -> None:
    services = _model()["services"]
    pinned = INFRAHUB_COMPOSE.read_text(encoding="utf-8")

    image = services["task-manager"]["image"]
    assert image.startswith("registry.opsmill.io/opsmill/infrahub:")
    version, digest = image.removeprefix("registry.opsmill.io/opsmill/infrahub:").split("@")
    assert version in pinned, "the dev task manager is not the Infrahub release the preview pins"
    assert digest in pinned, "the dev task manager is not the image digest the preview pins"
    assert "infrahub.prefect_server.app:create_infrahub_prefect" in " ".join(services["task-manager"]["command"])
    assert "networks" not in _model(), "the dev stack must not join another stack's network"


def test_the_sync_services_run_on_the_stacks_own_task_manager_and_database() -> None:
    services = _model()["services"]
    for name in SYNC_SERVICES:
        environment = services[name].get("environment") or {}
        assert environment.get("PREFECT_API_URL") == "http://task-manager:4200/api", name
        assert str(environment.get("INFRAHUB_SYNC_DATABASE_URL")).endswith("@task-manager-db:5432/infrahub_sync")
    assert services["sync-bootstrap"]["depends_on"]["task-manager"]["condition"] == "service_healthy"
    assert "postgres" not in services
    assert "prefect-server" not in services


def test_destroy_removes_the_stack_and_its_volumes_only(monkeypatch: Any) -> None:  # noqa: ANN401
    commands: list[str] = []
    monkeypatch.setattr(Context, "run", lambda _self, command, **_kwargs: commands.append(command))

    dev.destroy.body(Context())

    assert commands == [f"{dev.DEV_COMPOSE} down --volumes --remove-orphans"]


def test_the_dev_prefect_address_is_the_stacks_own_task_manager() -> None:
    assert dev.PREFECT_URL == "http://127.0.0.1:4230"
