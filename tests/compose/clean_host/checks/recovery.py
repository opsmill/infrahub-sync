"""An interrupted write is not retried, and its durable state leads to a fresh plan.

The driver has already interrupted a worker that had reached its write. This reads
what the deployment kept: the run is terminal rather than re-queued, it carries
durable reconciliation state for an operator to inspect, and a new plan run after
it completes normally.

Nothing here retries the interrupted run. That is the property — a write whose
outcome is unknown is not repeated on the chance that it failed.
"""

from __future__ import annotations

import sys

from kit import deployment, follow, key, refuse, run_request

run_id = sys.argv[1]

with deployment() as client:
    interrupted = client.get_run(run_id).run

    # Precondition: the interruption actually landed on this run.
    if interrupted.phase not in {"interrupted", "failed"} and "failed" not in interrupted.phase:
        refuse(f"the interrupted run reports {interrupted.phase}, so no ambiguous write was induced")

    if not interrupted.reconciliation_required:
        refuse("an interrupted write left no durable reconciliation state for an operator to act on")

    fresh = follow(client, client.plan(run_request(client, "plan", "clean-host: plan after recovery"), key("after")))
    if "failed" in fresh.run.phase:
        refuse(f"a fresh plan after an interrupted write ended in {fresh.run.phase}")

    if client.get_run(run_id).run.phase != interrupted.phase:
        refuse("the interrupted run moved after it was recorded, so something retried it")
