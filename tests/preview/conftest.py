"""Fixtures for the preview smoke suite.

These tests run against the environment `invoke preview.up` starts and confirm
the simple things on every preview surface (Python API, Sync HTTP API,
Prefect) so a tester never starts from a broken environment. They are not a
replacement for human testing, and they skip — never fail — when the preview
environment is not running.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tasks.preview import REPO_ROOT, PreviewError, load_preview_env, preview_urls


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
