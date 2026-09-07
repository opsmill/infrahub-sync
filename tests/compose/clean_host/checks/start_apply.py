"""Start a confirmed write and return only once it has actually reached the write.

The recovery row interrupts a worker mid-apply. Interrupting one before it began
writing leaves a run that never wrote, nothing to reconcile, and a row that would
report success having tested nothing — so this blocks until the run has moved past
planning, and prints the run identifier for the driver to interrupt.
"""

from __future__ import annotations

import sys
import time

from kit import POLL_SECONDS, RUN_TIMEOUT_SECONDS, deployment, key, refuse, run_request

with deployment() as client:
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
