"""Fixtures for the preview smoke suite.

These tests run against the environment `invoke preview.up` starts and confirm
the simple things on every preview surface (Python API, Sync HTTP API,
Prefect) so a tester never starts from a broken environment. They are not a
replacement for human testing, and they skip — never fail — when the preview
environment is not running.

They share one Infrahub branch and one Prefect deployment, and the collection
hook below orders them against each other, so the suite runs single-process:
under `pytest-xdist` the hook would order one worker's share of the modules and
the shared branch would take concurrent writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tasks.preview import REPO_ROOT, PreviewError, load_preview_env, preview_urls

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
def preview_settings() -> dict[str, Any]:
    """Shipped-plus-local preview settings, with derived URLs and tokens.

    A missing or trimmed settings file skips like every other
    unavailable-environment condition; these tests never fail for setup reasons.
    """
    try:
        values = load_preview_env()
    except PreviewError as exc:
        pytest.skip(f"preview settings unavailable ({exc}); start with `invoke preview.up`")
    urls = preview_urls(values)
    principals = json.loads(values["PREVIEW_BEARER_TOKENS"])
    first_actor = min(principals)
    return {
        "values": values,
        "urls": urls,
        "infrahub_token": values["INFRAHUB_INITIAL_ADMIN_TOKEN"],
        "bearer_token": principals[first_actor]["token"],
        "actor": first_actor,
        "examples_dir": str(REPO_ROOT / "examples"),
    }


@pytest.fixture(scope="session")
def preview_env(preview_settings: dict[str, Any]) -> dict[str, Any]:
    """Skip the suite when any preview endpoint is unreachable."""
    probes = {
        "Infrahub": f"{preview_settings['urls']['infrahub']}/api/config",
        "Prefect": f"{preview_settings['urls']['prefect']}/api/health",
        "Sync API": f"{preview_settings['urls']['sync_api']}/openapi.json",
    }
    for description, url in probes.items():
        try:
            response = httpx.get(url, timeout=5)
        except httpx.HTTPError as exc:
            pytest.skip(f"preview environment not running ({description}: {exc}); start it with `invoke preview.up`")
        if response.status_code >= 500:
            pytest.skip(f"preview {description} unhealthy (HTTP {response.status_code})")
    return preview_settings
