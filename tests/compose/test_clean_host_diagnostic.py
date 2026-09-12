"""What the pre-teardown account renders, read by running the renderer.

The check itself runs inside the candidate image against a real PostgreSQL, and
this suite reaches neither. What it can do is drive the one function that decides
what the retained artifact says, with rows shaped the way the store hands them
over — which is where a field leaks into an artifact or a run's executions stop
being attributable to it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

CHECKS = Path(__file__).resolve().parents[2] / "tests" / "compose" / "clean_host" / "checks"

RUN = "20260907T1307-a09b8278"
STARTED = "2026-09-07T13:07:00+00:00"
FINISHED = "2026-09-07T13:09:30+00:00"

# What the destination's own rejection put in the run's evidence, beside the two
# fields that describe what it touched. Neither of those two may print.
APPLY_FAILURE = {
    "stage": "apply",
    "outcome": "ambiguous",
    "error_type": "OperationApplyFailedError",
    "failed_operation": "op_ce661c16051cd41d",
    "may_have_partially_written": False,
}
SUMMARY = {"failed_stage": "apply", "sync_name": "infrahub-sync-qualification"}

# One joined row as the store hands it over. Overriding by name rather than by
# position is what lets a test say `results=None` and mean a null column.
COLUMNS: dict[str, object] = {
    "run_id": RUN,
    "operation": "sync",
    "phase": "apply-interrupted",
    "outcome": "ambiguous",
    "reconciliation_required": True,
    "started_at": STARTED,
    "finished_at": FINISHED,
    "summary": json.dumps(SUMMARY),
    "results": json.dumps({"apply_failure": APPLY_FAILURE}),
    "position": 0,
    "purpose": "plan",
    "attempt": 1,
    "last_observed_state": "Running",
    "terminal_state": "completed",
    "terminal_outcome": "succeeded",
}


@pytest.fixture(scope="module")
def diagnostics() -> Iterator[ModuleType]:
    """Load the check the way its own container does, with the kit importable beside it."""
    sys.path.insert(0, str(CHECKS))
    try:
        specification = importlib.util.spec_from_file_location("clean_host_diagnostics", CHECKS / "diagnostics.py")
        assert specification is not None
        loader = specification.loader
        assert loader is not None
        module = importlib.util.module_from_spec(specification)
        loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(CHECKS))


def row(**overrides: object) -> tuple[object, ...]:
    """One joined row, in the column order the query selects."""
    unknown = set(overrides) - set(COLUMNS)
    assert not unknown, f"the query selects no column named {sorted(unknown)}"
    return tuple({**COLUMNS, **overrides}.values())


def test_one_run_is_named_once_with_its_executions_beneath_it(diagnostics: ModuleType) -> None:
    """A run repeated per execution row cannot be read as one run that did three things."""
    rendered = list(
        diagnostics.render(
            [
                row(position=0, purpose="plan"),
                row(position=1, purpose="verify"),
                row(position=2, purpose="apply", terminal_state="interrupted", terminal_outcome="ambiguous"),
            ]
        )
    )

    assert len([line for line in rendered if line.startswith("run ")]) == 1
    assert len([line for line in rendered if line.startswith("    execution ")]) == 3
    assert rendered[0].startswith(f"run {RUN}")
    assert rendered[-1].endswith("terminal=interrupted/ambiguous")


def test_each_run_is_named_again_when_the_run_changes(diagnostics: ModuleType) -> None:
    """Grouping by the previous identifier is what makes the nesting mean anything."""
    rendered = list(diagnostics.render([row(run_id="first"), row(run_id="second"), row(run_id="first")]))

    assert [line.split()[1] for line in rendered if line.startswith("run ")] == ["first", "second", "first"]


def test_the_recorded_stage_failure_is_named_with_the_class_that_raised_it(diagnostics: ModuleType) -> None:
    """The one thing this account exists to carry."""
    rendered = list(diagnostics.render([row()]))

    assert "failure=apply/OperationApplyFailedError" in rendered[0]


def test_no_field_describing_what_a_run_touched_reaches_the_account(diagnostics: ModuleType) -> None:
    """`summary` and `results` are documents about the destination. They are read, not printed."""
    rendered = "\n".join(diagnostics.render([row()]))

    assert "op_ce661c16051cd41d" not in rendered
    assert "may_have_partially_written" not in rendered
    assert "infrahub-sync-qualification" not in rendered


@pytest.mark.parametrize(
    "recorded",
    ["a message with spaces", "x" * 200, "", None, 17, "Bad-Name"],
)
def test_an_error_type_that_is_not_a_class_name_does_not_print(diagnostics: ModuleType, recorded: object) -> None:
    """The one recorded field that is free-form in principle stays bounded in fact."""
    rendered = list(diagnostics.render([row(results=json.dumps({"apply_failure": {"error_type": recorded}}))]))

    assert "failure=apply/an unprintable error type" in rendered[0]


def test_a_run_that_never_reached_an_execution_says_so(diagnostics: ModuleType) -> None:
    """The left join yields nulls, and a blank execution line would read as a claim."""
    rendered = list(diagnostics.render([row(position=None, terminal_state=None, terminal_outcome=None)]))

    assert rendered[1] == "    no execution was ever claimed for this run"


def test_an_empty_store_is_reported_rather_than_rendered_as_nothing(diagnostics: ModuleType) -> None:
    """An empty section reads as a section that could not be produced."""
    assert list(diagnostics.render([])) == ["the store holds no run record"]


@pytest.mark.parametrize("column", ["", "not json at all", "[]", None, 5])
def test_a_document_column_that_is_not_a_document_is_read_as_none(diagnostics: ModuleType, column: object) -> None:
    """A store that hands back something unexpected must not end the whole account."""
    rendered = list(diagnostics.render([row(summary=column, results=column)]))

    assert "failure=none recorded" in rendered[0]
