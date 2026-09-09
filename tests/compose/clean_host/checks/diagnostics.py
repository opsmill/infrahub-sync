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

One line per run, then one indented line per execution beneath it. The bound is
on runs rather than on joined rows, so a run with several executions cannot push
the runs before it out of the account.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence
from typing import Any

from kit import FAILURE_STAGES, printable_type_name, refuse

# A row fails inside a matrix that has made a bounded number of runs, and the
# ones that matter are the last few. A tail, not a stream.
RUNS = 10

# The columns read, in the order they print. Every one is either an identifier
# the deployment generated or a value from a closed set. `summary` and `results`
# are documents describing what a run touched; they are read, never printed.
QUERY = """
WITH recent AS (
    SELECT run_id, operation, phase, outcome, reconciliation_required,
           started_at, finished_at, summary, results
    FROM product_runs ORDER BY started_at DESC LIMIT %s
)
SELECT r.run_id, r.operation, r.phase, r.outcome, r.reconciliation_required,
       r.started_at, r.finished_at, r.summary, r.results,
       e.position, e.purpose, e.attempt, e.last_observed_state,
       e.terminal_state, e.terminal_outcome
FROM recent AS r
LEFT JOIN prefect_executions AS e ON e.run_id = r.run_id
ORDER BY r.started_at DESC, e.position ASC
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


# The column order the query selects, named once so nothing reads by position.
RUN_ID, OPERATION, PHASE, OUTCOME, RECONCILIATION, STARTED, FINISHED, SUMMARY, RESULTS = range(9)
POSITION, PURPOSE, ATTEMPT, LAST_OBSERVED, TERMINAL_STATE, TERMINAL_OUTCOME = range(9, 15)


def run_line(row: Sequence[object]) -> str:
    """Render one run: what it was, where it stopped, and which stage recorded a failure."""
    return (
        f"run {row[RUN_ID]} {row[OPERATION]} phase={row[PHASE]} outcome={row[OUTCOME]}"
        f" reconciliation_required={row[RECONCILIATION]}"
        f" started={row[STARTED]} finished={row[FINISHED]}"
        f" failure={failure(row[SUMMARY], row[RESULTS])}"
    )


def execution_line(row: Sequence[object]) -> str:
    """Render one execution of a run, or say that the left join found none."""
    if row[POSITION] is None:
        return "    no execution was ever claimed for this run"
    return (
        f"    execution position={row[POSITION]} purpose={row[PURPOSE]} attempt={row[ATTEMPT]}"
        f" last_observed={row[LAST_OBSERVED]}"
        f" terminal={row[TERMINAL_STATE]}/{row[TERMINAL_OUTCOME]}"
    )


def render(rows: Sequence[Sequence[object]]) -> Iterator[str]:
    """Render the joined rows as one run per line with its executions beneath it."""
    if not rows:
        yield "the store holds no run record"
        return
    seen: str | None = None
    for row in rows:
        identifier = str(row[RUN_ID])
        if identifier != seen:
            seen = identifier
            yield run_line(row)
        yield execution_line(row)


def main() -> None:
    """Read the run records this deployment holds and print the account of them.

    The store driver is imported here rather than beside the others: the
    rendering above decides what a retained artifact says, and it is driven
    directly by a test that runs where this optional service dependency is not
    installed.
    """
    # ty cannot resolve this on the Python 3.10 profile, where the service extras
    # are not installed -- the same reason every other check that reaches a store
    # carries this suppression.
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    url = os.environ.get("INFRAHUB_SYNC_DATABASE_URL")
    if not url:
        refuse("this check was given no database to read the run records from")
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(QUERY, (RUNS,))
        rows = cursor.fetchall()
    for line in render(rows):
        print(line)


if __name__ == "__main__":
    main()
