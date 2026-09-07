"""Which workers this deployment's own Prefect server currently reports online.

A worker is identified to Prefect by a name it generates per process, so a
replacement is a different worker as far as the server is concerned -- and that,
not the container, is what a restart replaces. The shipped `restart` runs
`docker compose restart sync-api sync-worker`, which restarts the process inside
the container it already has, and its own comment says so: "Containers and data
survive; the processes inside them do not. The worker that comes back registers
under a new Prefect identity."

Read from the deployment's own Prefect server, over the deployment's own network,
because that is the only route to a worker name: the shipped status API reports a
worker's state and the live count and carries no name at all
(`client/models.py` `WorkerStatusResource`). Nothing here is a product test hook.

The pool comes from the deployment's own defaults rather than a literal: it is a
configurable setting, and a probe naming a pool the deployment does not use would
report an empty set for a healthy deployment.
"""

from __future__ import annotations

import os

import httpx
from kit import refuse

POOL = os.environ["INFRAHUB_SYNC_WORK_POOL"]
API = os.environ["PREFECT_API_URL"].rstrip("/")

# The state Prefect reports for a worker that is heartbeating. A departing
# worker's record lingers in it for a while, which is why the row that reads this
# waits for a name it has not seen rather than for the set to change size.
ONLINE = "ONLINE"

answer = httpx.post(f"{API}/work_pools/{POOL}/workers/filter", json={}, timeout=30)
if answer.status_code != 200:
    refuse(f"the deployment's Prefect server did not answer for work pool {POOL}")

reported = answer.json()
if not isinstance(reported, list):
    refuse(f"the deployment's Prefect server answered for {POOL} without a list of workers")

for name in sorted(
    str(record["name"])
    for record in reported
    if isinstance(record, dict) and record.get("status") == ONLINE and record.get("name")
):
    print(name)
