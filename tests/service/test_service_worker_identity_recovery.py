"""What the service worker does when its server identity is momentarily unusable.

Two recorded failures shared one cause: a transient identity condition was
treated as terminal. A periodic heartbeat invalidated the identity generation
even when it re-resolved the very same worker record, which crashed whatever
submission was in flight; and a single unresolvable read raised out of the sync,
which escaped Prefect's `critical_service_loop` and ended the worker process.

A third shares it: a submission that arrived while a heartbeat was in progress
was refused outright, with no wait and no recheck, even though the identity it
carried was the one still installed once the heartbeat finished.

Detection was never the problem — absent, stale and mismatched identity are all
correctly refused. What these cases pin is the consequence: refuse to execute,
stay alive, resolve again on a later heartbeat, and wait through a refresh
rather than fail inside it.

Everything here runs against a real temporary Prefect server with the real
client, a real work pool and the real worker. Nothing on the identity path is
stubbed, because these defects were invisible to stubs: the previous suite
asserted the refusals with a fake client and passed throughout. The only thing
stood in for is the child process itself, so that reaching the start is
observable without spawning one.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
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
# What a momentary read failure looks like at the client boundary.
BLIP = "momentary"


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
            raise httpx.ConnectError(BLIP)
        return cast("list[object]", await real(self))

    worker._read_worker_records = cast("Any", _read)  # type: ignore[method-assign]


def pause_the_next_refresh(
    worker: ServiceProcessWorker,
    *,
    reissue: UUID | None = None,
) -> tuple[asyncio.Event, asyncio.Event]:
    """Hold the next real refresh open while it owns the identity lock.

    The pause is injected at the client boundary the refresh already reads
    through, so what waits is the real `sync_with_backend` holding the real
    lock. Nothing here assigns the private refresh flags: a test that sets them
    by hand would prove only what it staged. `reissue` substitutes the
    identifier on every record read afterwards, which is what a replacement
    looks like to this worker.
    """
    real = ServiceProcessWorker._read_worker_records
    holding = asyncio.Event()
    release = asyncio.Event()

    async def _read(self: ServiceProcessWorker = worker) -> list[Any]:
        if not holding.is_set():
            holding.set()
            await release.wait()
        records = cast("list[Any]", await real(self))
        if reissue is None:
            return records
        return [record.model_copy(update={"id": reissue}) for record in records]

    worker._read_worker_records = cast("Any", _read)  # type: ignore[method-assign]
    return holding, release


class _StubRunner:
    """Stand in for the child process, and count the starts that were reached."""

    def __init__(self) -> None:
        self.starts: list[dict[str, str | None]] = []

    async def execute_flow_run(self, **kwargs: Any) -> SimpleNamespace:  # noqa: ANN401 - pinned Prefect runner shape.
        self.starts.append(kwargs["env"])
        kwargs["task_status"].started(42)
        return SimpleNamespace(returncode=0, pid=42)


def stub_child_start(worker: ServiceProcessWorker) -> _StubRunner:
    """Keep the child unspawned, so a start is observable without running one."""
    runner = _StubRunner()
    worker._runner = cast("Any", runner)
    return runner


def a_flow_run() -> Any:  # noqa: ANN401 - Prefect reads only the identifier here.
    """A flow run that carries what `ProcessWorker.run` actually reads."""
    return SimpleNamespace(id=uuid4(), name="service-run", deployment_id=None, job_variables={})


async def waiting_submission(
    worker: ServiceProcessWorker,
    configuration: ServiceProcessJobConfiguration,
) -> asyncio.Task[Any]:
    """Start a submission and return once it has reached the identity lock.

    The event is set immediately before `run` is entered, so when this returns
    the submission has run to its first suspension point. With the lock held by
    a paused refresh that point is the acquire, which is what makes
    `task.done()` a real question rather than a race: a submission that refused
    without waiting has already finished by now.
    """
    entered = asyncio.Event()

    async def _submit() -> Any:  # noqa: ANN401 - mirrors `run`'s Prefect result type.
        entered.set()
        return await worker.run(a_flow_run(), configuration)

    task = asyncio.create_task(_submit())
    await entered.wait()
    return task


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

        async def _blip(self: ServiceProcessWorker = worker) -> list[object]:  # noqa: RUF029 -- awaited hook
            del self
            raise httpx.ConnectError(BLIP)

        worker._read_worker_records = cast("Any", _blip)  # type: ignore[method-assign]

        await critical_service_loop(workload=worker.sync_with_backend, interval=0, run_once=True)

        assert worker.backend_id is None, "an unresolved identity was left installed after a blip"


async def test_a_deferred_identity_leaves_the_worker_unready(worker: ServiceProcessWorker) -> None:
    """Surviving an unresolved read must not be reported as a successful sync.

    Readiness is a second, independent gate on submission. Deferring without
    clearing it would leave the worker announcing that it is ready to take work
    it has no identity to run.
    """
    async with worker:
        await worker.sync_with_backend()
        await drop_the_pool(worker)

        await worker.sync_with_backend()

        assert worker.backend_id is None
        assert not worker._has_successfully_synced, "an unresolved sync reported readiness"


async def test_submission_is_refused_while_the_identity_is_unset(worker: ServiceProcessWorker) -> None:
    """The identity gate stands on its own, not on readiness happening to be false.

    Asserted with readiness forced true so the two gates are separated: without
    this, removing the identity check would still look correct because the
    readiness check happens to cover the same case.
    """
    async with worker:
        await worker.sync_with_backend()
        reached = watch_polling(worker)
        worker.backend_id = None
        worker._has_successfully_synced = True

        assert worker._submission_generation() is None, "an unset identity yielded a submission generation"
        assert await worker.get_and_submit_flow_runs() == []
        assert reached == [], "the worker polled with no identity installed"


async def test_the_generation_moves_when_the_identity_really_changes(worker: ServiceProcessWorker) -> None:
    """Restoring the generation is only correct for a record that did not change.

    Checked on the generation itself rather than through a refusal, because the
    prepared worker id would refuse a changed identity anyway and would hide a
    generation that had stopped moving.
    """
    async with worker:
        await worker.sync_with_backend()
        before = worker._identity_generation

        reissued = uuid4()
        real = ServiceProcessWorker._read_worker_records

        async def _rebound(self: ServiceProcessWorker = worker) -> list[Any]:
            records = await real(self)
            return [record.model_copy(update={"id": reissued}) for record in records]

        worker._read_worker_records = cast("Any", _rebound)  # type: ignore[method-assign]
        await worker.sync_with_backend()

        assert worker.backend_id == reissued
        assert worker._identity_generation != before, "a changed identity left the generation untouched"


async def test_a_child_prepared_outside_a_tracked_submission_is_refused(worker: ServiceProcessWorker) -> None:
    """A configuration nobody stamped is not a configuration this worker prepared.

    Its worker id can be perfectly current, so the generation is the only thing
    that distinguishes it from one this worker built during a poll it was
    tracking.
    """
    async with worker:
        await worker.sync_with_backend()
        untracked = worker.job_configuration()
        untracked._identity_generation = None
        untracked.env = {"PREFECT__WORKER_ID": str(worker.backend_id)}

        assert not accepted(worker, untracked), "a child prepared outside a tracked submission was accepted"


async def test_a_child_carrying_another_identity_is_refused(worker: ServiceProcessWorker) -> None:
    """The environment the child would actually run under has to name this worker.

    Stamped with the current generation on purpose: the generation alone would
    accept it, so what is pinned here is the check on the identifier the child
    receives.
    """
    async with worker:
        await worker.sync_with_backend()
        foreign = worker.job_configuration()
        foreign._identity_generation = worker._identity_generation
        foreign.env = {"PREFECT__WORKER_ID": str(uuid4())}

        assert not accepted(worker, foreign), "a child carrying another worker's identity was accepted"


async def test_a_submission_that_meets_a_refresh_waits_through_it_and_starts(
    worker: ServiceProcessWorker,
) -> None:
    """The repair. A submission valid before and after a refresh is valid during it.

    The recorded failure is a refusal with no wait and no revalidation: Prefect
    has already proposed `Submitting` by the time `run` is reached, so refusing
    inside a window that changes nothing marks the run `Crashed` for a
    replacement that never happened. `_submission_generation` already defers for
    the identical condition, so the two policies disagreed and this is the one
    that has to give.

    `task.done()` before the release is the whole assertion: a submission that
    refused instead of waiting has already finished at that point.
    """
    async with worker:
        await worker.sync_with_backend()
        runner = stub_child_start(worker)
        configuration = prepared(worker)
        first = worker.backend_id

        before = await worker.run(a_flow_run(), prepared(worker))

        holding, release = pause_the_next_refresh(worker)
        refresh = asyncio.create_task(worker.sync_with_backend())
        await holding.wait()
        during = await waiting_submission(worker, configuration)

        assert not during.done(), "the submission refused inside the refresh instead of waiting for it"
        assert len(runner.starts) == 1, "a child started while the refresh held the identity lock"

        release.set()
        result = await during
        await refresh

        after = await worker.run(a_flow_run(), prepared(worker))

        assert worker.backend_id == first, "the refresh resolved a different record, so this proves nothing"
        assert before.status_code == 0
        assert result.status_code == 0, "the submission that waited did not start its child"
        assert after.status_code == 0
        assert len(runner.starts) == 3, "before, during and after did not each start exactly one child"


async def test_a_replacement_during_the_wait_still_refuses_the_stale_submission(
    worker: ServiceProcessWorker,
) -> None:
    """Waiting must not become forgiving. What waited is revalidated, not admitted.

    The submission here waits through exactly the same window as the case
    above; the only difference is that the record really is reissued, so the
    generation the configuration was stamped with no longer matches and the
    worker id it would hand the child names an identity this worker no longer
    holds. Either surviving check is enough on its own.
    """
    async with worker:
        await worker.sync_with_backend()
        runner = stub_child_start(worker)
        stale = prepared(worker)
        first = worker.backend_id
        reissued = uuid4()

        holding, release = pause_the_next_refresh(worker, reissue=reissued)
        refresh = asyncio.create_task(worker.sync_with_backend())
        await holding.wait()
        submission = await waiting_submission(worker, stale)

        assert not submission.done(), "the submission did not wait for the refresh"

        release.set()
        with pytest.raises(ServiceWorkerIdentityError):
            await submission
        await refresh

        assert first is not None
        assert worker.backend_id == reissued, "the reissued identifier was not installed"
        assert worker.backend_id != first, "the identity did not change, so this proves nothing"
        assert runner.starts == [], "a child was started for a superseded identity"


async def test_refreshes_queued_behind_the_wait_do_not_admit_a_stale_submission(
    worker: ServiceProcessWorker,
) -> None:
    """Several heartbeats can stack up behind one wait; the last one still decides.

    A queued refresh is counted before it owns the lock, so the counter says
    only that a heartbeat is pending. That is why it cannot be a validity term.
    What has to hold instead is that every queued refresh completes before the
    waiting submission is validated, so the identity it is checked against is
    the one actually installed at the end.
    """
    async with worker:
        await worker.sync_with_backend()
        runner = stub_child_start(worker)
        stale = prepared(worker)
        reissued = uuid4()

        holding, release = pause_the_next_refresh(worker, reissue=reissued)
        first = asyncio.create_task(worker.sync_with_backend())
        await holding.wait()
        queued = [asyncio.create_task(worker.sync_with_backend()) for _ in range(2)]
        submission = await waiting_submission(worker, stale)

        release.set()
        with pytest.raises(ServiceWorkerIdentityError):
            await submission
        await asyncio.gather(first, *queued)

        assert worker.backend_id == reissued
        assert runner.starts == [], "a stale configuration was admitted while refreshes were queued"


async def test_a_queued_refresh_alone_does_not_refuse_a_current_submission(
    worker: ServiceProcessWorker,
) -> None:
    """A pending heartbeat is not a changed identity, and must not read as one.

    This is the defect in its smallest form. The refreshes here all re-resolve
    the same record, so nothing about the identity moves; only the pending
    count does.
    """
    async with worker:
        await worker.sync_with_backend()
        runner = stub_child_start(worker)
        configuration = prepared(worker)
        first = worker.backend_id

        holding, release = pause_the_next_refresh(worker)
        held = asyncio.create_task(worker.sync_with_backend())
        await holding.wait()
        queued = [asyncio.create_task(worker.sync_with_backend()) for _ in range(2)]
        submission = await waiting_submission(worker, configuration)

        release.set()
        result = await submission
        await asyncio.gather(held, *queued)

        assert worker.backend_id == first, "the heartbeats resolved a different record"
        assert result.status_code == 0
        assert len(runner.starts) == 1, "the submission was refused for a heartbeat that changed nothing"


async def test_a_submission_cancelled_while_waiting_leaves_no_lease_held(
    worker: ServiceProcessWorker,
) -> None:
    """Waiting introduces a cancellation point, so the lock must survive one.

    Prefect cancels submissions on shutdown. A cancelled wait that left the
    identity lock held would deadlock every later submission and every later
    heartbeat, which is a worse failure than the one being repaired. Asserted on
    the lock and on a later submission rather than on the lease, so it is the
    observable state that is pinned.
    """
    async with worker:
        await worker.sync_with_backend()
        runner = stub_child_start(worker)

        holding, release = pause_the_next_refresh(worker)
        refresh = asyncio.create_task(worker.sync_with_backend())
        await holding.wait()
        submission = await waiting_submission(worker, prepared(worker))
        submission.cancel()

        release.set()
        await refresh
        with pytest.raises(asyncio.CancelledError):
            await submission

        assert not worker._identity_lock.locked(), "a cancelled wait left the identity lock held"
        assert runner.starts == [], "a cancelled submission started a child"

        result = await worker.run(a_flow_run(), prepared(worker))

        assert result.status_code == 0, "a later submission could not proceed after the cancelled wait"
        assert len(runner.starts) == 1


async def test_no_child_is_started_before_the_identity_is_validated(
    worker: ServiceProcessWorker,
) -> None:
    """Validation is a gate on starting a process, not a report made afterwards.

    Stated as an event order rather than a return value: a refused submission
    must leave the runner untouched, and the same runner must record exactly one
    start once a configuration the worker really did prepare is submitted.
    """
    async with worker:
        await worker.sync_with_backend()
        runner = stub_child_start(worker)
        foreign = worker.job_configuration()
        foreign._identity_generation = worker._identity_generation
        foreign.env = {"PREFECT__WORKER_ID": str(uuid4())}

        with pytest.raises(ServiceWorkerIdentityError):
            await worker.run(a_flow_run(), foreign)

        assert runner.starts == [], "a child process was started for a configuration that was refused"

        result = await worker.run(a_flow_run(), prepared(worker))

        assert result.status_code == 0
        assert len(runner.starts) == 1, "the accepted submission did not start exactly one child"
