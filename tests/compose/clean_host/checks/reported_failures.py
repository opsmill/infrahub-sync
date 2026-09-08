"""The raw failure evidence row 11 sweeps, from the two places its claim names.

Row 11's claim is that no credential this run generated reached the product's own
failure evidence or anything an orchestration console shows. Printing a
configuration summary and a status line swept neither: both are documents that
could not carry a generated credential in the first place, so the sweep cleared
bytes it had no reason to suspect.

What the claim is actually about is two things, and they are collected here
because reaching either needs the client and the orchestration API rather than
the shell:

* the product's own recorded failure evidence -- the whole ``results`` document
  each recent run holds, as the API hands it over, plus the run record's own
  fields. This is what an operator reads after a run failed, and it is where a
  destination's rejection, a traceback's context and a rendered request would
  land if any of them carried a credential.

* what the deployment's own Prefect server reports about the executions behind
  those runs -- each flow run's state, its message, and a bounded tail of its
  logs. This is what an orchestration console shows, and a worker that logged an
  environment would put it here.

Raw on purpose. Every other check prints only enumerated fields, because what it
prints reaches a message or a retained artifact. This one exists so a sweep has
the unredacted bytes to search, and the driver captures it to a file, sweeps it,
and destroys it -- it is never retained and never reaches a terminal.

Bounded on purpose too. A stream is not evidence a row can finish sweeping before
the deployment holding it is destroyed.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import httpx
from kit import deployment, refuse

if TYPE_CHECKING:
    from infrahub_sync.client import SyncClient

# The recent runs, and the tail of each execution's logs. A row fails inside a
# matrix that made a bounded number of runs and the ones that matter are the last
# few -- the same bound the pre-teardown account uses, for the same reason.
RUNS = 10
LOG_LIMIT = 200

# The deployment's own Prefect server, over the deployment's own network. The
# shipped status API reports a worker state and a queue depth and carries no flow
# run at all, so this is the only route to what an orchestration console shows.
PREFECT = os.environ.get("PREFECT_API_URL", "").rstrip("/")

# How the run records are enumerated. The API exposes one run by identifier, so
# which runs exist is read from the store the deployment writes them to -- the
# same store and the same bound the pre-teardown account reads.
QUERY = "SELECT run_id FROM product_runs ORDER BY started_at DESC LIMIT %s"

HTTP_TIMEOUT_SECONDS = 30.0


def recent_run_ids() -> list[str]:
    """Return the identifiers of the runs this deployment most recently recorded."""
    # ty cannot resolve this on the Python 3.10 profile, where the service extras
    # are not installed -- the same reason every other check that reaches a store
    # carries this suppression.
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    url = os.environ.get("INFRAHUB_SYNC_DATABASE_URL")
    if not url:
        refuse("this check was given no database to read the deployment's runs from")
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(QUERY, (RUNS,))
        return [str(row[0]) for row in cursor.fetchall()]


def product_failure_evidence(client: SyncClient, run_id: str) -> dict[str, Any]:
    """Return everything the product records about one run, raw.

    Both documents, whole. ``results`` is where each stage writes its failure
    evidence and ``summary`` is what the run reports about itself; a sweep given
    only the fields another check prints would be searching a redaction of the
    thing it is meant to search.
    """
    run = client.get_run(run_id).run
    return {
        "run_id": run.run_id,
        "operation": run.operation,
        "phase": run.phase,
        "outcome": run.outcome,
        "reconciliation_required": run.reconciliation_required,
        "summary": run.summary,
        "results": client.get_results(run_id).results,
        "prefect_executions": [link.model_dump(mode="json") for link in run.prefect_executions],
    }


def prefect_evidence(flow_run_id: str) -> dict[str, Any]:
    """Return what the deployment's Prefect server shows for one execution.

    The state and its message, then a bounded tail of the logs. A console shows
    both, and a failing task's log is the one place a worker's own rendering of
    its inputs would appear.
    """
    collected: dict[str, Any] = {"flow_run_id": flow_run_id}
    state = httpx.get(f"{PREFECT}/flow_runs/{flow_run_id}", timeout=HTTP_TIMEOUT_SECONDS)
    if state.status_code != httpx.codes.OK:
        refuse(f"the deployment's Prefect server did not answer for flow run {flow_run_id}")
    collected["flow_run"] = state.json()
    logged = httpx.post(
        f"{PREFECT}/logs/filter",
        json={
            "logs": {"flow_run_id": {"any_": [flow_run_id]}},
            "sort": "TIMESTAMP_DESC",
            "limit": LOG_LIMIT,
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    if logged.status_code != httpx.codes.OK:
        refuse(f"the deployment's Prefect server did not answer for the logs of flow run {flow_run_id}")
    collected["logs"] = logged.json()
    return collected


def main() -> None:
    """Print the raw failure evidence for the runs this deployment holds.

    Fails closed. A collection that could not be completed is not a shorter list
    of bytes to sweep: it is a claim the sweeping row would then be making about
    evidence nobody read.
    """
    if not PREFECT:
        refuse("this check was given no orchestration API to read the executions' states and logs from")
    with deployment() as client:
        for run_id in recent_run_ids():
            evidence = product_failure_evidence(client, run_id)
            for link in evidence["prefect_executions"]:
                evidence.setdefault("prefect", []).append(prefect_evidence(str(link["flow_run_id"])))
            print(json.dumps(evidence, default=str))


if __name__ == "__main__":
    main()
