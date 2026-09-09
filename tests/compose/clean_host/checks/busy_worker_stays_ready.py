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
import signal
import time
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING

from kit import ACTIONS, bundled, create_run, deployment, follow, key, refuse

from infrahub_sync.client import SyncClientError
from infrahub_sync.service.apply_guard import hold_apply_guard
from infrahub_sync.service.storage import service_guard_secrets, service_guard_session

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import FrameType

    from infrahub_sync.client import SyncClient
    from infrahub_sync.client.models import (
        CreateRunRequest,
        OrchestrationSummary,
        PublicRunResource,
        RunResource,
        WorkerStatusResource,
    )

# The only writable path this check is given. The driver creates it for this row
# and removes it with the row; no other check receives one.
CONTROL = pathlib.Path("/control")

# One budget for everything after the claim, and it is a total rather than a
# per-step allowance.
#
# Three product clocks start when the first run is claimed and run once: the
# guard's own `lock_timeout` (`apply_guard.py` DEFAULT_DEADLINE_SECONDS = 30),
# the liveness stall threshold (`max(3 * worker query, 30)` = 30), and the
# live-worker freshness window (`max(3 * heartbeat, 30)` = 30). Each ends the
# state this row exists to observe, and the first ends it by failing the run. A
# row whose steps each restarted a deadline could obey every individual bound and
# still outlive all three, and would then report a stalled or contended run
# instead of the coordination failure that actually happened.
#
# A real interval timer rather than a checked deadline, because most of this time
# is spent inside blocking client calls: a loop condition is not re-examined
# while `httpx` waits on a socket, and `SIGALRM` interrupts it. Nothing
# product-side is tuned to accommodate any of this.
POST_CLAIM_BUDGET_SECONDS = 15.0
HELD_POLL_SECONDS = 1.0

# Settlement runs after the key is released and the parent resumed, so it is
# outside the budget above and bounded on its own.
SETTLE_TIMEOUT_SECONDS = 90.0
SETTLE_POLL_SECONDS = 3.0

# A claim crosses the worker's scheduled-run poll and a process start.
CLAIM_TIMEOUT_SECONDS = 180.0
CLAIM_POLL_SECONDS = 2.0

# Whether a worker able to claim at all exists, asked before the claim is waited
# for and bounded separately from it. A worker whose exact pool identity the
# service cannot resolve does not poll -- `worker.py` skips submission entirely
# while it is unavailable -- so a row that only waited for the claim would spend
# that whole budget on a deployment which was never going to take its run, and
# then refuse in the words of the queue property. Two bounds, two sentences.
CLAIMABLE_TIMEOUT_SECONDS = 60.0

# What the deployment's own lifecycle command treats as a live worker, and
# therefore as READY. `busy` belonging to this set is the property.
LIVE = {"ready", "busy"}

# What row 5 left behind: one planted attribute value on the source branch, whose
# schema it reverted and whose value it did not.
EXPECTED_UPDATES = 1

# The two runs this row accepts. Counted, because a submission interrupted
# between the server accepting it and this row recording the handle would
# otherwise leave a run nothing settles.
EXPECTED_ACCEPTED = 2

# The purpose the second run's mutation key is derived from. Named once, because
# recovering an interrupted acceptance means replaying that exact key: a
# different one would create another run rather than return the accepted one.
QUEUED_PURPOSE = "busy-queued"


class BudgetExpiredError(Exception):
    """The one budget this row has after its first run was claimed has run out."""


@contextmanager
def post_claim_budget() -> Iterator[None]:
    """Bound everything inside to one total, interrupting a blocking call if need be.

    `signal.setitimer` and `SIGALRM` are POSIX and this check runs on the main
    thread of the candidate's Linux image, which is where Python delivers
    signals -- so the alarm reaches a thread blocked in `httpx` as readily as one
    in `time.sleep`.

    Both the handler and the timer are process state, so both are put back. A row
    that left either behind would arm an alarm over whatever ran next.
    """

    def expire(_signum: int, _frame: FrameType | None) -> None:
        raise BudgetExpiredError

    previous_handler = signal.signal(signal.SIGALRM, expire)
    previous_delay, previous_interval = signal.setitimer(signal.ITIMER_REAL, POST_CLAIM_BUDGET_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, previous_delay, previous_interval)
        signal.signal(signal.SIGALRM, previous_handler)


def settle_accepted(client: SyncClient, handles: list[RunResource]) -> None:
    """Give every run this row had accepted a bounded chance to reach a verdict.

    Reached on every path, because the rows after this one wait for READY and a
    run of this row's making holds it open. It runs after the key is released and
    after the driver has been asked to resume, so the runs can actually finish.

    Nothing here reports anything. A refusal is already on its way out when this
    runs on the failure path, and a settlement that failed says nothing about the
    property that failed -- so the client's own error taxonomy is suppressed and
    the original refusal is what leaves this check.
    """
    for handle in handles:
        with suppress(SyncClientError):
            client.wait_for_run(handle, timeout=SETTLE_TIMEOUT_SECONDS, poll_interval=SETTLE_POLL_SECONDS)


def recover_queued(client: SyncClient, request: CreateRunRequest, handles: list[RunResource]) -> None:
    """Recover a second run the server may have accepted before this row recorded it.

    The budget's alarm can land inside `client.plan` after the service has
    admitted the run and before the handle reaches `handles`, which would leave
    an accepted run nothing settles and a READY the next row waits on forever.

    Replaying the same mutation key is what recovers it: the key is the one the
    submission used, so the service answers with that run rather than admitting
    another. If nothing was ever accepted, the replay admits the run the
    interrupted call did not -- still one run for one key, never a third.
    """
    with suppress(SyncClientError):
        handles.append(client.plan(request, key(QUEUED_PURPOSE)))


def release_and_settle(
    client: SyncClient, request: CreateRunRequest, handles: list[RunResource], *, attempted: bool
) -> None:
    """Let the driver resume the parent, recover an ambiguous acceptance, then settle.

    In that order. Nothing this row accepted can reach a verdict while the
    worker's parent is stopped, and on the refusal path it still is -- the driver
    is waiting to hear from this check before it resumes. The key is already
    released by the time this runs, because the hold closed above it.

    `attempted` is what makes the recovery a recovery. A missing second handle
    means two different things: a submission that began and may have been
    admitted before this row could record it, or a row that failed long before it
    ever reached that submission. Only the first is ambiguous. Replaying on the
    second would admit a run during cleanup that the row never asked for, which
    is a worse fault than the leak it was meant to close.
    """
    with suppress(OSError):
        signal_driver("done")
    if attempted and len(handles) < EXPECTED_ACCEPTED:
        recover_queued(client, request, handles)
    settle_accepted(client, handles)


def signal_driver(name: str) -> None:
    """Record one state for the driver, which is watching for exactly this name."""
    (CONTROL / name).write_text("", encoding="utf-8")


def await_driver(name: str, sentence: str) -> None:
    """Block until the driver records one state, or refuse with what did not happen.

    Expiring here is a statement about the driver, not about the deployment. It
    has no deadline of its own: the one budget covering everything after the
    claim is what ends it, and it says so with this wait's own sentence.
    """
    try:
        while not (CONTROL / name).exists():
            time.sleep(HELD_POLL_SECONDS)
    except BudgetExpiredError:
        refuse(f"{sentence} within the {POST_CLAIM_BUDGET_SECONDS:.0f}s this row has after its first run was claimed")


def attempt_of(client: SyncClient, accepted: RunResource) -> OrchestrationSummary:
    """Return the newest orchestration attempt the deployment records for one run."""
    recorded = client.get_run(accepted.run.run_id).orchestration
    if not recorded:
        refuse(f"the deployment records no orchestration attempt for run {accepted.run.run_id}")
    return max(recorded, key=lambda attempt: attempt.attempt)


def claimed_and_running(attempt: OrchestrationSummary) -> bool:
    """Answer whether a worker holds one attempt and has not finished it."""
    return attempt.claimed_at is not None and attempt.terminal_at is None


def await_claimable_worker(client: SyncClient) -> None:
    """Block until the deployment reports a worker that could claim anything.

    A precondition of this row, not its property: expiring here says the
    deployment had no live worker to queue work behind, which is a statement
    about the setup rather than about what a working deployment reports. The
    claim below keeps its own bound, so a live worker that does not take the run
    is still that refusal and not this one.
    """
    deadline = time.monotonic() + CLAIMABLE_TIMEOUT_SECONDS
    reported = "nothing"
    while True:
        worker = client.get_status().worker
        reported = repr(worker.state)
        if worker.detail_available and worker.live_workers is not None and worker.live_workers >= 1:
            return
        if time.monotonic() >= deadline:
            refuse(
                f"the deployment reported no live worker within {CLAIMABLE_TIMEOUT_SECONDS:.0f}s of this row"
                f" starting -- the last reading was {reported} -- so nothing could have claimed its first run"
            )
        time.sleep(CLAIM_POLL_SECONDS)


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
    reported = "nothing"
    try:
        while True:
            worker = client.get_status().worker
            reported = repr(worker.state)
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
                    refuse(
                        "the second run was claimed as the queue was read, so neither of this row's runs was waiting"
                    )
                return worker
            time.sleep(HELD_POLL_SECONDS)
    except BudgetExpiredError:
        refuse(
            f"no snapshot within the {POST_CLAIM_BUDGET_SECONDS:.0f}s after the first run was claimed reported"
            f" queued work and a live worker together -- the last one reported {reported}, so nothing was"
            " observed about a working deployment"
        )


def require_converged_drift(settled: PublicRunResource) -> None:
    """Refuse a first run that wrote anything other than the drift row 5 left.

    Stated rather than bounded below. This row is the transition that converges
    the difference row 5 planted, and a run that wrote more than that is writing
    something nobody planted -- which a qualification gate should catch.
    """
    written = {action: int(settled.summary.get(action, 0)) for action in ACTIONS}
    if written != {"create": 0, "update": EXPECTED_UPDATES, "delete": 0}:
        refuse(f"the first sync wrote {written}, not the single update row 5 left for it to converge")


accepted: list[RunResource] = []
# Set immediately before the second submission, so a missing handle afterwards is
# an ambiguous acceptance rather than a row that never got that far.
queued_attempted = False

try:
    with deployment() as client:
        config_id, registry_version = bundled(client)
        # Before anything is submitted, and before the guard is held: this row
        # needs a worker that can claim, and asking first is what keeps a
        # deployment without one from being reported as a queue that failed.
        await_claimable_worker(client)
        write = create_run("sync", config_id=config_id, registry_version=registry_version, reason="clean-host: busy")
        read = create_run("plan", config_id=config_id, registry_version=registry_version, reason="clean-host: queued")

        try:
            # The key the flow will contend on, taken through the product's own hold
            # so it is derived once. Released by the `with`, on the exception path
            # too -- including the budget's own expiry, which unwinds through it.
            with hold_apply_guard(config_id, connect=service_guard_session, secrets=service_guard_secrets()):
                executing_run = client.sync(write, key("busy-executing"))
                accepted.append(executing_run)
                await_execution(client, executing_run)

                # One budget from here to the last handshake. Everything below it
                # happens while the product's own clocks are already running.
                #
                # Translated here as well as inside the waits. The alarm can land
                # anywhere -- in `client.plan`, in a reproof's `get_run`, in a
                # control write, in the property itself -- and each of those is a
                # blocking call no loop condition guards. Without this the row
                # would end in a raw traceback instead of a sentence. The waits
                # keep their own phrasing because they can say which step it was;
                # this says only that the row ran out of budget, which is what is
                # actually known anywhere else.
                try:
                    with post_claim_budget():
                        # The driver stops the worker's parent so the next run has
                        # nowhere to go but the queue. Announced after the proof of
                        # execution, not before it.
                        signal_driver("executing")
                        await_driver("stopped", "the driver never reported stopping the worker parent")

                        # Immediately before, never after: the window this closes
                        # is the one between the service admitting the run and
                        # this row recording the handle.
                        queued_attempted = True
                        queued_run = client.plan(read, key(QUEUED_PURPOSE))
                        accepted.append(queued_run)
                        worker = await_qualifying_snapshot(client, executing_run, queued_run)

                        # Property: with that work in hand, the deployment still reports
                        # a live worker. `no-live-worker` is what an operator acts on.
                        if worker.state not in LIVE:
                            refuse(f"a worker with a run in hand left the deployment reporting {worker.state!r}")

                        signal_driver("observed")
                        await_driver("resumed", "the driver never reported resuming the worker parent")
                except BudgetExpiredError:
                    refuse(
                        f"this row ran out of the {POST_CLAIM_BUDGET_SECONDS:.0f}s it has after its first run"
                        " was claimed, so what it was doing when the budget ended is all that is known"
                    )

            # Outside the hold, because both runs need the key this row was holding,
            # and outside the budget, because the alarm must not interrupt a run
            # that is finally allowed to finish. Strict on the success path: the
            # verdict is followed and the one write row 5 left behind is stated.
            require_converged_drift(follow(client, executing_run).run)
            follow(client, queued_run)
        except BaseException:
            # Something is already on its way out -- a refusal, a budget expiry,
            # an interrupt -- and it is this check's finding. Cleanup still has to
            # run, but nothing it raises may take that finding's place, so every
            # exception from it is swallowed here and only here. `settle_accepted`
            # suppresses the client's own taxonomy; this covers everything else it
            # or the recovery could raise. The original leaves unchanged.
            with suppress(Exception):
                release_and_settle(client, read, accepted, attempted=queued_attempted)
            raise
        else:
            # Nothing in flight, so cleanup speaks for itself: a settlement that
            # cannot complete on the success path is a finding of its own rather
            # than noise over someone else's.
            release_and_settle(client, read, accepted, attempted=queued_attempted)
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
