"""What the service worker does when its server identity is momentarily unusable.

Two recorded failures shared one cause: a transient identity condition was
treated as terminal. A periodic heartbeat invalidated the identity generation
even when it re-resolved the very same worker record, which crashed whatever
submission was in flight; and a single unresolvable read raised out of the sync,
which escaped Prefect's `critical_service_loop` and ended the worker process.

Detection was never the problem — absent, stale and mismatched identity are all
correctly refused. What these cases pin is the consequence: refuse to execute,
stay alive, and resolve again on a later heartbeat.

Everything here runs against a real temporary Prefect server with the real
client, a real work pool and the real worker. Nothing on the identity path is
stubbed, because both defects were invisible to stubs: the previous suite
asserted the refusals with a fake client and passed throughout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolCreate
from prefect.testing.utilities import prefect_test_harness
from prefect.utilities.services import critical_service_loop

from infrahub_sync.service.worker import (
    ServiceProcessJobConfiguration,
    ServiceProcessWorker,
    ServiceWorkerIdentityError,
    service_worker_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

# The worker type this entrypoint registers under, so the real server accepts a
# pool the real worker will then adopt.
POOL_TYPE = "infrahub-sync-service-process"


@pytest.fixture(scope="module")
def _prefect_server() -> Iterator[None]:
    """One real Prefect server for the module, because starting one is not cheap."""
    with prefect_test_harness():
        yield


@pytest.fixture
async def worker(_prefect_server: None, request: pytest.FixtureRequest) -> ServiceProcessWorker:
    """A real worker, in its own real pool, resolved against the real server."""
    pool = f"identity-recovery-{request.node.name[-40:].replace('_', '-')}"[:60]
    async with get_client() as client:
        await client.create_work_pool(work_pool=WorkPoolCreate(name=pool, type=POOL_TYPE))
    return ServiceProcessWorker(work_pool_name=pool, name=service_worker_name())


def prepared(worker: ServiceProcessWorker) -> ServiceProcessJobConfiguration:
    """Stamp a configuration the way a submission in flight would have."""
    configuration = worker.job_configuration()
    configuration._identity_generation = worker._identity_generation
    configuration.env = {"PREFECT__WORKER_ID": str(worker.backend_id)}
    return configuration


def accepted(worker: ServiceProcessWorker, configuration: ServiceProcessJobConfiguration) -> bool:
    """Report whether the worker would still start a child for this configuration."""
    try:
        worker._validate_child_identity(configuration)
    except ServiceWorkerIdentityError:
        return False
    return True


async def drop_the_pool(worker: ServiceProcessWorker) -> None:
    """Remove the pool through the real client.

    The worker creates it again on its next heartbeat, so what the following
    sync meets is the real transient: the pool is back and this worker's own
    record has not been written yet. That is the same shape as the recorded
    pause/unpause window.
    """
    async with get_client() as client:
        await client.delete_work_pool(worker._work_pool_name)


def watch_polling(worker: ServiceProcessWorker) -> list[bool]:
    """Record whether Prefect's scheduled-run read is reached, without submitting."""
    reached: list[bool] = []

    async def _scheduled() -> list[object]:  # noqa: RUF029 -- Prefect awaits this hook
        reached.append(True)
        return []

    worker._get_scheduled_flow_runs = cast("Any", _scheduled)  # type: ignore[method-assign]
    return reached


def fail_reads_once(worker: ServiceProcessWorker) -> None:
    """Make the next identity read fail, then let the real one resolve again.

    A deleted pool is permanent, so it proves the escape but cannot prove
    recovery. What the recorded pause/unpause window actually is, is one read
    that does not yet see an online record followed by one that does. The
    failure is injected at the client boundary; resolution afterwards is the
    real method against the real server.
    """
    real = ServiceProcessWorker._read_worker_records
    attempts = {"count": 0}

    async def _read(self: ServiceProcessWorker = worker) -> list[object]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ConnectError("not yet")
        return cast("list[object]", await real(self))

    worker._read_worker_records = cast("Any", _read)  # type: ignore[method-assign]


async def test_a_heartbeat_that_resolves_the_same_record_keeps_a_submission_valid(
    worker: ServiceProcessWorker,
) -> None:
    """An ordinary heartbeat changes nothing about the identity, so it invalidates nothing.

    This is the producer failure. A configuration is prepared during submission
    against the generation the poll captured; if a routine heartbeat moves that
    generation, the child is refused after Prefect has already proposed
    Submitting, and the run is marked Crashed for a reason that never happened.
    """
    async with worker:
        await worker.sync_with_backend()
        first = worker.backend_id
        configuration = prepared(worker)

        assert first is not None, "the real server did not issue an identity"
        assert accepted(worker, configuration), "the fresh configuration was refused"

        await worker.sync_with_backend()

        assert worker.backend_id == first, "the heartbeat resolved a different record"
        assert accepted(worker, configuration), (
            "a heartbeat that re-resolved the same identity invalidated an in-flight submission"
        )


async def test_an_unresolvable_identity_does_not_end_the_worker_loop(worker: ServiceProcessWorker) -> None:
    """This is the clean-host failure, run through the loop that actually died.

    `critical_service_loop` forgives intermittent trouble and terminates on
    anything else, so raising out of the sync ends the worker process and the
    deployment never comes back. Refusing to execute is required; ending the
    process is not.
    """
    async with worker:
        await worker.sync_with_backend()
        assert worker.backend_id is not None
        await drop_the_pool(worker)

        await critical_service_loop(workload=worker.sync_with_backend, interval=0, run_once=True)

        assert worker.backend_id is None, "an unresolved identity was left installed"


async def test_a_worker_without_an_identity_submits_nothing(worker: ServiceProcessWorker) -> None:
    """Surviving the failure must not turn into polling without an identity."""
    async with worker:
        await worker.sync_with_backend()
        await drop_the_pool(worker)
        reached = watch_polling(worker)

        await worker.sync_with_backend()

        assert worker.backend_id is None
        assert await worker.get_and_submit_flow_runs() == []
        assert reached == [], "the worker read scheduled runs while its identity was unresolved"


async def test_the_worker_resolves_again_on_a_later_heartbeat(worker: ServiceProcessWorker) -> None:
    """Forward progress. The identity comes back, and the worker starts polling again.

    The pool is recreated by the failed heartbeat itself, so the next one writes
    this worker's record and resolves it. Nothing external has to intervene.
    """
    async with worker:
        await worker.sync_with_backend()
        assert worker.backend_id is not None
        fail_reads_once(worker)

        await critical_service_loop(workload=worker.sync_with_backend, interval=0, run_once=True)
        deferred = worker.backend_id

        await critical_service_loop(workload=worker.sync_with_backend, interval=0, run_once=True)
        reached = watch_polling(worker)
        await worker.get_and_submit_flow_runs()

        assert deferred is None, "the unresolved read left an identity installed"
        assert worker.backend_id is not None, "the identity never came back"
        assert worker._has_successfully_synced, "readiness never returned"
        assert reached == [True], "the worker did not resume polling once its identity resolved"


async def test_a_real_identity_change_still_refuses_a_stale_submission(worker: ServiceProcessWorker) -> None:
    """The safety property, unchanged. A different record means a different identity.

    Kept alongside the case above deliberately: making a heartbeat harmless must
    not make a genuine replacement harmless. Both identities here are real UUIDs
    the server issued for this worker's name.
    """
    async with worker:
        await worker.sync_with_backend()
        first = worker.backend_id
        stale = prepared(worker)
        assert accepted(worker, stale)

        # The same record, reissued under a new identifier: what a replacement
        # looks like to this worker. Only the identifier is substituted; the
        # comparison that has to catch it is the real one.
        reissued = uuid4()
        real = ServiceProcessWorker._read_worker_records

        async def _rebound(self: ServiceProcessWorker = worker) -> list[Any]:
            records = await real(self)
            return [record.model_copy(update={"id": reissued}) for record in records]

        worker._read_worker_records = cast("Any", _rebound)  # type: ignore[method-assign]
        await worker.sync_with_backend()
        second = worker.backend_id

        assert isinstance(first, UUID)
        assert second == reissued, "the reissued identifier was not installed"
        assert second != first, "the identity did not change, so this proves nothing"
        assert not accepted(worker, stale), "a configuration prepared for the previous identity was accepted"


async def test_a_transport_blip_is_not_terminal_for_the_worker(worker: ServiceProcessWorker) -> None:
    """Prefect's loop forgives intermittent transport errors; this path must not undo that.

    The identity read is converted into a `RuntimeError` subclass, which the loop
    does not forgive, so one dropped connection ends the process. The blip is
    injected at the client boundary, leaving the identity path itself real.
    """
    async with worker:
        await worker.sync_with_backend()
        assert worker.backend_id is not None

        async def _blip() -> list[object]:
            raise httpx.ConnectError("momentary")

        worker._read_worker_records = cast("Any", _blip)  # type: ignore[method-assign]

        await critical_service_loop(workload=worker.sync_with_backend, interval=0, run_once=True)

        assert worker.backend_id is None, "an unresolved identity was left installed after a blip"
