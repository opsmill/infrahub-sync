"""A worker with work in hand is working, not missing.

Deployment-level readiness is about whether the worker is answering, so a run in
flight must not move it: an operator who saw a deployment reported unhealthy here
would restart a healthy one in the middle of its own work.

The precondition and the property are asserted apart, and this row has been wrong
in both directions for the same reason. Demanding `ready` would fail a deployment
that is working; accepting `ready` passes one whose worker never took the run at
all, because `plan` returns on acceptance and says nothing about what claimed it.

What the deployment calls `busy` is a positive scheduled queue depth --
`service.py` derives the state as `"busy" if snapshot.queue_depth > 0` over the
runs Prefect still reports as scheduled -- and not a worker mid-execution. So one
submitted run is off that queue as soon as the single worker claims it, and a row
that submits one and polls for `busy` is polling for a sub-second interval.

Two runs submitted together do not fix that on their own: both can sit scheduled
with nothing executing, and `live_workers` would not say otherwise, because it
counts heartbeats rather than work. What separates a queue behind a working
deployment from a queue behind an idle one is the claim the flow records for
itself. `claim_execution` is called from inside the worker's own process, before
any configuration or adapter work begins, so a recorded `claimed_at` is a worker
running that run.

The order is therefore the evidence. One run is submitted and proven claimed and
unfinished; only then is a second submitted, which has nowhere to go but the
queue; and the deployment's own account of itself is read while the first is
re-proven still executing and the second re-proven still waiting.

Both halves of the queue reading come from one snapshot. A `queue_depth` taken
before the claim and a `live_workers` taken after it describe two different
moments, and no single moment they jointly describe need ever have existed.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from kit import deployment, follow, key, refuse, run_request

if TYPE_CHECKING:
    from infrahub_sync.client import SyncClient
    from infrahub_sync.client.models import OrchestrationSummary, RunResource, WorkerStatusResource

# A claim crosses the worker's own scheduled-run poll, which the deployment sets
# with `PREFECT_WORKER_QUERY_SECONDS`, and then a process start. A bound rather
# than a wait: a run nothing ever claims is a failure of this row's setup.
CLAIM_TIMEOUT_SECONDS = 180.0
CLAIM_POLL_SECONDS = 2.0

# The second run is queued from the moment it is accepted, and stays queued until
# the same poll comes round again. Bounded for the same reason.
QUEUE_TIMEOUT_SECONDS = 120.0
QUEUE_POLL_SECONDS = 2.0

# What the deployment's own lifecycle command treats as a live worker, and
# therefore as READY. `busy` belonging to this set is the property.
LIVE = {"ready", "busy"}


def attempt_of(client: SyncClient, accepted: RunResource) -> OrchestrationSummary:
    """Return the newest orchestration attempt the deployment records for one run.

    Newest by the attempt number the record carries, rather than by position: a
    reconciled or resubmitted run has more than one, and the older ones describe
    an execution that is over.
    """
    recorded = client.get_run(accepted.run.run_id).orchestration
    if not recorded:
        refuse(f"the deployment records no orchestration attempt for run {accepted.run.run_id}")
    return max(recorded, key=lambda attempt: attempt.attempt)


def executing(attempt: OrchestrationSummary) -> bool:
    """Answer whether a worker holds one attempt and has not finished it."""
    return attempt.claimed_at is not None and attempt.terminal_at is None


def await_execution(client: SyncClient, accepted: RunResource) -> None:
    """Block until a worker is executing one accepted run.

    A precondition, not the property: expiring here says no worker ever took the
    run, which is a statement about this row's setup rather than about what a
    deployment reports while its worker works.
    """
    deadline = time.monotonic() + CLAIM_TIMEOUT_SECONDS
    while True:
        attempt = attempt_of(client, accepted)
        if executing(attempt):
            return
        if attempt.terminal_at is not None:
            refuse(
                f"the first run ended {attempt.terminal_state}/{attempt.terminal_outcome} before a second"
                " was submitted, so nothing was ever queued behind a worker that was executing"
            )
        if time.monotonic() >= deadline:
            refuse(
                f"no worker claimed the first run within {CLAIM_TIMEOUT_SECONDS:.0f}s, so this row has"
                " no executing worker to queue anything behind"
            )
        time.sleep(CLAIM_POLL_SECONDS)


def await_queued_work(client: SyncClient, running: RunResource, waiting: RunResource) -> WorkerStatusResource:
    """Return the one snapshot reporting queued work behind a still-executing run.

    The two re-proofs are taken at the reading, not before it: a first run that
    finished, or a second that was claimed, in the interval before the snapshot
    would leave a queue depth this row had nothing to do with.
    """
    deadline = time.monotonic() + QUEUE_TIMEOUT_SECONDS
    while True:
        worker = client.get_status().worker
        if (
            worker.detail_available
            and worker.queue_depth is not None
            and worker.live_workers is not None
            and worker.queue_depth >= 1
            and worker.live_workers >= 1
        ):
            if not executing(attempt_of(client, running)):
                refuse("the first run stopped executing as the queue was read, so nothing was queued behind work")
            if attempt_of(client, waiting).claimed_at is not None:
                refuse("the second run was claimed as the queue was read, so neither of this row's runs was waiting")
            return worker
        if time.monotonic() >= deadline:
            refuse(
                f"no snapshot within {QUEUE_TIMEOUT_SECONDS:.0f}s reported queued work and a live worker together"
                f" -- the last one reported {worker.state!r}, so nothing here was observed about a working deployment"
            )
        time.sleep(QUEUE_POLL_SECONDS)


with deployment() as client:
    # Two runs, and two mutation keys: one key submitted twice is a replay of the
    # first run, which would leave the queue exactly as short as one submission.
    request = run_request(client, "plan", "clean-host: keep a worker busy")

    # Precondition, first half: a worker really is executing this run. Submitting
    # both at once would leave the queue satisfiable by two runs nothing claimed.
    claimed = client.plan(request, key("busy-claimed"))
    await_execution(client, claimed)

    # Second half: a run behind the one being executed. With a single worker
    # already occupied, this is what the deployment's queue depth is counting.
    queued = client.plan(request, key("busy-queued"))
    worker = await_queued_work(client, claimed, queued)

    # Property: with that work in hand, the deployment still reports a live
    # worker. `no-live-worker` is what an operator would act on.
    if worker.state not in LIVE:
        refuse(f"a worker with a run in hand left the deployment reporting {worker.state!r}")

    # Settled here, so this row hands the rows after it a deployment with nothing
    # in flight: the next one waits for READY, which a queue of this row's making
    # would hold open.
    follow(client, claimed)
    follow(client, queued)
