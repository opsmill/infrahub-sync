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

from kit import UNKEYED_REFUSAL, deployment, follow, key, recorded_failure, refuse, run_request

run_id = sys.argv[1]

with deployment() as client:
    interrupted = client.get_run(run_id).run

    # Precondition: the interruption actually landed on this run.
    if interrupted.phase not in {"interrupted", "failed"} and "failed" not in interrupted.phase:
        refuse(f"the interrupted run reports {interrupted.phase}, so no ambiguous write was induced")

    if not interrupted.reconciliation_required:
        refuse("an interrupted write left no durable reconciliation state for an operator to act on")

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
