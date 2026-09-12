"""Create durable state the alpha replacement row then replaces.

A replacement that started from nothing would demonstrate nothing: the row's
claim is that prior state is gone and a clean deployment stands in its place, so
there has to be prior state.
"""

from __future__ import annotations

from kit import deployment, follow, key, refuse, run_request

with deployment() as client:
    completed = follow(client, client.plan(run_request(client, "plan", "clean-host: disposable state"), key("state")))
    if "failed" in completed.run.phase:
        refuse(f"the run that creates disposable state ended in {completed.run.phase}")
