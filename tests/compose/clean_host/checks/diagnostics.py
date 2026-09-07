"""What the deployment recorded about its own runs, read from the store itself.

This is the account a failed row leaves behind, and the teardown that follows
removes the state holding it -- so it is taken before teardown, and it is read
from PostgreSQL directly. Through the client would be the wrong instrument
twice: the verdict under diagnosis is one the client reported, and a client that
misread a response would describe the failure in the same direction as the bug
that caused it.

What it prints is enumerated field by field: identifiers the deployment
generated, the phase and outcome columns, each execution's terminal state, and
the class name of a recorded stage failure. The rest of a failure's evidence
describes what a run touched in the destination, and none of that belongs in an
artifact.
"""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency
from kit import FAILURE_STAGES, printable_type_name, refuse

# One run crosses a handful of executions, and a row fails inside a matrix that
# has made a bounded number of them. A tail, not a stream.
RUNS = 20

# The columns read, in the order they print. Every one is either an identifier
# the deployment generated or a value from a closed set.
QUERY = """
SELECT r.run_id, r.operation, r.phase, r.outcome, r.reconciliation_required,
       r.started_at, r.finished_at, r.summary, r.results,
       e.position, e.purpose, e.attempt, e.last_observed_state,
       e.terminal_state, e.terminal_outcome
FROM product_runs AS r
LEFT JOIN prefect_executions AS e ON e.run_id = r.run_id
ORDER BY r.started_at DESC, e.position DESC
LIMIT %s
"""


def decoded(column: object) -> dict[str, Any]:
    """Return one JSON column as a mapping, or an empty one if it is not that.

    The driver reaches PostgreSQL, so a column arrives as text; a store that
    hands back a decoded document is read the same way rather than differently.
    """
    loaded: object = column
    if isinstance(column, (str, bytes)):
        try:
            loaded = json.loads(column)
        except ValueError:
            return {}
    if isinstance(loaded, dict):
        return {str(key): value for key, value in loaded.items()}
    return {}


def failure(summary: object, results: object) -> str:
    """Return the stage that failed and the class it raised, from the stored evidence."""
    recorded = decoded(results)
    for stage in FAILURE_STAGES:
        evidence = recorded.get(f"{stage}_failure")
        if isinstance(evidence, dict):
            return f"{stage}/{printable_type_name(evidence.get('error_type'))}"
    stage = decoded(summary).get("failed_stage")
    if isinstance(stage, str):
        return f"{printable_type_name(stage)}/no recorded error type"
    return "none recorded"


def main() -> None:
    url = os.environ.get("INFRAHUB_SYNC_DATABASE_URL")
    if not url:
        refuse("this check was given no database to read the run records from")
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(QUERY, (RUNS,))
        rows = cursor.fetchall()
    if not rows:
        print("the store holds no run record")
        return
    for (
        run_id,
        operation,
        phase,
        outcome,
        reconciliation_required,
        started_at,
        finished_at,
        summary,
        results,
        position,
        purpose,
        attempt,
        last_observed_state,
        terminal_state,
        terminal_outcome,
    ) in rows:
        print(
            f"run {run_id} {operation} phase={phase} outcome={outcome}"
            f" reconciliation_required={reconciliation_required}"
            f" started={started_at} finished={finished_at}"
            f" failure={failure(summary, results)}"
        )
        print(
            f"    execution position={position} purpose={purpose} attempt={attempt}"
            f" last_observed={last_observed_state}"
            f" terminal={terminal_state}/{terminal_outcome}"
        )


main()
