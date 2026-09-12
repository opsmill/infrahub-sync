"""The raw failure evidence row 11 sweeps, from the two places its claim names.

Row 11's claim is that no credential this run generated reached the product's own
failure evidence or anything an orchestration console shows. A sweep is only ever
as wide as the bytes it was handed, so every place this collection could quietly
stop short is a place that claim becomes wider than the evidence behind it.

There are three such places, and none of them is allowed to trim silently:

* **which runs.** Every run the deployment holds, not a tail of them. The first
  deployment in this matrix has already made a dozen runs by the time row 11
  reads it, so a limit chosen to look reasonable dropped real failure documents.
  There is still a maximum, because a stream is not evidence anyone can finish
  sweeping -- but one more than the maximum is asked for, so an overflow is
  observed and refused instead of arriving as a full page nobody can tell from a
  complete answer.

* **how much of each run.** Both documents whole: the run resource as the client
  models it, and the results document as the deployment stored it. Enumerating a
  few fields would be a redaction, and a sweep over a redaction clears the
  redaction.

* **how much of each log.** Every entry an orchestration console would show,
  paginated to the end, with the same max-plus-one refusal. A two-hundred-line
  tail is a tail whatever the server holds.

Raw on purpose. Every other check prints enumerated fields, because what it
prints reaches a message or a retained artifact. This one exists so a sweep has
unredacted bytes to search: the driver captures it to a file, sweeps it, and
destroys it. It is never retained and never reaches a terminal.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from kit import deployment, refuse

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from infrahub_sync.client import SyncClient

# The most this check will collect, and what it does when there is more. Both
# numbers are generous: they exist so an unbounded read cannot happen, not to
# choose how much of the evidence row 11 gets. Reaching either is a refusal.
MAX_RUNS = 500
MAX_LOG_ENTRIES = 20000
LOG_PAGE = 200

# The deployment's own Prefect server, over the deployment's own network. The
# shipped status API reports a worker state and a queue depth and carries no flow
# run at all, so this is the only route to what an orchestration console shows.
PREFECT = os.environ.get("PREFECT_API_URL", "").rstrip("/")

# How the run records are enumerated. The API exposes one run by identifier, so
# which runs exist is read from the store the deployment writes them to. One more
# than the maximum, because a query that asked for exactly the maximum cannot
# tell a deployment holding that many from one holding a hundred times more.
QUERY = "SELECT run_id FROM product_runs ORDER BY started_at DESC LIMIT %s"

HTTP_TIMEOUT_SECONDS = 30.0


class Cursor(Protocol):
    """The one thing this check needs of a database cursor.

    Positional, and loosely typed on purpose: what this has to accept is the real
    driver's cursor, whose parameter names are its own. Naming them here would
    make the protocol describe this check rather than the thing it is handed.
    """

    def execute(self, query: Any, parameters: Any, /) -> object:  # noqa: ANN401 - the driver's own shapes
        """Run one parameterised statement."""

    def fetchall(self) -> Sequence[Any]:
        """Return every row the statement produced."""


def collected_run_ids(cursor: Cursor) -> list[str]:
    """Return every run this deployment recorded, refusing rather than trimming.

    Asked for one more than the maximum: a full page at exactly the maximum reads
    identically whether the deployment holds that many or thousands, and the
    difference is the whole question. The extra row makes it visible.
    """
    cursor.execute(QUERY, (MAX_RUNS + 1,))
    rows = cursor.fetchall()
    if len(rows) > MAX_RUNS:
        refuse(
            f"this deployment holds more than the {MAX_RUNS} runs this check will collect, so the sweep"
            " that follows would cover less evidence than it claims"
        )
    return [str(row[0]) for row in rows]


def recent_run_ids() -> list[str]:
    """Return every run identifier, read from the store the deployment writes them to."""
    # ty cannot resolve this on the Python 3.10 profile, where the service extras
    # are not installed -- the same reason every other check that reaches a store
    # carries this suppression.
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    url = os.environ.get("INFRAHUB_SYNC_DATABASE_URL")
    if not url:
        refuse("this check was given no database to read the deployment's runs from")
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        return collected_run_ids(cursor)


def product_failure_evidence(client: SyncClient, run_id: str) -> dict[str, Any]:
    """Return everything the product records about one run, whole.

    Both documents entire. `results` is where each stage writes its failure
    evidence and the run resource carries everything the deployment reports about
    itself; a sweep given only the fields another check prints would be searching
    a redaction of the thing it is meant to search, and a field nobody thought to
    enumerate is exactly where an unnoticed credential would be.
    """
    resource = client.get_run(run_id)
    return {
        "run": resource.run.model_dump(mode="json"),
        "orchestration": [attempt.model_dump(mode="json") for attempt in resource.orchestration],
        "results": client.get_results(run_id).results,
    }


def fetch_logs(flow_run_id: str, offset: int, limit: int) -> Any:  # noqa: ANN401 - whatever the server answered
    """Return one page of what the deployment's Prefect server logged for one execution."""
    answered = httpx.post(
        f"{PREFECT}/logs/filter",
        json={
            "logs": {"flow_run_id": {"any_": [flow_run_id]}},
            "sort": "TIMESTAMP_ASC",
            "limit": limit,
            "offset": offset,
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    if answered.status_code != httpx.codes.OK:
        refuse(f"the deployment's Prefect server did not answer for the logs of flow run {flow_run_id}")
    return answered.json()


def collected_logs(flow_run_id: str, fetch: Callable[[str, int, int], Any] = fetch_logs) -> list[Any]:
    """Return every log entry one execution holds, paginated, refusing rather than trimming.

    What an orchestration console shows is the whole log, and that is what row 11
    claims carries no credential. So this pages to the end, and the last page it
    will ever ask for reaches exactly one entry past the maximum -- a full page
    there is an overflow, and an overflow is a refusal rather than a quiet tail.
    """
    collected: list[Any] = []
    offset = 0
    while True:
        if len(collected) > MAX_LOG_ENTRIES:
            refuse(
                f"one execution holds more than the {MAX_LOG_ENTRIES} log entries this check will collect, so"
                " the sweep that follows would cover less evidence than it claims"
            )
        wanted = min(LOG_PAGE, MAX_LOG_ENTRIES + 1 - len(collected))
        page = fetch(flow_run_id, offset, wanted)
        if not isinstance(page, list):
            refuse(f"the deployment's Prefect server answered for the logs of {flow_run_id} without a list of entries")
        collected.extend(page)
        if len(page) < wanted:
            return collected
        offset += len(page)


def prefect_evidence(flow_run_id: str) -> dict[str, Any]:
    """Return what the deployment's Prefect server shows for one execution.

    The flow run whole, then every log entry. A console shows both, and a failing
    task's log is the one place a worker's own rendering of its inputs appears.
    """
    state = httpx.get(f"{PREFECT}/flow_runs/{flow_run_id}", timeout=HTTP_TIMEOUT_SECONDS)
    if state.status_code != httpx.codes.OK:
        refuse(f"the deployment's Prefect server did not answer for flow run {flow_run_id}")
    return {"flow_run_id": flow_run_id, "flow_run": state.json(), "logs": collected_logs(flow_run_id)}


def main() -> None:
    """Print the raw failure evidence for every run this deployment holds.

    Fails closed everywhere. A collection that could not be completed is not a
    shorter list of bytes to sweep: it is a claim the sweeping row would then be
    making about evidence nobody read.
    """
    if not PREFECT:
        refuse("this check was given no orchestration API to read the executions' states and logs from")
    with deployment() as client:
        for run_id in recent_run_ids():
            evidence = product_failure_evidence(client, run_id)
            executions = evidence["run"].get("prefect_executions") or []
            evidence["prefect"] = [
                prefect_evidence(str(link["flow_run_id"])) for link in executions if link.get("flow_run_id")
            ]
            print(json.dumps(evidence, default=str))


if __name__ == "__main__":
    main()
