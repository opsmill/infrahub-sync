"""Arrange an actual destination write that is in flight, and prove it is.

The recovery row interrupts a worker mid-apply. What makes that mean anything is
that the destination has completed a write and the worker does not yet know the
outcome: a worker killed before the write leaves nothing ambiguous to reconcile,
and a worker killed after it learned the answer leaves nothing uncertain either.

Reading a run phase cannot find that moment. ``planned`` is left the instant the
run starts applying, which is before any byte reaches the destination -- so the
driver that killed a worker on that reading interrupted whatever the run happened
to be doing, and the row reported a property it never induced.

So the moment is held open instead of looked for. The deployment's registered
configuration names this gate's own proxy, and this check arms it for the next
GraphQL mutation. The proxy claims that mutation, forwards it to the real pinned
destination, requires the destination to have completed it, records that durably,
and then withholds the answer from the worker.

This check then reads the *destination itself* -- not the proxy, not the
deployment -- and requires the value it planted to be present on the branch the
plan writes to. Only then does it name the run, because naming the run is the
driver's licence to kill a worker.

One absolute budget covers everything from the instant the proxy accepted the
mutation. It is the proxy's, because the proxy is the only party that knows that
instant, and this check reads its remaining share of it from what the proxy
recorded.
"""

from __future__ import annotations

import signal
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from destination_proxy import (
    ACCEPTED_AT,
    ARM,
    BUDGET_SENTENCE,
    PROXY_BUDGET_SECONDS,
    UPSTREAM_COMPLETED,
    UPSTREAM_FAILED,
    held,
    recorded,
    signal_proxy,
)
from kit import (
    PLANTED_OPERATIONS,
    SEEDED_DEVICE,
    SEEDED_KIND,
    deployment,
    follow,
    key,
    planned_branch,
    plant,
    planted_attribute,
    refuse,
    require_planned_work,
    run_request,
    sdk,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import FrameType

    from infrahub_sync.client import SyncClient

# Waiting for the proxy to accept a mutation happens before the one budget starts,
# and before any of the product's clocks that matter are running: the run has to
# be admitted, claimed, planned, and reach its first write. So it is generous, and
# it is a bound rather than a wait -- a run that never wrote is a finding.
ACCEPT_TIMEOUT_SECONDS = 300.0
ACCEPT_POLL_SECONDS = 1.0

# Inside the budget nothing has a deadline of its own. The budget is what ends
# them, and it says so with the one sentence the proxy declares.
HELD_POLL_SECONDS = 0.25


class BudgetExpiredError(Exception):
    """The one absolute budget that began when the proxy accepted the mutation is gone."""


@contextmanager
def coordination_budget(accepted_at: float) -> Iterator[None]:
    """Bound everything inside to what is left of the one budget, interrupting a blocking call.

    Derived from the instant the proxy recorded rather than started here: the
    budget has to cover the forwarding and the durable signal, which already
    happened by the time this check learns the mutation was accepted. A timer
    armed on entry would begin after the part of the budget it is meant to bound.

    A real interval timer rather than a checked deadline, because the direct read
    of the destination below is a blocking HTTP call and a loop condition is not
    re-examined while a socket is waited on. Both the handler and the timer are
    process state, so both are put back.
    """
    remaining = accepted_at + PROXY_BUDGET_SECONDS - time.time()
    if remaining <= 0:
        raise BudgetExpiredError

    def expire(_signum: int, _frame: FrameType | None) -> None:
        raise BudgetExpiredError

    previous_handler = signal.signal(signal.SIGALRM, expire)
    previous_delay, previous_interval = signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, previous_delay, previous_interval)
        signal.signal(signal.SIGALRM, previous_handler)


def await_acceptance(client: SyncClient, run_id: str) -> float:
    """Return the instant the proxy accepted this run's mutation.

    Two ways this ends without one, and they are different findings. A run that
    reached a terminal record without any mutation arriving wrote nothing at all,
    which is a statement about the deployment. A run still going after the bound
    is a statement about this host.

    Terminal is read as the record being finished rather than as a phase: a phase
    past planning is exactly the reading this row stopped relying on.
    """
    deadline = time.monotonic() + ACCEPT_TIMEOUT_SECONDS
    while not held(ACCEPTED_AT):
        if client.get_run(run_id).run.finished_at is not None:
            refuse("the run finished without any mutation reaching the destination, so it wrote nothing to interrupt")
        if time.monotonic() >= deadline:
            refuse(
                f"no mutation from this run reached the destination proxy within {ACCEPT_TIMEOUT_SECONDS:.0f}s,"
                " so no destination write was ever in flight to interrupt"
            )
        time.sleep(ACCEPT_POLL_SECONDS)
    accepted = recorded(ACCEPTED_AT).strip()
    try:
        return float(accepted)
    except ValueError:
        refuse("the destination proxy recorded accepting a mutation without the instant it accepted it")


def await_upstream() -> None:
    """Block until the destination has completed the held write, or say why it did not.

    No deadline of its own: the one budget is what ends this, and the sentence it
    ends with is the one both sides of the handshake declare.
    """
    while not held(UPSTREAM_COMPLETED):
        if held(UPSTREAM_FAILED):
            refuse(
                f"the destination did not complete the write this row held --"
                f" {recorded(UPSTREAM_FAILED).strip()} -- so there is nothing ambiguous about interrupting it"
            )
        time.sleep(HELD_POLL_SECONDS)


def require_planted_value_reached(planted: str) -> None:
    """Prove the held write landed, by reading the destination rather than anything else.

    Read from the destination directly, on the branch the declared configuration
    writes to. The deployment's own view arrives through the proxy whose behaviour
    this row is arranging, and the proxy's record says a mutation completed rather
    than what it wrote -- neither is evidence about the destination's state.
    """
    branch = planned_branch()
    device = sdk().get(kind=SEEDED_KIND, branch=branch, name__value=SEEDED_DEVICE)
    if planted_attribute(device).value != planted:
        refuse(
            f"the destination does not hold the value this row planted on {branch}, so the held write"
            " was not the one this row is interrupting"
        )


with deployment() as client:
    # The rows before this one converged the two sides. An empty run reaches a
    # terminal state without ever writing, and interrupting it would leave nothing
    # ambiguous -- so this plants its own difference and proves, in a read-only run
    # of its own, that there is one operation to interrupt before it starts one.
    planted_value = plant("interrupt")
    proved = follow(
        client, client.plan(run_request(client, "plan", "clean-host: work to interrupt"), key("interrupt-probe"))
    )
    require_planned_work(client, proved.run.run_id, expected=PLANTED_OPERATIONS)

    # Armed before the run that produces the mutation is submitted. Armed after,
    # the write could already have gone through unheld -- and the row would wait
    # out its bound over a destination that had converged.
    signal_proxy(ARM)
    accepted_run = client.sync(run_request(client, "sync", "clean-host: interrupt mid-apply"), key("interrupt"))
    run_id = accepted_run.run.run_id

    accepted_at = await_acceptance(client, run_id)
    try:
        with coordination_budget(accepted_at):
            await_upstream()
            require_planted_value_reached(planted_value)
    except BudgetExpiredError:
        refuse(BUDGET_SENTENCE)

    # Last, and only here: this is the driver's licence to kill a worker, and by
    # now the destination has completed a write whose answer the worker cannot get.
    print(run_id)
