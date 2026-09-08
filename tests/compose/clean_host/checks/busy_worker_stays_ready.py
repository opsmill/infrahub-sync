"""A worker with work in hand is working, not missing.

Deployment-level readiness is about whether the worker is answering, so a run in
flight must not move it: an operator who saw a deployment reported unhealthy here
would restart a healthy one in the middle of its own work.

What the deployment calls `busy` is a positive scheduled queue depth --
`service.py` derives the state as `"busy" if snapshot.queue_depth > 0` over the
runs Prefect still reports as scheduled -- and not a worker mid-execution. The
shipped bundle runs one worker with no concurrency limit, so it claims every run
it sees on its next poll. Both halves of "a run executing with another queued
behind it" are therefore intervals, and an earlier version of this row lost that
race by two tenths of a second.

Neither half is sampled here. Both are held open, by this row, for exactly as
long as it needs them:

* The first run is a real `sync`, and this row takes the deployment's own
  configuration write guard before submitting it. `flow.py` claims the execution
  (`_claim_current_execution`) and only then enters `_configuration_write_guard`,
  so the run is claimed and then blocks on a key this row holds. It is executing
  because nothing lets it finish, not because the workload is slow. The guard is
  taken through the product's own `hold_apply_guard`, so the key contended on is
  the same one the flow derives rather than a copy that could drift.

* The second run stays scheduled because the driver stops the worker's parent
  process while this row waits. `docker kill --signal STOP` reaches PID 1 only;
  the flow runs as a separate child process, so the first run keeps running and
  keeps its claim while the parent can no longer claim anything.

The two sides meet through a control directory this row alone is given. Every
handshake waits for a state that stays true once it is true, so no step samples
for a moment it could miss.

The `sync` writes. Row 5 plants a difference and reverts only the schema it
drifted, so the two sides still differ when this row runs, and this run converges
exactly that one update. The count is asserted rather than tolerated: an
unasserted write is the thing this gate exists to catch.
"""

from __future__ import annotations

import pathlib
import time
from contextlib import suppress
from typing import TYPE_CHECKING

from kit import ACTIONS, bundled, create_run, deployment, follow, key, refuse

from infrahub_sync.service.apply_guard import hold_apply_guard
from infrahub_sync.service.storage import service_guard_secrets, service_guard_session

if TYPE_CHECKING:
    from infrahub_sync.client import SyncClient
    from infrahub_sync.client.models import OrchestrationSummary, PublicRunResource, RunResource, WorkerStatusResource

# The only writable path this check is given. The driver creates it for this row
# and removes it with the row; no other check receives one.
CONTROL = pathlib.Path("/control")

# Each wait is for a file that stays written once written, so these bound a
# partner that has died rather than an interval that might be missed.
#
# Every one of them runs while the first run is blocked on the key this row
# holds, and three product clocks are already running from the moment that run
# was claimed: the guard's own `lock_timeout` (`apply_guard.py`
# DEFAULT_DEADLINE_SECONDS = 30), the liveness stall threshold
# (`max(3 * worker query, 30)` = 30), and the live-worker freshness window
# (`max(3 * heartbeat, 30)` = 30). Each one ends the state this row exists to
# observe, and the first of them ends it by failing the run. So this bound is not
# a comfort margin -- it has to expire, release the key and let the driver resume
# the parent with time to spare, which is why it is well under thirty rather than
# near it. Nothing product-side is tuned to accommodate it.
HELD_TIMEOUT_SECONDS = 20.0
HELD_POLL_SECONDS = 1.0

# A claim crosses the worker's scheduled-run poll and a process start.
CLAIM_TIMEOUT_SECONDS = 180.0
CLAIM_POLL_SECONDS = 2.0

# What the deployment's own lifecycle command treats as a live worker, and
# therefore as READY. `busy` belonging to this set is the property.
LIVE = {"ready", "busy"}

# What row 5 left behind: one planted attribute value on the source branch, whose
# schema it reverted and whose value it did not.
EXPECTED_UPDATES = 1


def signal_driver(name: str) -> None:
    """Record one state for the driver, which is watching for exactly this name."""
    (CONTROL / name).write_text("", encoding="utf-8")


def await_driver(name: str, sentence: str) -> None:
    """Block until the driver records one state, or refuse with what did not happen.

    Expiring here is a statement about the driver, not about the deployment, and
    it has to happen early enough that the key is released and the worker parent
    resumed before the product's own thirty-second clocks reach the first run.
    """
    deadline = time.monotonic() + HELD_TIMEOUT_SECONDS
    while not (CONTROL / name).exists():
        if time.monotonic() >= deadline:
            refuse(f"{sentence} within {HELD_TIMEOUT_SECONDS:.0f}s, so this row released the key and gave up")
        time.sleep(HELD_POLL_SECONDS)


def attempt_of(client: SyncClient, accepted: RunResource) -> OrchestrationSummary:
    """Return the newest orchestration attempt the deployment records for one run."""
    recorded = client.get_run(accepted.run.run_id).orchestration
    if not recorded:
        refuse(f"the deployment records no orchestration attempt for run {accepted.run.run_id}")
    return max(recorded, key=lambda attempt: attempt.attempt)


def claimed_and_running(attempt: OrchestrationSummary) -> bool:
    """Answer whether a worker holds one attempt and has not finished it."""
    return attempt.claimed_at is not None and attempt.terminal_at is None


def await_execution(client: SyncClient, accepted: RunResource) -> None:
    """Block until a worker is executing one accepted run.

    Bounded, but not a race: the guard this row holds is what the run blocks on,
    so once the state is reached it lasts until this row gives the key up.
    """
    deadline = time.monotonic() + CLAIM_TIMEOUT_SECONDS
    while True:
        attempt = attempt_of(client, accepted)
        if claimed_and_running(attempt):
            return
        if attempt.terminal_at is not None:
            refuse(
                f"the first run ended {attempt.terminal_state}/{attempt.terminal_outcome} without ever blocking"
                " on the write guard this row holds, so nothing was executing to queue anything behind"
            )
        if time.monotonic() >= deadline:
            refuse(
                f"no worker claimed the first run within {CLAIM_TIMEOUT_SECONDS:.0f}s, so this row has"
                " no executing worker to queue anything behind"
            )
        time.sleep(CLAIM_POLL_SECONDS)


def await_qualifying_snapshot(client: SyncClient, running: RunResource, waiting: RunResource) -> WorkerStatusResource:
    """Return the one snapshot proving queued work behind a still-executing run.

    Both re-proofs are taken at the qualifying reading rather than before it: a
    first run that ended, or a second that was claimed, in the interval before the
    snapshot would leave a queue depth this row had nothing to do with.
    """
    deadline = time.monotonic() + HELD_TIMEOUT_SECONDS
    while True:
        worker = client.get_status().worker
        if (
            worker.detail_available
            and worker.queue_depth is not None
            and worker.live_workers is not None
            and worker.queue_depth >= 1
            and worker.live_workers >= 1
        ):
            if not claimed_and_running(attempt_of(client, running)):
                refuse("the first run stopped executing as the queue was read, so nothing was queued behind work")
            if attempt_of(client, waiting).claimed_at is not None:
                refuse("the second run was claimed as the queue was read, so neither of this row's runs was waiting")
            return worker
        if time.monotonic() >= deadline:
            refuse(
                f"no snapshot within {HELD_TIMEOUT_SECONDS:.0f}s reported queued work and a live worker"
                f" together -- the last one reported {worker.state!r}, so nothing was observed about a working"
                " deployment"
            )
        time.sleep(HELD_POLL_SECONDS)


def require_converged_drift(settled: PublicRunResource) -> None:
    """Refuse a first run that wrote anything other than the drift row 5 left.

    Stated rather than bounded below. This row is the transition that converges
    the difference row 5 planted, and a run that wrote more than that is writing
    something nobody planted -- which a qualification gate should catch.
    """
    written = {action: int(settled.summary.get(action, 0)) for action in ACTIONS}
    if written != {"create": 0, "update": EXPECTED_UPDATES, "delete": 0}:
        refuse(f"the first sync wrote {written}, not the single update row 5 left for it to converge")


try:
    with deployment() as client:
        config_id, registry_version = bundled(client)
        write = create_run("sync", config_id=config_id, registry_version=registry_version, reason="clean-host: busy")
        read = create_run("plan", config_id=config_id, registry_version=registry_version, reason="clean-host: queued")

        # The key the flow will contend on, taken through the product's own hold so
        # it is derived once. Released by the `with`, on the exception path too.
        with hold_apply_guard(config_id, connect=service_guard_session, secrets=service_guard_secrets()):
            executing_run = client.sync(write, key("busy-executing"))
            await_execution(client, executing_run)

            # Only now: the driver stops the worker's parent so the next run has
            # nowhere to go but the queue. Announced after the proof, not before.
            signal_driver("executing")
            await_driver("stopped", "the driver never reported stopping the worker parent")

            queued_run = client.plan(read, key("busy-queued"))
            worker = await_qualifying_snapshot(client, executing_run, queued_run)

            # Property: with that work in hand, the deployment still reports a live
            # worker. `no-live-worker` is what an operator would act on.
            if worker.state not in LIVE:
                refuse(f"a worker with a run in hand left the deployment reporting {worker.state!r}")

            signal_driver("observed")
            await_driver("resumed", "the driver never reported resuming the worker parent")

        # Outside the hold, because both runs need the key this row was holding.
        require_converged_drift(follow(client, executing_run).run)
        follow(client, queued_run)
finally:
    # Said on every path, including a refusal: a driver still waiting for a state
    # this check will never reach would wait out its whole bound before resuming
    # a worker it stopped.
    #
    # Suppressed on failure, and only here. A control directory that cannot be
    # written is not this row's verdict to give -- the refusal already raised is,
    # and an exception from this line would replace it with a filesystem error
    # about the harness. The driver's own bound is what covers the state never
    # arriving, which is exactly the case this cannot report.
    with suppress(OSError):
        signal_driver("done")
