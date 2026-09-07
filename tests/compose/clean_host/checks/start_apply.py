"""Start a confirmed write and return only once it has actually reached the write.

The recovery row interrupts a worker mid-apply. Interrupting one before it began
writing leaves a run that never wrote, nothing to reconcile, and a row that would
report success having tested nothing — so this plants a difference, proves in a
read-only run that there is an operation to interrupt, then blocks until the run
has moved past planning and prints its identifier for the driver to interrupt.
"""

from __future__ import annotations

import sys
import time

from kit import (
    POLL_SECONDS,
    RUN_TIMEOUT_SECONDS,
    deployment,
    follow,
    key,
    plant,
    refuse,
    require_planned_work,
    run_request,
)

with deployment() as client:
    # The rows before this one converged the two sides. An empty run reaches a
    # terminal state without ever writing, and interrupting it would leave nothing
    # ambiguous -- so this plants its own difference and proves, in a read-only run
    # of its own, that there is one operation to interrupt before it starts one.
    plant("interrupt")
    proved = follow(client, client.plan(run_request(client, "plan", "clean-host: work to interrupt"), key("probe")))
    require_planned_work(client, proved.run.run_id)

    accepted = client.sync(run_request(client, "sync", "clean-host: interrupt mid-apply"), key("interrupt"))
    run_id = accepted.run.run_id

    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        phase = client.get_run(run_id).run.phase
        if phase not in {"accepted", "planned"}:
            print(run_id)
            sys.exit(0)
        time.sleep(POLL_SECONDS)
    refuse("the run never moved past planning, so there was no write to interrupt")
