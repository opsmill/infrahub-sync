"""A worker with work in hand is working, not missing.

Deployment-level readiness is about whether the worker is answering, so a run in
flight must not move it: an operator who saw DEGRADED here would restart a
healthy deployment in the middle of its own work.
"""

from __future__ import annotations

from kit import deployment, follow, key, refuse, run_request

with deployment() as client:
    accepted = client.plan(run_request(client, "plan", "clean-host: keep a worker busy"), key("busy"))
    state = client.get_status().worker.state
    if state not in {"ready", "busy"}:
        refuse(f"a worker with a run in hand reported {state}, which is neither ready nor busy")
    follow(client, accepted)
