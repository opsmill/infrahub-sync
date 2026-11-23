"""Health checkers built from fake checks, for the tests of `GET /health`.

No check reaches a real component: each one returns, or raises the error a client would raise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from infrahub_sync.service.health import DEPENDENCY_NAMES, DependencyCheck, HealthChecker
from infrahub_sync.service.orchestration import PoolStatus, PoolWorker

if TYPE_CHECKING:
    from infrahub_sync.client.models import HealthResource

WORKER_NAME = "infrahub-sync-worker-3f0c2a9e-6a1b-4c55-9d3e-2b7c1e0f4a11"
STALE_WORKER_NAME = "infrahub-sync-worker-8b1d7e40-2c9f-4e0a-a6b3-5f2e9c7d1b08"


async def _up() -> None:  # noqa: RUF029 - the checker awaits every check.
    return None


async def _refused() -> None:  # noqa: RUF029 - the checker awaits every check.
    raise ConnectionRefusedError


def _workers(now: datetime) -> tuple[PoolWorker, ...]:
    return (
        PoolWorker(
            worker_id="3f0c2a9e-6a1b-4c55-9d3e-2b7c1e0f4a11",
            status="online",
            last_heartbeat=now - timedelta(seconds=5),
            heartbeat_interval_seconds=10.0,
            name=WORKER_NAME,
        ),
        PoolWorker(
            worker_id="8b1d7e40-2c9f-4e0a-a6b3-5f2e9c7d1b08",
            status="offline",
            last_heartbeat=now - timedelta(hours=1),
            heartbeat_interval_seconds=10.0,
            name=STALE_WORKER_NAME,
        ),
    )


async def _pool(now: datetime) -> PoolStatus:  # noqa: RUF029 - the checker awaits the pool read.
    return PoolStatus(detail_available=True, queue_depth=0, observed_at=now, workers=_workers(now))


def fake_health_checker(*, healthy: bool = True) -> HealthChecker:
    """Return a checker whose components are all up, or whose database refuses connections.

    The work pool lists one live and one stale worker in both cases.
    """
    checks = [
        DependencyCheck(name, _refused if not healthy and name == "database" else _up) for name in DEPENDENCY_NAMES
    ]
    return HealthChecker(
        checks=checks, pool_reader=_pool, minimum=1, check_timeout=3, wall_clock=lambda: datetime.now(timezone.utc)
    )


class RaisingHealthChecker:
    """A checker that fails outside its checks, as a fault in the endpoint itself would."""

    async def report(self) -> HealthResource:  # noqa: PLR6301 - the route calls it as a method.
        """Raise an error whose text must not reach the answer."""
        msg = "health-checker-canary db.internal 10.0.0.7"
        raise RuntimeError(msg)
