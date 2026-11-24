"""Worker liveness rule and worker name parsing for Prefect pool workers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("prefect")

from infrahub_sync.service.orchestration import (
    PoolWorker,
    _pool_worker,  # noqa: PLC2701 - the Prefect record parser is under test.
    worker_is_live,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _worker(
    *,
    status: str = "online",
    seconds_ago: float | None = 0.0,
    interval: float | None = 10.0,
) -> PoolWorker:
    heartbeat = None if seconds_ago is None else NOW - timedelta(seconds=seconds_ago)
    return PoolWorker(str(uuid4()), status, heartbeat, interval)


def test_online_worker_with_fresh_heartbeat_is_live() -> None:
    """An online worker that sent a heartbeat a moment ago is live."""
    assert worker_is_live(_worker(seconds_ago=1.0), NOW) is True


def test_offline_worker_is_not_live() -> None:
    """An offline worker is not live even with a fresh heartbeat."""
    assert worker_is_live(_worker(status="offline", seconds_ago=0.0), NOW) is False


def test_worker_without_heartbeat_is_not_live() -> None:
    """A worker that never sent a heartbeat is not live."""
    assert worker_is_live(_worker(seconds_ago=None), NOW) is False


def test_worker_without_interval_is_not_live() -> None:
    """A worker with no heartbeat interval is not live."""
    assert worker_is_live(_worker(interval=None), NOW) is False


def test_zero_interval_is_rejected_and_never_live() -> None:
    """A zero interval cannot be constructed, and the rule treats it as not live regardless."""
    with pytest.raises(ValueError):
        _worker(interval=0.0)
    worker = _worker(interval=10.0)
    object.__setattr__(worker, "heartbeat_interval_seconds", 0.0)  # noqa: PLC2801 - the only way past the frozen validator.
    assert worker_is_live(worker, NOW) is False


@pytest.mark.parametrize(
    ("interval", "limit"),
    [
        pytest.param(20.0, 60.0, id="three-intervals"),
        pytest.param(5.0, 30.0, id="thirty-second-floor"),
    ],
)
def test_heartbeat_exactly_at_limit_is_live(interval: float, limit: float) -> None:
    """A heartbeat exactly max(3 x interval, 30) seconds old is still live."""
    assert worker_is_live(_worker(seconds_ago=limit, interval=interval), NOW) is True


@pytest.mark.parametrize(
    ("interval", "limit"),
    [
        pytest.param(20.0, 60.0, id="three-intervals"),
        pytest.param(5.0, 30.0, id="thirty-second-floor"),
    ],
)
def test_heartbeat_one_second_past_limit_is_not_live(interval: float, limit: float) -> None:
    """A heartbeat one second older than max(3 x interval, 30) seconds is not live."""
    assert worker_is_live(_worker(seconds_ago=limit + 1, interval=interval), NOW) is False


def test_five_second_interval_uses_thirty_second_floor() -> None:
    """With a 5-second interval, a 20-second-old heartbeat (past 3 x 5) is live under the 30-second floor."""
    assert worker_is_live(_worker(seconds_ago=20.0, interval=5.0), NOW) is True


def test_heartbeat_in_the_future_counts_as_fresh() -> None:
    """A heartbeat later than now counts as zero seconds old."""
    assert worker_is_live(_worker(seconds_ago=-120.0), NOW) is True


def _prefect_record(name: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        status="ONLINE",
        last_heartbeat_time=NOW,
        heartbeat_interval_seconds=10,
        name=name,
    )


def test_prefect_record_name_is_kept() -> None:
    """A string Prefect worker name is kept on the parsed worker."""
    assert _pool_worker(_prefect_record("worker-1")).name == "worker-1"


@pytest.mark.parametrize("name", [None, 42, b"worker-1", ""], ids=["none", "int", "bytes", "empty"])
def test_non_string_prefect_name_gives_no_name(name: object) -> None:
    """A Prefect worker name that is not a non-empty string gives name=None and keeps the record valid."""
    worker = _pool_worker(_prefect_record(name))
    assert worker.name is None
    assert worker.status == "online"
