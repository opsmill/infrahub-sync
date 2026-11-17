"""Shared builders for the tests of the site: the Sync API under `/api` and the web interface."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, AnonymousAccess, EnvironmentPrincipalResolver
from infrahub_sync.service.orchestration import CancellationResult, Observation, PoolStatus, RunLogRead, Submission
from infrahub_sync.service.service import RunService

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from pathlib import Path

    import pytest
    from fastapi import FastAPI

    from infrahub_sync.product_store import ProductProjection
    from infrahub_sync.service.liveness import RunLivenessReconciler

WEB_UI_TOKEN = "web-ui-token-canary-0001"  # noqa: S105 - deliberate non-secret boundary canary.
OPERATOR_TOKEN = "operator-token-canary-0002"  # noqa: S105 - deliberate non-secret boundary canary.
ADMIN_TOKEN = "admin-token-canary-0003"  # noqa: S105 - deliberate non-secret boundary canary.
TOKENS = (WEB_UI_TOKEN, OPERATOR_TOKEN, ADMIN_TOKEN)
DEFAULT_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
INLINE_SCRIPT = 'document.documentElement.dataset.theme="dark"'
INDEX_HTML = f"""<!doctype html>
<html><head><title>Infrahub Sync</title><script>{INLINE_SCRIPT}</script>
<script type="module" src="/assets/app-abc123.js"></script></head><body><div id="app"></div></body></html>
"""


class IdleOrchestration:
    """An orchestration that accepts nothing; the site tests never start an execution."""

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: PLR6301
        del parameters, idempotency_key
        msg = "the site tests do not submit executions"
        raise AssertionError(msg)

    async def observe(self, flow_run_id: str) -> Observation:  # noqa: PLR6301
        del flow_run_id
        return Observation(available=False, state=None, reason="prefect-execution-unavailable")

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:  # noqa: PLR6301
        del work_pool_name, now
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:  # noqa: PLR6301
        del flow_run_id
        return CancellationResult(acknowledged=False, reason="prefect-cancellation-unavailable")

    async def read_logs(self, flow_run_ids: Sequence[str]) -> RunLogRead:  # noqa: PLR6301
        del flow_run_ids
        return RunLogRead(expired=False)


class RecordingReconciler:
    """A liveness reconciler that records each iteration and lets a test wait for the first one."""

    cadence_seconds = 3600

    def __init__(self) -> None:
        self.iterations = 0
        self.started = asyncio.Event()

    async def reconcile_once(self) -> None:
        """Record one liveness iteration."""
        self.iterations += 1
        self.started.set()


def set_principals(monkeypatch: pytest.MonkeyPatch) -> EnvironmentPrincipalResolver:
    """Configure the `web-ui`, `operator` and administrator `admin` principals and return their resolver."""
    monkeypatch.setenv(
        PRINCIPALS_ENV,
        json.dumps(
            {
                "web-ui": {"token": WEB_UI_TOKEN},
                "operator": {"token": OPERATOR_TOKEN},
                "admin": {"token": ADMIN_TOKEN, "administrator": True},
            }
        ),
    )
    return EnvironmentPrincipalResolver.from_environment()


def build_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    anonymous_actor: str | None = None,
    allowed_hosts: frozenset[str] = DEFAULT_HOSTS,
    reconciler: RecordingReconciler | None = None,
) -> tuple[FastAPI, ProductProjection]:
    """Return the Sync API application, with anonymous access when an actor is named, and its projection."""
    resolver = set_principals(monkeypatch)
    projection = local_product_projection(tmp_path.resolve())
    service = RunService(projection, IdleOrchestration(), secrets=resolver.secret_values)
    anonymous = None
    if anonymous_actor is not None:
        principal = resolver.principal_for_actor(anonymous_actor)
        assert principal is not None
        anonymous = AnonymousAccess(principal=principal, allowed_hosts=allowed_hosts)
    api = create_app(
        service, resolver, reconciler=cast("RunLivenessReconciler | None", reconciler), anonymous=anonymous
    )
    return api, projection


def write_web_build(directory: Path) -> Path:
    """Write a small built web interface: an `index.html` with one inline script, and one hashed asset."""
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (directory / "assets" / "app-abc123.js").write_text("console.log('app');\n", encoding="utf-8")
    return directory
