"""An interrupted write is not retried, and its durable state leads to a fresh plan.

The driver has already interrupted a worker that had reached its write. This reads
what the deployment kept: the run is terminal rather than re-queued, it carries
durable reconciliation state for an operator to inspect, and a new plan run after
it completes normally.

Nothing here retries the interrupted run. That is the property — a write whose
outcome is unknown is not repeated on the chance that it failed.

The reconciliation flag alone does not say a write was uncertain: it derives from
a dispatch having been proven, not from bytes having reached the destination, so
a refusal that sent nothing raises it as well. What separates the two is the
recorded cause, and this row requires the interruption not to be one.
"""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING

from kit import UNKEYED_REFUSAL, deployment, follow, key, recorded_failure, refuse, run_request

if TYPE_CHECKING:
    from infrahub_sync.client import SyncClient
    from infrahub_sync.client.models import PublicRunResource

run_id = sys.argv[1]

# What the service waits before it will call a claimed execution stalled:
# `max(3 * PREFECT_WORKER_QUERY_SECONDS, 30)` in `service/liveness.py`, with a
# reconciler cadence of half that. The killed worker records nothing itself --
# reconciliation is what terminalises its execution -- so this row cannot read a
# verdict that has not been reached yet. Bounded at several times the threshold
# so a slow host is waited for rather than reported as a product failure.
STALL_THRESHOLD_SECONDS = 30.0
RECONCILE_TIMEOUT_SECONDS = 8 * STALL_THRESHOLD_SECONDS
RECONCILE_POLL_SECONDS = 5.0


def await_reconciliation(client: SyncClient, identifier: str) -> PublicRunResource:
    """Return the run once it carries a terminal, reconciliation-flagged verdict.

    A precondition, not the property: expiring here says the interruption was
    never recorded, which is a statement about this row's setup rather than
    evidence about what the deployment does with an uncertain write.
    """
    deadline = time.monotonic() + RECONCILE_TIMEOUT_SECONDS
    while True:
        run = client.get_run(identifier).run
        if run.reconciliation_required:
            return run
        if time.monotonic() >= deadline:
            refuse(
                f"the interrupted run still reports {run.phase!r} after"
                f" {RECONCILE_TIMEOUT_SECONDS:.0f}s, so no interruption was ever recorded to read"
            )
        time.sleep(RECONCILE_POLL_SECONDS)


with deployment() as client:
    # Precondition: the interruption actually landed on this run, and was
    # recorded. Reading immediately would race the reconciliation that records it.
    interrupted = await_reconciliation(client, run_id)
    if interrupted.phase not in {"interrupted", "failed"} and "failed" not in interrupted.phase:
        refuse(f"the interrupted run reports {interrupted.phase}, so no ambiguous write was induced")

    # And it is not a refusal. Every apply or sync failure after the first
    # operation sets `reconciliation_required`, because the flag derives from a
    # dispatch having been proven rather than from bytes having been sent -- so an
    # operation the write surface refused before sending anything sets it too.
    # Without this the row passes on a run that provably wrote nothing, which is
    # the opposite of the state it exists to observe, and its property could never
    # fail for the reason it is about.
    if recorded_failure(client, run_id).get("cause_type") == UNKEYED_REFUSAL:
        refuse("the interrupted run was refused before its write, so there is nothing ambiguous about it")

    fresh = follow(client, client.plan(run_request(client, "plan", "clean-host: plan after recovery"), key("after")))
    if "failed" in fresh.run.phase:
        refuse(f"a fresh plan after an interrupted write ended in {fresh.run.phase}")

    if client.get_run(run_id).run.phase != interrupted.phase:
        refuse("the interrupted run moved after it was recorded, so something retried it")
