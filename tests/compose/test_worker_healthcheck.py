"""Both Compose files give `sync-worker` the alive-file healthcheck (spec 029, US2).

Reads the files only; no container is started.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from infrahub_sync.service.worker_alive import WORKER_HEALTHCHECK_COMMAND
from tests.compose.conftest import REPO_ROOT

COMPOSE_FILES = [REPO_ROOT / "docker-compose.yml", REPO_ROOT / "development" / "docker-compose.yml"]


def _services(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return document["services"]


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda path: str(path.relative_to(REPO_ROOT)))
def test_the_worker_healthcheck_runs_the_alive_file_command(path: Path) -> None:
    healthcheck = _services(path)["sync-worker"]["healthcheck"]

    assert healthcheck["test"] == ["CMD", "python", "-I", "-c", WORKER_HEALTHCHECK_COMMAND]
    assert healthcheck["interval"] == "10s"
    assert healthcheck["timeout"] == "5s"
    assert healthcheck["retries"] == 3
    assert healthcheck["start_period"] == "60s"


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda path: str(path.relative_to(REPO_ROOT)))
def test_the_api_healthcheck_stays_on_the_version_route(path: Path) -> None:
    test = _services(path)["sync-api"]["healthcheck"]["test"]

    assert any("/api/version" in part for part in test)
