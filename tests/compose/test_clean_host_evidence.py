"""What row 11's failure evidence must actually contain before it is swept.

Row 11's claim is that no credential this run generated reached the product's own
failure evidence or anything an orchestration console shows. A sweep is only as
wide as the bytes handed to it, so every place the collection can quietly stop
short is a place the claim becomes wider than the evidence.

There are three such places, and they are what this suite drives: the number of
runs enumerated, the completeness of each run's own documents, and the number of
Prefect log entries read back. In each case a silent trim reads exactly like a
deployment that had nothing more to say.

The check runs inside the candidate image against a real PostgreSQL and a real
Prefect server, and this suite reaches neither. What it can do is drive the
functions that decide how much is collected, with the shapes those two hand over.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

CHECKS = Path(__file__).resolve().parents[2] / "tests" / "compose" / "clean_host" / "checks"


@pytest.fixture(scope="module")
def evidence() -> Iterator[ModuleType]:
    """Load the check the way its own container does, with the kit importable beside it."""
    sys.path.insert(0, str(CHECKS))
    try:
        specification = importlib.util.spec_from_file_location(
            "clean_host_reported_failures", CHECKS / "reported_failures.py"
        )
        assert specification is not None
        loader = specification.loader
        assert loader is not None
        module = importlib.util.module_from_spec(specification)
        loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(CHECKS))


class Cursor:
    """A cursor that hands back as many run identifiers as it was told to hold."""

    def __init__(self, available: int) -> None:
        self.available = available
        self.asked: int | None = None

    def execute(self, _query: str, parameters: tuple[int, ...]) -> None:
        self.asked = parameters[0]

    def fetchall(self) -> list[tuple[str]]:
        assert self.asked is not None, "the rows were read before anything was asked for"
        return [(f"run-{index:04d}",) for index in range(min(self.available, self.asked))]


# ---------------------------------------------------------------------------
# Every run, and an overflow that says so
# ---------------------------------------------------------------------------
def test_every_run_the_deployment_holds_is_enumerated(evidence: ModuleType) -> None:
    """A tail is the wrong instrument for a sweep.

    The first deployment in this matrix has already made twelve runs by the time
    row 11 reads it, so a limit of ten silently dropped two of them — and the row
    then reported that no credential reached failure evidence it had not looked at.
    """
    cursor = Cursor(available=12)

    assert len(evidence.collected_run_ids(cursor)) == 12


def test_exactly_the_maximum_number_of_runs_is_still_collected(evidence: ModuleType) -> None:
    """The bound is a bound, not an off-by-one refusal at the last acceptable run."""
    cursor = Cursor(available=evidence.MAX_RUNS)

    assert len(evidence.collected_run_ids(cursor)) == evidence.MAX_RUNS


def test_more_runs_than_the_maximum_is_refused_rather_than_trimmed(evidence: ModuleType) -> None:
    """A bound has to be observed to be honest, so one more than it is asked for.

    Asking for exactly the maximum cannot tell a deployment that holds exactly
    that many from one that holds thousands: both answer with a full page. The
    extra row is what makes the difference visible, and an overflow ends the row
    rather than quietly narrowing what row 11 claims to have swept.
    """
    cursor = Cursor(available=evidence.MAX_RUNS + 1)

    with pytest.raises(SystemExit):
        evidence.collected_run_ids(cursor)

    assert cursor.asked == evidence.MAX_RUNS + 1, "the query cannot observe an overflow it never asked for"


# ---------------------------------------------------------------------------
# The whole of each run, not a chosen few fields
# ---------------------------------------------------------------------------
class Run:
    """One run resource, dumped the way the client's models dump."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        del mode
        return self.payload


class Client:
    """The deployment, answering for one run and its results."""

    def __init__(self, run: dict[str, Any], results: dict[str, Any]) -> None:
        self.run = run
        self.results = results

    def get_run(self, _run_id: str) -> Any:  # noqa: ANN401 - whatever the resource is
        return type("Resource", (), {"run": Run(self.run), "orchestration": ()})()

    def get_results(self, _run_id: str) -> Any:  # noqa: ANN401 - whatever the resource is
        return type("Results", (), {"results": self.results})()


def test_the_whole_run_and_the_whole_results_document_are_collected(evidence: ModuleType) -> None:
    """A credential can only be found in bytes the sweep was given.

    Enumerating a handful of fields is a redaction, and a sweep over a redaction
    clears the redaction rather than the evidence. Both documents go through
    whole.
    """
    client = Client(
        run={"run_id": "r", "phase": "apply-interrupted", "a_field_nobody_thought_of": "clean-host-canary"},
        results={"apply_failure": {"nested": {"deep": "clean-host-canary"}}},
    )

    collected = evidence.product_failure_evidence(client, "r")
    rendered = repr(collected)

    assert rendered.count("clean-host-canary") == 2, "a field the collection did not enumerate is not swept"
    assert collected["run"]["a_field_nobody_thought_of"] == "clean-host-canary"
    assert collected["results"]["apply_failure"]["nested"]["deep"] == "clean-host-canary"


# ---------------------------------------------------------------------------
# Every Prefect log entry, paginated, with an overflow that says so
# ---------------------------------------------------------------------------
def pages(available: int) -> Any:  # noqa: ANN401 - a fetch callable
    """A Prefect server holding `available` log entries for one flow run."""

    def fetch(_flow_run_id: str, offset: int, limit: int) -> list[dict[str, Any]]:
        return [{"message": f"line {index}"} for index in range(offset, min(offset + limit, available))]

    return fetch


def test_every_prefect_log_entry_is_read_rather_than_the_last_page_of_them(evidence: ModuleType) -> None:
    """A two-hundred-line tail is a tail whatever the server holds.

    What an orchestration console shows is the whole log, and that is what row 11
    claims carries no credential.
    """
    collected = evidence.collected_logs("flow", fetch=pages(650))

    assert len(collected) == 650, "the log is truncated to one page and reported as the whole log"
    assert collected[0]["message"] == "line 0", "the earliest entries are the ones dropped"


def test_a_log_shorter_than_one_page_needs_no_second_request(evidence: ModuleType) -> None:
    """A short page is the end of the log, and asking again would be asking for nothing."""
    asked: list[int] = []

    def fetch(_flow_run_id: str, offset: int, _limit: int) -> list[dict[str, Any]]:
        asked.append(offset)
        return [{"message": "only"}] if offset == 0 else []

    assert len(evidence.collected_logs("flow", fetch=fetch)) == 1
    assert asked == [0], "the collection kept paging past the end of the log"


def test_exactly_the_maximum_number_of_log_entries_is_still_collected(evidence: ModuleType) -> None:
    """The bound is a bound, not an off-by-one refusal at the last acceptable entry."""
    assert len(evidence.collected_logs("flow", fetch=pages(evidence.MAX_LOG_ENTRIES))) == evidence.MAX_LOG_ENTRIES


def test_more_log_entries_than_the_maximum_is_refused_rather_than_trimmed(evidence: ModuleType) -> None:
    """The same reason as the runs: a bound nobody can observe is a silent trim."""
    with pytest.raises(SystemExit):
        evidence.collected_logs("flow", fetch=pages(evidence.MAX_LOG_ENTRIES + 1))


def test_the_collection_never_asks_for_more_than_one_past_the_maximum(evidence: ModuleType) -> None:
    """An overflow is one entry past the bound, and reading further is unbounded."""
    reached = 0

    def fetch(_flow_run_id: str, offset: int, limit: int) -> list[dict[str, Any]]:
        nonlocal reached
        reached = max(reached, offset + limit)
        return [{"message": "x"} for _ in range(limit)]

    with pytest.raises(SystemExit):
        evidence.collected_logs("flow", fetch=fetch)

    assert reached == evidence.MAX_LOG_ENTRIES + 1, f"the collection read up to {reached} entries"


def test_a_server_that_answered_with_something_other_than_entries_ends_the_row(evidence: ModuleType) -> None:
    """An answer this check cannot read is not an empty log."""

    def fetch(_flow_run_id: str, _offset: int, _limit: int) -> Any:  # noqa: ANN401 - deliberately wrong
        return {"not": "a list"}

    with pytest.raises(SystemExit):
        evidence.collected_logs("flow", fetch=fetch)
