"""Everything the deployment reports about its own failures, for the canary sweep.

Collected here rather than in the driver because reaching it needs the client and
the orchestration surface, and the sweep that follows is about what an operator or
an orchestration console would see.
"""

from __future__ import annotations

from kit import deployment

with deployment() as client:
    for summary in client.list_configs():
        print(summary.model_dump_json())
    status = client.get_status()
    print(status.model_dump_json())
