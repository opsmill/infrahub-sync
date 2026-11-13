"""Fixtures for the development stack's smoke suite.

These tests run against the development stack that `invoke dev.start` (or `dev.deps` with
`dev.api` and `dev.worker`) starts, and confirm the simple things on every surface (Python
API, Sync HTTP API, Prefect) so a tester never starts from a broken stack. They are not a
replacement for human testing, and they skip — never fail — when the stack is not running.
`invoke dev.smoke` seeds Infrahub and runs them.

They share one Infrahub branch and one Prefect deployment, and the collection
hook below orders them against each other, so the suite runs single-process:
under `pytest-xdist` the hook would order one worker's share of the modules and
the shared branch would take concurrent writes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tasks import dev
from tasks.utils import REPO_BASE as REPO_ROOT

if TYPE_CHECKING:
    from _pytest.nodes import Item

_HERE = Path(__file__).parent
# The Prefect surface smoke asserts on the flow runs every client smoke creates, so it
# has to run after all of them. Filename collation is not a dependency this suite may
# rest on: renaming any module silently reorders them and the surface smoke then polls a
# deployment that nothing has submitted to yet.
_RUN_CREATORS = (
    _HERE / "test_service_api.py",
    _HERE / "test_cli_client.py",
    _HERE / "test_python_client.py",
)
_RUN_OBSERVER = _HERE / "test_prefect_surface.py"


def pytest_collection_modifyitems(items: list[Item]) -> None:
    """Run every run-creating smoke before the Prefect surface smoke that observes them."""
    observers = [item for item in items if item.path == _RUN_OBSERVER]
    creators = [item for item in items if item.path in _RUN_CREATORS]
    if not observers or not creators or items.index(creators[-1]) < items.index(observers[0]):
        return
    for observer in observers:
        items.remove(observer)
    resume_at = items.index(creators[-1]) + 1
    items[resume_at:resume_at] = observers


@pytest.fixture(scope="session")
def evidence_dir() -> Path:
    """Where the qualification rows write their captured HTTP transcripts.

    Under the development stack's gitignored runtime state, so one directory holds everything
    a run of the matrix leaves behind for a reader.
    """
    path = REPO_ROOT / ".dev" / "evidence"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session")
def smoke_settings() -> dict[str, Any]:
    """The development stack's settings, with derived URLs and tokens.

    The worker runs in a container, so a package names Infrahub by its service name
    (`urls["infrahub"]`) while the suite's own Infrahub client uses the host publication
    (`urls["infrahub_host"]`). In dependencies mode (`dev.worker` on the host, after
    `dev.deps`, which `dev.smoke` reports through `INFRAHUB_SYNC_SMOKE_STACK`), the package
    names the host publication too. Unreadable settings skip like every other
    unavailable-stack condition; these tests never fail for setup reasons.
    """
    host_worker = os.environ.get(dev.SMOKE_STACK_VARIABLE) == dev.DEPS_SMOKE_STACK
    try:
        settings = dev.dev_settings()
    except dev.DevStackError as exc:
        pytest.skip(f"development stack settings unavailable ({exc}); start with `invoke dev.start`")
    principals = json.loads(settings.bearer_tokens)
    first_actor = min(principals)
    return {
        "work_pool": settings.work_pool,
        "urls": {
            "infrahub": settings.infrahub_url if host_worker else settings.worker_infrahub_url,
            "infrahub_host": settings.infrahub_url,
            "prefect": settings.prefect_url,
            "sync_api": settings.sync_api_url,
        },
        "infrahub_token": settings.infrahub_admin_token,
        "bearer_token": principals[first_actor]["token"],
        "actor": first_actor,
        "examples_dir": str(REPO_ROOT / "examples"),
    }


def infrahub_host_url(settings: dict[str, Any]) -> str:
    """The Infrahub URL the suite itself reaches, which differs from a package's on the dev stack."""
    return settings["urls"].get("infrahub_host", settings["urls"]["infrahub"])


@pytest.fixture(scope="session")
def smoke_env(smoke_settings: dict[str, Any]) -> dict[str, Any]:
    """Skip the suite when any endpoint of the stack is unreachable."""
    probes = {
        "Infrahub": f"{infrahub_host_url(smoke_settings)}/api/config",
        "Prefect": f"{smoke_settings['urls']['prefect']}/api/health",
        "Sync API": f"{smoke_settings['urls']['sync_api']}/openapi.json",
    }
    for description, url in probes.items():
        try:
            response = httpx.get(url, timeout=5)
        except httpx.HTTPError as exc:
            pytest.skip(f"development stack not running ({description}: {exc}); start it with `invoke dev.start`")
        if response.status_code >= 500:
            pytest.skip(f"development stack {description} unhealthy (HTTP {response.status_code})")
    return smoke_settings
