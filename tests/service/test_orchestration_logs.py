"""`PrefectOrchestration.read_logs` reads a run's flow-run lines from Prefect (spec 017, FR-004, FR-010)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest

pytest.importorskip("prefect")

from prefect.client.schemas.filters import LogFilter
from prefect.client.schemas.sorting import LogSort
from prefect.exceptions import ObjectNotFound

from infrahub_sync.service.orchestration import (
    LOG_PAGE_SIZE,
    FlowRunLog,
    OrchestrationUnavailableError,
    PrefectOrchestration,
)

T0 = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)


def _prefect_log(flow_run_id: UUID, index: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        flow_run_id=flow_run_id,
        timestamp=T0 + timedelta(seconds=index),
        level=20,
        message=f"infrahub_sync | line {index}",
        name="prefect.flow_runs",
    )


class _FakePrefectClient:
    """Answer flow-run and log reads from fixed data, recording each call."""

    def __init__(
        self,
        logs: list[SimpleNamespace],
        *,
        missing: frozenset[UUID] = frozenset(),
        failure: Exception | None = None,
    ) -> None:
        self.logs = logs
        self.missing = missing
        self.failure = failure
        self.flow_run_reads: list[UUID] = []
        self.log_reads: list[dict[str, Any]] = []

    async def read_flow_run(self, flow_run_id: UUID) -> SimpleNamespace:
        self.flow_run_reads.append(flow_run_id)
        if self.failure is not None:
            raise self.failure
        if flow_run_id in self.missing:
            raise ObjectNotFound(http_exc=Exception())
        return SimpleNamespace(id=flow_run_id)

    async def read_logs(
        self,
        log_filter: LogFilter | None = None,
        limit: int | None = None,
        offset: int | None = None,
        sort: LogSort | None = None,
    ) -> list[SimpleNamespace]:
        self.log_reads.append({"filter": log_filter, "limit": limit, "offset": offset, "sort": sort})
        start = offset or 0
        return self.logs[start : start + (limit or len(self.logs))]


def _orchestration(client: _FakePrefectClient) -> PrefectOrchestration:
    return PrefectOrchestration(cast("Any", client))


async def test_read_logs_reads_each_flow_run_then_pages_the_logs_until_a_short_page() -> None:
    plan_run, verify_run = uuid4(), uuid4()
    logs = [_prefect_log(plan_run if index % 2 else verify_run, index) for index in range(LOG_PAGE_SIZE * 2 + 7)]
    client = _FakePrefectClient(logs)

    result = await _orchestration(client).read_logs((str(plan_run), str(verify_run)))

    assert client.flow_run_reads == [plan_run, verify_run]
    assert [call["offset"] for call in client.log_reads] == [0, LOG_PAGE_SIZE, LOG_PAGE_SIZE * 2]
    assert all(call["limit"] == LOG_PAGE_SIZE == 200 for call in client.log_reads)
    assert all(call["sort"] == LogSort.TIMESTAMP_ASC for call in client.log_reads)
    log_filter = client.log_reads[0]["filter"]
    assert isinstance(log_filter, LogFilter)
    assert log_filter.flow_run_id is not None
    assert log_filter.flow_run_id.any_ == [plan_run, verify_run]
    assert result.expired is False
    assert len(result.logs) == len(logs)
    first = result.logs[0]
    assert first == FlowRunLog(
        log_id=str(logs[0].id),
        flow_run_id=str(verify_run),
        timestamp=T0,
        level=20,
        message="infrahub_sync | line 0",
    )


async def test_read_logs_stops_after_one_page_when_the_first_page_is_short() -> None:
    flow_run = uuid4()
    client = _FakePrefectClient([_prefect_log(flow_run, 0)])

    result = await _orchestration(client).read_logs((str(flow_run),))

    assert len(client.log_reads) == 1
    assert len(result.logs) == 1


async def test_read_logs_reports_expired_when_prefect_no_longer_holds_a_flow_run() -> None:
    held, gone = uuid4(), uuid4()
    client = _FakePrefectClient([_prefect_log(held, 0)], missing=frozenset({gone}))

    result = await _orchestration(client).read_logs((str(held), str(gone)))

    assert result.expired is True
    assert result.logs == ()
    assert client.log_reads == []


async def test_read_logs_without_flow_runs_reads_nothing() -> None:
    client = _FakePrefectClient([])

    result = await _orchestration(client).read_logs(())

    assert (result.expired, result.logs) == (False, ())
    assert (client.flow_run_reads, client.log_reads) == ([], [])


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("prefect-server:4200 refused"),
        httpx.ReadTimeout("timed out"),
        TimeoutError(),
        httpx.HTTPStatusError(
            "server error",
            request=httpx.Request("POST", "http://prefect-server:4200/api/logs/filter"),
            response=httpx.Response(500),
        ),
    ],
)
async def test_a_client_error_or_timeout_raises_orchestration_unavailable(failure: Exception) -> None:
    client = _FakePrefectClient([], failure=failure)

    with pytest.raises(OrchestrationUnavailableError) as raised:
        await _orchestration(client).read_logs((str(uuid4()),))

    assert "prefect" not in str(raised.value).lower()
