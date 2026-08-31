"""Run the managed ProcessWorker with a canonical self-hosted Prefect identity."""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import httpx
from prefect.client.schemas.objects import Worker, WorkerStatus
from prefect.exceptions import ObjectNotFound
from prefect.workers.process import ProcessWorker

if TYPE_CHECKING:
    from collections.abc import Sequence

_IDENTITY_ERROR = "managed worker identity is unavailable"
_WORKER_NAME_PREFIX = "infrahub-sync-managed"
_WORKER_PAGE_SIZE = 200


class ManagedWorkerIdentityError(RuntimeError):
    """Refuse polling when the worker's exact server identity is unavailable."""


def managed_worker_name() -> str:
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


class ManagedProcessWorker(ProcessWorker):
    """Resolve this process worker's server record before Prefect can poll runs."""

    @classmethod
    def __dispatch_key__(cls) -> str | None:
        """Keep the explicit managed entrypoint separate from Prefect's process key."""
        return "infrahub-sync-managed-process"

    async def sync_with_backend(self) -> None:
        """Make readiness depend on a fresh heartbeat followed by identity resolution."""
        self._has_successfully_synced = False
        previous_identity = self.backend_id
        self.backend_id = None
        if previous_identity is not None and os.environ.get("PREFECT__WORKER_ID") == str(previous_identity):
            os.environ.pop("PREFECT__WORKER_ID", None)
        await super().sync_with_backend()

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
                raise ManagedWorkerIdentityError(_IDENTITY_ERROR)
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
                raise ManagedWorkerIdentityError(_IDENTITY_ERROR)
        except (httpx.HTTPError, ObjectNotFound, AttributeError, TypeError, ValueError):
            raise ManagedWorkerIdentityError(_IDENTITY_ERROR) from None
        self._record_worker_id(worker_id)

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
    parser = argparse.ArgumentParser(description="Start an Infrahub Sync managed process worker")
    parser.add_argument("--pool", required=True, help="existing Prefect process work pool")
    arguments = parser.parse_args(argv)
    pool = arguments.pool
    if not isinstance(pool, str) or not pool.strip():
        parser.error("--pool must be non-empty")
    return pool


def main(argv: Sequence[str] | None = None) -> int:
    """Start one fail-closed managed process worker."""
    worker = ManagedProcessWorker(
        work_pool_name=_pool_argument(argv),
        name=managed_worker_name(),
        create_pool_if_not_found=False,
    )
    asyncio.run(worker.start())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
