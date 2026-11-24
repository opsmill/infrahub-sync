"""`GET /api/health` on the running development stack (spec 029).

The stack's four components are running and its worker sends heartbeats, so the report is
healthy. Skips, like the rest of the suite, when the stack is not running.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from infrahub_sync.client.models import HealthResource

pytestmark = pytest.mark.smoke


def test_the_development_stack_reports_four_components_up_and_a_live_worker(smoke_env: dict[str, Any]) -> None:
    response = httpx.get(f"{smoke_env['urls']['sync_api']}/api/health", timeout=30)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    report = HealthResource.model_validate(response.json())
    assert report.status == "healthy"
    assert [(dependency.name, dependency.status) for dependency in report.dependencies] == [
        ("database", "up"),
        ("object_store", "up"),
        ("task_manager", "up"),
        ("task_manager_db", "up"),
    ]
    assert report.workers.state == "known"
    assert any(worker.state == "live" for worker in report.workers.items)
