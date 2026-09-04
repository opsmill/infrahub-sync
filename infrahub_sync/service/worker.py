"""Run the service ProcessWorker with a canonical self-hosted Prefect identity."""

from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import anyio
import httpx
from prefect.client.schemas.objects import Worker, WorkerStatus
from prefect.exceptions import ObjectNotFound
from prefect.logging.loggers import PrefectLogAdapter
from prefect.workers.process import ProcessJobConfiguration, ProcessWorker, ProcessWorkerResult
from pydantic import PrivateAttr

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterator, Sequence

    from anyio.abc import TaskStatus
    from prefect.client.schemas.objects import Flow as APIFlow
    from prefect.client.schemas.objects import FlowRun, WorkPool
    from prefect.client.schemas.responses import DeploymentResponse

_IDENTITY_ERROR = "service worker identity is unavailable"
_WORKER_NAME_PREFIX = "infrahub-sync-service"
_WORKER_PAGE_SIZE = 200
_SUBMISSION_IDENTITY: ContextVar[tuple[bool, int | None]] = ContextVar(
    "service_worker_submission_identity",
    default=(False, None),
)


class ServiceWorkerIdentityError(RuntimeError):
    """Refuse polling when the worker's exact server identity is unavailable."""


def service_worker_name() -> str:
    """Return a process-unique Prefect worker name for the supported entrypoint."""
    return f"{_WORKER_NAME_PREFIX}-{uuid4()}"


def _canonical_uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return parsed if str(parsed) == value else None


def _without_worker_id(
    logger: logging.Logger | logging.LoggerAdapter[logging.Logger],
) -> logging.Logger | logging.LoggerAdapter[logging.Logger]:
    if not isinstance(logger, PrefectLogAdapter):
        return logger
    extra = dict(logger.extra or {})
    extra.pop("worker_id", None)
    return PrefectLogAdapter(logger.logger, extra=extra)


class ServiceProcessJobConfiguration(ProcessJobConfiguration):
    """Carry the worker identity generation used to prepare this child."""

    _identity_generation: int | None = PrivateAttr(default=None)

    def prepare_for_flow_run(  # pylint: disable=too-many-positional-arguments
        self,
        flow_run: FlowRun,
        deployment: DeploymentResponse | None = None,
        flow: APIFlow | None = None,
        work_pool: WorkPool | None = None,
        worker_name: str | None = None,
        worker_id: UUID | None = None,
    ) -> None:
        super().prepare_for_flow_run(
            flow_run,
            deployment,
            flow,
            work_pool,
            worker_name,
            worker_id,
        )
        bound, generation = _SUBMISSION_IDENTITY.get()
        self._identity_generation = generation if bound else None


class _IdentityLeaseTaskStatus:
    """Release the identity lease when Prefect reports that the child started."""

    def __init__(self, task_status: TaskStatus[int], lock: asyncio.Lock) -> None:
        self._task_status = task_status
        self._lock = lock
        self._released = False

    def started(self, value: int | None = None) -> None:
        try:
            if value is None:
                cast("TaskStatus[None]", self._task_status).started()
            else:
                self._task_status.started(value)
        finally:
            self.release()

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._lock.release()


class ServiceProcessWorker(ProcessWorker):
    """Resolve this process worker's server record before Prefect can poll runs."""

    job_configuration: type[ServiceProcessJobConfiguration] = ServiceProcessJobConfiguration

    def __init__(  # pylint: disable=too-many-positional-arguments
        self,
        work_pool_name: str,
        work_queues: list[str] | None = None,
        name: str | None = None,
        prefetch_seconds: float | None = None,
        create_pool_if_not_found: bool = True,
        limit: int | None = None,
        heartbeat_interval_seconds: int | None = None,
        *,
        base_job_template: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            work_pool_name=work_pool_name,
            work_queues=work_queues,
            name=name,
            prefetch_seconds=prefetch_seconds,
            create_pool_if_not_found=create_pool_if_not_found,
            limit=limit,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            base_job_template=base_job_template,
        )
        self._identity_lock = asyncio.Lock()
        self._identity_generation = 0
        self._identity_refresh_requests = 0
        self._identity_refresh_active = False

    @classmethod
    def __dispatch_key__(cls) -> str | None:
        """Keep the explicit service entrypoint separate from Prefect's process key."""
        return "infrahub-sync-service-process"

    async def sync_with_backend(self) -> None:
        """Make readiness depend on a fresh heartbeat followed by identity resolution."""
        self._identity_refresh_requests += 1
        try:
            async with self._identity_lock:
                self._identity_refresh_active = True
                self._identity_generation += 1
                self._has_successfully_synced = False
                previous_identity = self.backend_id
                self.backend_id = None
                if previous_identity is not None and os.environ.get("PREFECT__WORKER_ID") == str(previous_identity):
                    os.environ.pop("PREFECT__WORKER_ID", None)
                try:
                    await super().sync_with_backend()
                finally:
                    self._identity_refresh_active = False
        finally:
            self._identity_refresh_requests -= 1

    async def _initialize_after_sync(self) -> None:
        if self._work_pool is None:
            await super()._initialize_after_sync()
            return
        await self._refresh_worker_identity()
        await super()._initialize_after_sync()

    async def _refresh_worker_identity(self) -> None:
        """Resolve exactly one online, pool-scoped record and install its UUID."""
        try:
            records = await self._read_worker_records()
            matches = [record for record in records if record.name == self.name]
            if len(matches) != 1:
                raise ServiceWorkerIdentityError(_IDENTITY_ERROR)
            record = matches[0]
            worker_id = _canonical_uuid(record.id)
            record_pool_id = _canonical_uuid(record.work_pool_id)
            expected_pool_id = _canonical_uuid(self.work_pool.id)
            if (
                worker_id is None
                or record_pool_id is None
                or expected_pool_id is None
                or record_pool_id != expected_pool_id
                or record.status != WorkerStatus.ONLINE
            ):
                raise ServiceWorkerIdentityError(_IDENTITY_ERROR)
        except (httpx.HTTPError, ObjectNotFound, AttributeError, TypeError, ValueError):
            raise ServiceWorkerIdentityError(_IDENTITY_ERROR) from None
        self._record_worker_id(worker_id)

    def _record_worker_id(self, remote_id: UUID) -> None:
        super()._record_worker_id(remote_id)
        self._logger = _without_worker_id(self._logger)

    def get_flow_run_logger(self, flow_run: FlowRun) -> PrefectLogAdapter:
        logger = _without_worker_id(super().get_flow_run_logger(flow_run))
        return cast("PrefectLogAdapter", logger)

    def _submission_generation(self) -> int | None:
        if self._identity_refresh_requests or self._identity_refresh_active or self.backend_id is None:
            return None
        return self._identity_generation

    async def get_and_submit_flow_runs(self) -> list[FlowRun]:
        generation = self._submission_generation()
        if generation is None:
            self._logger.debug("Service worker identity is unavailable; skipping flow run submission.")
            self._last_polled_time = datetime.now(timezone.utc)
            return []
        token = _SUBMISSION_IDENTITY.set((True, generation))
        try:
            return await super().get_and_submit_flow_runs()
        finally:
            _SUBMISSION_IDENTITY.reset(token)

    async def _submit_run_and_capture_errors(
        self,
        flow_run: FlowRun,
        task_status: TaskStatus[int | Exception] | None = None,
    ) -> ProcessWorkerResult | Exception:
        bound, generation = _SUBMISSION_IDENTITY.get()
        token = None
        if not bound:
            generation = self._submission_generation()
            token = _SUBMISSION_IDENTITY.set((True, generation))
        try:
            return cast(
                "ProcessWorkerResult | Exception",
                await super()._submit_run_and_capture_errors(flow_run, task_status),
            )
        finally:
            if token is not None:
                _SUBMISSION_IDENTITY.reset(token)

    def _validate_child_identity(self, configuration: ProcessJobConfiguration) -> None:
        if not isinstance(configuration, ServiceProcessJobConfiguration):
            raise ServiceWorkerIdentityError(_IDENTITY_ERROR)
        if (
            self._identity_refresh_requests
            or self._identity_refresh_active
            or self.backend_id is None
            or configuration._identity_generation != self._identity_generation
            or configuration.env.get("PREFECT__WORKER_ID") != str(self.backend_id)
        ):
            raise ServiceWorkerIdentityError(_IDENTITY_ERROR)

    async def run(
        self,
        flow_run: FlowRun,
        configuration: ProcessJobConfiguration,
        task_status: TaskStatus[int] | None = None,
    ) -> ProcessWorkerResult:
        """Start a child only while its prepared identity generation is current."""
        if self._identity_refresh_requests or self._identity_refresh_active:
            raise ServiceWorkerIdentityError(_IDENTITY_ERROR)
        await self._identity_lock.acquire()
        effective_status: TaskStatus[int] = task_status if task_status is not None else anyio.TASK_STATUS_IGNORED
        lease = _IdentityLeaseTaskStatus(effective_status, self._identity_lock)
        try:
            self._validate_child_identity(configuration)
            return await super().run(flow_run, configuration, task_status=lease)
        finally:
            lease.release()

    async def _read_worker_records(self) -> list[Worker]:
        records: list[Worker] = []
        offset = 0
        while True:
            page = await self.client.read_workers_for_work_pool(
                self._work_pool_name,
                offset=offset,
                limit=_WORKER_PAGE_SIZE,
            )
            if not isinstance(page, list):
                raise TypeError
            records.extend(page)
            if len(page) < _WORKER_PAGE_SIZE:
                return records
            offset += _WORKER_PAGE_SIZE


def _pool_argument(argv: Sequence[str] | None = None) -> str:
    parser = argparse.ArgumentParser(description="Start an Infrahub Sync service process worker")
    parser.add_argument("--pool", required=True, help="existing Prefect process work pool")
    arguments = parser.parse_args(argv)
    pool = arguments.pool
    if not isinstance(pool, str) or not pool.strip():
        parser.error("--pool must be non-empty")
    return pool


@contextmanager
def neutral_working_directory() -> Iterator[Path]:
    """Run this parent from a fresh empty directory, and remove it on the way out.

    Prefect prepends the process working directory to ``sys.path`` every time it resolves
    a deployment's module entrypoint, and this parent does resolve it: a ``ProcessWorker``
    builds a ``Runner`` in this process, and that runner imports the flow to run
    ``on_crashed`` hooks once a child dies. A parent started inside a source tree would
    import that copy of the package rather than the installed distribution, which is the
    checkout dependence this service does not have. An empty directory holds nothing to
    import, so the installed distribution is the only answer available -- for the whole
    lifetime of the parent, hook inspection after a crash included.
    """
    previous = Path.cwd()
    root = Path(mkdtemp(prefix="infrahub-sync-worker-"))
    os.chdir(root)
    try:
        yield root
    finally:
        os.chdir(previous)
        rmtree(root, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Start one fail-closed service process worker."""
    pool = _pool_argument(argv)
    # Entered before the worker exists, so nothing this parent imports later -- the
    # runner, its crash hooks, or an adapter -- can resolve out of a source tree.
    with neutral_working_directory():
        worker = ServiceProcessWorker(
            work_pool_name=pool,
            name=service_worker_name(),
            create_pool_if_not_found=False,
        )
        asyncio.run(worker.start())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
