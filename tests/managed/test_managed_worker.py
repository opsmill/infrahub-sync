"""Managed process-worker identity resolution and child attribution."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError

pytest.importorskip("prefect")

from prefect import __version__ as prefect_version
from prefect.client.schemas.objects import WorkerStatus
from prefect.logging.handlers import APILogHandler
from prefect.server.schemas.actions import LogCreate as ServerLogCreate
from prefect.workers.process import ProcessWorker

from infrahub_sync.managed.worker import ManagedProcessWorker, ManagedWorkerIdentityError, managed_worker_name

if TYPE_CHECKING:
    from prefect.client.schemas.objects import FlowRun, WorkPool

POOL_NAME = "managed-pool"
POOL_ID = UUID("e0679e8a-9460-4ca7-8bf1-70bf967eed2d")
FLOW_ID = UUID("ed4778cb-f2cf-4b1f-a87b-68be37659e93")
FIRST_WORKER_ID = UUID("8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0")
SECOND_WORKER_ID = UUID("d08f703b-ce73-4269-a7aa-1bfb00f8cc63")


@pytest.fixture(autouse=True)
def _isolate_worker_attribution_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PREFECT__WORKER_ID", raising=False)


def _record(
    name: str,
    worker_id: object = FIRST_WORKER_ID,
    *,
    work_pool_id: object = POOL_ID,
    status: object = WorkerStatus.ONLINE,
) -> SimpleNamespace:
    return SimpleNamespace(id=worker_id, name=name, work_pool_id=work_pool_id, status=status)


class _WorkerClient:
    def __init__(self, records: list[object]) -> None:
        self.records = records
        self.calls: list[tuple[str, int | None, int | None]] = []

    async def read_workers_for_work_pool(
        self,
        work_pool_name: str,
        worker_filter: object = None,  # noqa: ARG002 - pinned Prefect client shape.
        offset: int | None = None,
        limit: int | None = None,
    ) -> list[object]:
        self.calls.append((work_pool_name, offset, limit))
        start = offset or 0
        stop = None if limit is None else start + limit
        return self.records[start:stop]

    async def read_flow(self, flow_id: UUID) -> SimpleNamespace:  # noqa: PLR6301 - Prefect client protocol.
        return SimpleNamespace(id=flow_id, name="managed-flow", labels={})


def _worker(name: str, records: list[Any]) -> ManagedProcessWorker:
    worker = ManagedProcessWorker(work_pool_name=POOL_NAME, name=name)
    worker._client = cast("Any", _WorkerClient(records))
    worker._work_pool = cast(
        "WorkPool",
        SimpleNamespace(id=POOL_ID, name=POOL_NAME, base_job_template=worker.get_default_base_job_template()),
    )
    return worker


def _flow_run() -> SimpleNamespace:
    return SimpleNamespace(
        id=FLOW_ID,
        name="managed-run",
        flow_id=SECOND_WORKER_ID,
        deployment_id=None,
        job_variables={},
    )


class _Runner:
    def __init__(self) -> None:
        self.child_environments: list[dict[str, str | None]] = []

    async def execute_flow_run(self, **kwargs: Any) -> SimpleNamespace:  # noqa: ANN401
        self.child_environments.append(kwargs["env"])
        kwargs["task_status"].started(42)
        return SimpleNamespace(returncode=0, pid=42)


def _stub_submission(worker: ManagedProcessWorker) -> _Runner:
    runner = _Runner()
    worker._runner = cast("Any", runner)
    worker._emit_flow_run_submitted_event = cast("Any", lambda _configuration: None)  # type: ignore[method-assign]
    worker._give_worker_labels_to_flow_run = cast("Any", AsyncMock())  # type: ignore[method-assign]
    worker._propose_submitting_state = cast("Any", AsyncMock())  # type: ignore[method-assign]
    worker._propose_crashed_state = cast("Any", AsyncMock())  # type: ignore[method-assign]
    worker._release_limit_slot = cast("Any", lambda _flow_run_id: None)  # type: ignore[method-assign]
    return runner


@pytest.mark.parametrize(
    "records",
    [
        pytest.param([], id="absent"),
        pytest.param([_record("managed-a", str(FIRST_WORKER_ID).upper())], id="noncanonical-id"),
        pytest.param(
            [_record("managed-a", FIRST_WORKER_ID), _record("managed-a", SECOND_WORKER_ID)],
            id="ambiguous-name",
        ),
        pytest.param([_record("managed-a", status=WorkerStatus.OFFLINE)], id="not-online"),
        pytest.param([_record("managed-a", work_pool_id=SECOND_WORKER_ID)], id="wrong-pool"),
    ],
)
async def test_unresolved_identity_refuses_before_polling(records: list[object]) -> None:
    worker = _worker("managed-a", records)
    polled: list[bool] = []

    worker._get_scheduled_flow_runs = cast(  # type: ignore[method-assign]
        "Any",
        AsyncMock(side_effect=lambda: polled.append(True) or []),
    )

    with pytest.raises(ManagedWorkerIdentityError, match="managed worker identity is unavailable"):
        await worker._initialize_after_sync()

    assert worker.backend_id is None
    assert not worker._has_successfully_synced
    assert await worker.get_and_submit_flow_runs() == []
    assert polled == []


async def test_restart_resolves_the_current_server_worker_uuid() -> None:
    first = _worker("managed-a", [_record("managed-a", FIRST_WORKER_ID)])
    restarted = _worker("managed-a", [_record("managed-a", SECOND_WORKER_ID)])

    await first._refresh_worker_identity()
    await restarted._refresh_worker_identity()

    assert first.backend_id == FIRST_WORKER_ID
    assert restarted.backend_id == SECOND_WORKER_ID


def test_supported_worker_entrypoint_generates_a_distinct_name_per_process() -> None:
    first = managed_worker_name()
    second = managed_worker_name()

    assert first != second
    assert str(UUID(first[-36:])) == first[-36:]
    assert str(UUID(second[-36:])) == second[-36:]


async def test_uniquely_named_workers_do_not_share_identity() -> None:
    records = [_record("managed-a", FIRST_WORKER_ID), _record("managed-b", SECOND_WORKER_ID)]
    first = _worker("managed-a", records)
    second = _worker("managed-b", records)

    await first._refresh_worker_identity()
    await second._refresh_worker_identity()

    assert first.backend_id == FIRST_WORKER_ID
    assert second.backend_id == SECOND_WORKER_ID
    assert first.backend_id != second.backend_id


async def test_self_hosted_logs_omit_worker_metadata_but_keep_child_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = _worker("managed-a", [_record("managed-a", FIRST_WORKER_ID)])
    await worker._refresh_worker_identity()

    with caplog.at_level(logging.INFO):
        worker._logger.info("managed worker heartbeat completed")
        worker.get_flow_run_logger(cast("Any", _flow_run())).info("managed run submitted")

    worker_record = next(record for record in caplog.records if record.message == "managed worker heartbeat completed")
    flow_record = next(record for record in caplog.records if record.message == "managed run submitted")
    assert not hasattr(worker_record, "worker_id")

    payload = APILogHandler().prepare(flow_record)
    payload.pop("__payload_size__")
    assert "worker_id" not in payload
    ServerLogCreate.model_validate(payload)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ServerLogCreate.model_validate({**payload, "worker_id": str(FIRST_WORKER_ID)})

    assert worker.backend_id == FIRST_WORKER_ID
    assert worker_record.message == "managed worker heartbeat completed"


async def test_polling_submission_refuses_when_refresh_changes_identity_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker("managed-a", [_record("managed-a", FIRST_WORKER_ID)])
    await worker._refresh_worker_identity()
    runner = _stub_submission(worker)
    poll_started = asyncio.Event()
    release_poll = asyncio.Event()
    refresh_cleared_identity = asyncio.Event()
    release_refresh = asyncio.Event()

    async def _scheduled_runs() -> list[object]:
        poll_started.set()
        await release_poll.wait()
        return [SimpleNamespace(flow_run=cast("Any", _flow_run()))]

    async def _base_sync(_worker: ProcessWorker) -> None:
        refresh_cleared_identity.set()
        await release_refresh.wait()

    worker._get_scheduled_flow_runs = cast("Any", _scheduled_runs)  # type: ignore[method-assign]
    monkeypatch.setattr(ProcessWorker, "sync_with_backend", _base_sync)

    async def _submit_scheduled(*, flow_run_response: list[object]) -> list[object]:
        assert flow_run_response
        return [await worker._submit_run_and_capture_errors(cast("Any", _flow_run()))]

    worker._submit_scheduled_flow_runs = cast("Any", _submit_scheduled)  # type: ignore[method-assign]
    worker._has_successfully_synced = True
    poll_task = asyncio.create_task(worker.get_and_submit_flow_runs())
    await poll_started.wait()
    refresh_task = asyncio.create_task(worker.sync_with_backend())
    await refresh_cleared_identity.wait()
    assert worker.backend_id is None

    release_poll.set()
    results = await poll_task
    release_refresh.set()
    await refresh_task

    assert len(results) == 1
    assert isinstance(results[0], ManagedWorkerIdentityError)
    assert runner.child_environments == []


async def test_polling_submission_refuses_when_refresh_rebinds_to_a_new_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker("managed-a", [_record("managed-a", FIRST_WORKER_ID)])
    await worker._refresh_worker_identity()
    runner = _stub_submission(worker)
    poll_started = asyncio.Event()
    release_poll = asyncio.Event()

    async def _scheduled_runs() -> list[object]:
        poll_started.set()
        await release_poll.wait()
        return [SimpleNamespace(flow_run=cast("Any", _flow_run()))]

    async def _base_sync(self: ProcessWorker) -> None:
        await self._initialize_after_sync()

    async def _base_initialize(self: ProcessWorker) -> None:  # noqa: RUF029 - Prefect lifecycle hook.
        self._has_successfully_synced = True

    worker._get_scheduled_flow_runs = cast("Any", _scheduled_runs)  # type: ignore[method-assign]
    monkeypatch.setattr(ProcessWorker, "sync_with_backend", _base_sync)
    monkeypatch.setattr(ProcessWorker, "_initialize_after_sync", _base_initialize)

    async def _submit_scheduled(*, flow_run_response: list[object]) -> list[object]:
        assert flow_run_response
        return [await worker._submit_run_and_capture_errors(cast("Any", _flow_run()))]

    worker._submit_scheduled_flow_runs = cast("Any", _submit_scheduled)  # type: ignore[method-assign]
    worker._has_successfully_synced = True
    poll_task = asyncio.create_task(worker.get_and_submit_flow_runs())
    await poll_started.wait()
    cast("_WorkerClient", worker._client).records = [_record("managed-a", SECOND_WORKER_ID)]
    await worker.sync_with_backend()
    assert worker.backend_id == SECOND_WORKER_ID

    release_poll.set()
    results = await poll_task

    assert len(results) == 1
    assert isinstance(results[0], ManagedWorkerIdentityError)
    assert runner.child_environments == []


async def test_recurring_sync_clears_readiness_until_identity_is_refreshed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker("managed-a", [_record("managed-a", FIRST_WORKER_ID)])
    worker._has_successfully_synced = True
    observed: list[bool] = []

    async def _base_sync(self: ProcessWorker) -> None:
        observed.append(self._has_successfully_synced)
        await self._initialize_after_sync()

    async def _base_initialize(self: ProcessWorker) -> None:  # noqa: RUF029 - awaited Prefect lifecycle hook.
        self._has_successfully_synced = True

    monkeypatch.setattr(ProcessWorker, "sync_with_backend", _base_sync)
    monkeypatch.setattr(ProcessWorker, "_initialize_after_sync", _base_initialize)

    await worker.sync_with_backend()

    assert observed == [False]
    assert worker.backend_id == FIRST_WORKER_ID
    assert worker._has_successfully_synced


async def test_prefect_381_injects_the_resolved_worker_uuid_into_the_actual_child_environment() -> None:
    assert prefect_version == "3.8.1"
    worker = _worker("managed-a", [_record("managed-a", FIRST_WORKER_ID)])
    await worker._refresh_worker_identity()
    flow_run = cast("FlowRun", _flow_run())
    runner = _stub_submission(worker)
    result = await worker._submit_run_and_capture_errors(flow_run)

    assert not isinstance(result, Exception)
    assert result.status_code == 0
    assert runner.child_environments[0]["PREFECT__WORKER_ID"] == str(FIRST_WORKER_ID)
