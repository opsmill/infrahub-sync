"""A worker with work in hand is working, not missing.

Deployment-level readiness is about whether the worker is answering, so a run in
flight must not move it: an operator who saw a deployment reported unhealthy here
would restart a healthy one in the middle of its own work.

The precondition and the property are asserted apart, and this row has been wrong
in both directions for the same reason. Demanding `ready` would fail a deployment
that is working; accepting `ready` passes one whose worker never took the run at
all, because `plan` returns on acceptance and says nothing about what claimed it.
So the worker is first established as busy, and only then is the deployment's own
account of itself read.
"""

from __future__ import annotations

import time

from kit import deployment, follow, key, refuse, run_request

# A submitted run has to be claimed before the worker reports it, which crosses a
# queue and a poll. A bound rather than a wait: a run nothing ever claims is a
# failure of this row's setup with a name.
BUSY_TIMEOUT_SECONDS = 120.0
BUSY_POLL_SECONDS = 2.0

# What the deployment's own lifecycle command treats as a live worker, and
# therefore as READY. `busy` belonging to this set is the property.
LIVE = {"ready", "busy"}

with deployment() as client:
    accepted = client.plan(run_request(client, "plan", "clean-host: keep a worker busy"), key("busy"))

    # Precondition: a worker really took the run. Without it the assertion below
    # holds for a deployment whose worker sat idle the whole time.
    deadline = time.monotonic() + BUSY_TIMEOUT_SECONDS
    observed = client.get_status().worker.state
    while observed != "busy":
        if time.monotonic() >= deadline:
            refuse(
                f"no worker reported busy within {BUSY_TIMEOUT_SECONDS:.0f}s of a submitted run"
                f" -- the last state was {observed!r}, so nothing here was observed about a busy one"
            )
        time.sleep(BUSY_POLL_SECONDS)
        observed = client.get_status().worker.state

    # Property: with that worker busy, the deployment still reports a live worker
    # and knows how many it has. `no-live-worker` is what an operator would act on.
    status = client.get_status()
    if status.worker.state not in LIVE:
        refuse(f"a worker with a run in hand left the deployment reporting {status.worker.state!r}")
    if not status.worker.detail_available or not status.worker.live_workers:
        refuse("the deployment reported no live worker detail while one of its workers was busy")

    follow(client, accepted)
