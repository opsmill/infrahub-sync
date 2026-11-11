"""The CLI lines for problems, left-out and kept records (spec 019 T016, FR-007, contracts/cli.md).

- `infrahub-sync plan` and `infrahub-sync runs plan <run_id>` print, after the counts by action,
  `left_out`, `problems` and `kept_without_relationship`, then one line per kind whose deletes
  the plan does not compute and one line when Dropdown values were not checked.
- `infrahub-sync plan` then prints the first 20 problems and the command that lists the rest.
- `infrahub-sync runs plan <run_id> --problems [--type] [--kind]`, `--left-out [--kind]` and
  `--kept [--kind]` read every page and print one line per entry. `--problems`, `--left-out`,
  `--kept` and `--detail` are exclusive, and `--type` needs `--problems`.

The client is a `SyncClient` mock, as in `test_client_commands.py`.

Names this module assumes, for spec 019 T029 to T031 to confirm or change:

- the client's paged reads `get_plan_problems`, `get_plan_left_out` and `get_plan_kept`, each
  called with the run ID and the keywords `offset`, `limit` and the filters `problem_type`
  (problems only) and `kind`, each returning a page with `entries`, `total` and `next_offset`
  (contracts/sync-api.md); the pages here are plain namespaces with those fields
- the summary counts of data-model.md "Plan summary additions" on `PlanSummaryResource`; the
  summary is built with `model_construct` because the fields do not exist before T029

Until T029 to T031 each test is an expected failure: the CLI prints none of these lines and has no
`--problems`, `--left-out`, `--kept` or `--type` option (`AssertionError`).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from infrahub_sync.client import PlanSummaryResource
from tests.cli.test_client_commands import _invoke, _plan, client

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

__all__ = ["client"]

pytestmark = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="spec 019 US1 not implemented: the CLI prints no problem, left-out or kept line (T031)",
)

RUN_ID = "service-run-1"

# A server page size smaller than any limit the CLI asks for, so a command that reads one page
# only misses entries. Used only where a test checks that every page is read.
PAGE_SIZE = 2


@pytest.fixture(autouse=True)
def _restore_package_logging() -> Iterator[None]:
    """Undo the logger level and handler that `main` installs on every CLI invocation."""
    package_logger = logging.getLogger("infrahub_sync")
    level = package_logger.level
    handlers = list(package_logger.handlers)
    yield
    package_logger.setLevel(level)
    for handler in list(package_logger.handlers):
        if handler not in handlers:
            package_logger.removeHandler(handler)
            handler.close()


def _record(kind: str, source_id: str, **identity: str) -> dict[str, Any]:
    return {"kind": kind, "identity": identity, "source_id": source_id}


def _problem(index: int) -> dict[str, Any]:
    return {
        "problem_type": "wrong_value_type",
        "record": _record("DcimDevice", str(index), name=f"rtr{index:02d}"),
        "field": "position",
        "value": 2.5,
        "allowed": None,
        "peer": None,
        "other_records": [],
        "reason": f"DcimDevice rtr{index:02d}: position 2.5 is not a Number",
    }


DROPDOWN_PROBLEM = {
    "problem_type": "dropdown_value_not_in_choices",
    "record": _record("LocationSite", "3", name="berlin"),
    "field": "status",
    "value": "decommissioning",
    "allowed": ["active", "planned"],
    "peer": None,
    "other_records": [],
    "reason": "LocationSite berlin: status 'decommissioning' is not one of the choices of the Dropdown attribute status",
}

LEFT_OUT = [
    {
        "record": _record("LocationSite", "3", name="berlin"),
        "cause": "has_problem",
        "problem_types": ["dropdown_value_not_in_choices"],
        "depends_on": None,
        "relationship": None,
        "loaded": True,
    },
    {
        "record": _record("InterfacePhysical", "40", name="eth0"),
        "cause": "depends_on_left_out",
        "problem_types": [],
        "depends_on": _record("DcimDevice", "30", name="rtr30"),
        "relationship": "device",
        "loaded": True,
    },
    {
        "record": _record("InterfacePhysical", "41", name="eth1"),
        "cause": "depends_on_left_out",
        "problem_types": [],
        "depends_on": _record("DcimDevice", "30", name="rtr30"),
        "relationship": "device",
        "loaded": True,
    },
]

KEPT = [
    {
        "record": _record("IpamIPAddress", "82", address="10.0.0.9/32"),
        "cause": "peer_not_found_in_source",
        "relationship": "vrf",
        "left_out_peer": {"kind": "IpamVRF", "identity": None, "source_id": "998"},
    }
]


def _summary(**additions: Any) -> PlanSummaryResource:  # noqa: ANN401
    """The plan summary of `_plan()` with the counts of data-model.md "Plan summary additions"."""
    counts: dict[str, Any] = {
        "left_out": 0,
        "left_out_with_problem": 0,
        "left_out_dependent": 0,
        "left_out_by_kind": {},
        "problems": 0,
        "problems_by_type": {},
        "kept": 0,
        "deletes_withheld_by_kind": {},
        "dropdown_values_checked": True,
    }
    counts.update(additions)
    base = _plan().summary.model_dump()
    return PlanSummaryResource.model_construct(**base, **counts)


def _pages(
    entries: list[dict[str, Any]], *, filters: tuple[str, ...], page_size: int | None = None
) -> Callable[..., SimpleNamespace]:
    """A paged read over `entries`, filtered by the given keywords.

    A page holds `limit` entries, or fewer when `page_size` is smaller, as a server may answer.
    """

    def read(run_id: str, *, offset: int = 0, limit: int = 500, **given: Any) -> SimpleNamespace:  # noqa: ANN401
        assert run_id == RUN_ID
        assert set(given) <= set(filters)
        selected = [
            entry
            for entry in entries
            if (given.get("problem_type") is None or entry.get("problem_type") == given["problem_type"])
            and (given.get("kind") is None or entry["record"]["kind"] == given["kind"])
        ]
        size = limit if page_size is None else min(limit, page_size)
        page = selected[offset : offset + size]
        next_offset = offset + size if offset + size < len(selected) else None
        return SimpleNamespace(
            run_id=RUN_ID,
            plan_checksum=_plan().checksum,
            entries=tuple(page),
            offset=offset,
            limit=limit,
            total=len(selected),
            next_offset=next_offset,
        )

    return read


def _with_report(
    client: MagicMock,
    *,
    problem_entries: list[dict[str, Any]],
    left_out_entries: list[dict[str, Any]] | None = None,
    kept_entries: list[dict[str, Any]] | None = None,
    page_size: int | None = None,
    **summary: Any,  # noqa: ANN401
) -> None:
    """Make the mocked client serve a plan summary with these counts and the three paged lists."""
    plan = _plan()
    client.get_plan.return_value = plan.model_copy(update={"summary": _summary(**summary)})
    client.get_plan_problems = MagicMock(
        side_effect=_pages(problem_entries, filters=("problem_type", "kind"), page_size=page_size)
    )
    client.get_plan_left_out = MagicMock(
        side_effect=_pages(left_out_entries or [], filters=("kind",), page_size=page_size)
    )
    client.get_plan_kept = MagicMock(side_effect=_pages(kept_entries or [], filters=("kind",), page_size=page_size))


def _lines_starting(output: str, prefix: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(prefix)]


def test_runs_plan_prints_the_left_out_problem_and_kept_counts(client: MagicMock) -> None:
    """The three summary lines follow the counts by action."""
    _with_report(
        client,
        problem_entries=[DROPDOWN_PROBLEM],
        left_out=6,
        left_out_with_problem=1,
        left_out_dependent=5,
        left_out_by_kind={"LocationSite": 1, "InterfacePhysical": 5},
        problems=1,
        problems_by_type={"dropdown_value_not_in_choices": 1},
        kept=0,
    )

    result = _invoke(client, "runs", "plan", RUN_ID)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "left_out: 6 (with a problem: 1, depending on one: 5)" in lines
    assert "problems: 1" in lines
    assert "kept_without_relationship: 0" in lines
    assert lines.index("left_out: 6 (with a problem: 1, depending on one: 5)") < lines.index("problems: 1")
    assert not _lines_starting(result.output, "deletes not computed for")
    assert "Dropdown values were not checked" not in result.output


def test_runs_plan_prints_withheld_deletes_and_unchecked_dropdown_values(client: MagicMock) -> None:
    """One line per kind whose deletes are not computed, and one when the run had no schema snapshot."""
    _with_report(
        client,
        problem_entries=[],
        left_out=1,
        left_out_with_problem=1,
        left_out_by_kind={"IpamVRF": 1},
        problems=1,
        problems_by_type={"transform_failed": 1},
        deletes_withheld_by_kind={"IpamVRF": 1},
        dropdown_values_checked=False,
    )

    result = _invoke(client, "runs", "plan", RUN_ID)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "deletes not computed for IpamVRF: 1 records without identity" in lines
    assert "Dropdown values were not checked: no destination schema snapshot in this run" in lines


def test_plan_prints_the_first_20_problems_and_the_command_for_the_rest(client: MagicMock) -> None:
    """25 problems: the first 20 are printed, then `infrahub-sync runs plan <run_id> --problems`."""
    problems = [_problem(index) for index in range(25)]
    _with_report(
        client,
        problem_entries=problems,
        left_out=25,
        left_out_with_problem=25,
        left_out_by_kind={"DcimDevice": 25},
        problems=25,
        problems_by_type={"wrong_value_type": 25},
    )

    result = _invoke(client, "plan", "--config", "edge-sync", "--version", "1", "--reason", "review")

    assert result.exit_code == 0, result.output
    printed = _lines_starting(result.output, "wrong_value_type  DcimDevice  ")
    assert [line.split("  ")[2] for line in printed] == [f"name=rtr{index:02d}" for index in range(20)]
    assert f"infrahub-sync runs plan {RUN_ID} --problems" in result.output


def test_runs_plan_problems_reads_every_page(client: MagicMock) -> None:
    """`--problems` prints one line per problem across pages of two entries."""
    problems = [_problem(index) for index in range(5)]
    _with_report(
        client, problem_entries=problems, page_size=PAGE_SIZE, problems=5, problems_by_type={"wrong_value_type": 5}
    )

    result = _invoke(client, "runs", "plan", RUN_ID, "--problems")

    assert result.exit_code == 0, result.output
    printed = _lines_starting(result.output, "wrong_value_type  DcimDevice  ")
    assert [line.split("  ")[2] for line in printed] == [f"name=rtr{index:02d}" for index in range(5)]
    assert client.get_plan_problems.call_count == 3


def test_runs_plan_problems_filters_by_type_and_kind(client: MagicMock) -> None:
    """`--type` and `--kind` reach the paged read as `problem_type` and `kind`."""
    problems = [DROPDOWN_PROBLEM, *(_problem(index) for index in range(3))]
    _with_report(client, problem_entries=problems, problems=4)

    by_type = _invoke(client, "runs", "plan", RUN_ID, "--problems", "--type", "dropdown_value_not_in_choices")
    by_kind = _invoke(client, "runs", "plan", RUN_ID, "--problems", "--kind", "DcimDevice")

    assert by_type.exit_code == 0, by_type.output
    assert _lines_starting(by_type.output, "dropdown_value_not_in_choices  LocationSite  name=berlin  ")
    assert not _lines_starting(by_type.output, "wrong_value_type")
    assert by_kind.exit_code == 0, by_kind.output
    assert len(_lines_starting(by_kind.output, "wrong_value_type  DcimDevice  ")) == 3
    assert not _lines_starting(by_kind.output, "dropdown_value_not_in_choices")
    calls = [call.kwargs for call in client.get_plan_problems.call_args_list]
    assert {"problem_type": "dropdown_value_not_in_choices"}.items() <= calls[0].items()
    assert any(call.get("kind") == "DcimDevice" for call in calls)


def test_runs_plan_left_out_and_kept_print_one_line_per_entry(client: MagicMock) -> None:
    """`--left-out` prints the cause, kind and identity of each record; `--kept` each kept record."""
    _with_report(
        client,
        problem_entries=[DROPDOWN_PROBLEM],
        left_out_entries=LEFT_OUT,
        kept_entries=KEPT,
        page_size=PAGE_SIZE,
        left_out=3,
        left_out_with_problem=1,
        left_out_dependent=2,
        problems=1,
        kept=1,
    )

    left_out = _invoke(client, "runs", "plan", RUN_ID, "--left-out")
    dependents = _invoke(client, "runs", "plan", RUN_ID, "--left-out", "--kind", "InterfacePhysical")
    kept = _invoke(client, "runs", "plan", RUN_ID, "--kept")

    assert left_out.exit_code == 0, left_out.output
    assert _lines_starting(left_out.output, "has_problem  LocationSite  name=berlin")
    assert len(_lines_starting(left_out.output, "depends_on_left_out  InterfacePhysical  ")) == 2
    assert dependents.exit_code == 0, dependents.output
    assert not _lines_starting(dependents.output, "has_problem")
    assert len(_lines_starting(dependents.output, "depends_on_left_out  InterfacePhysical  ")) == 2
    assert kept.exit_code == 0, kept.output
    assert _lines_starting(kept.output, "peer_not_found_in_source  IpamIPAddress  address=10.0.0.9/32")


@pytest.mark.parametrize(
    "options",
    [
        ("--problems", "--left-out"),
        ("--problems", "--kept"),
        ("--left-out", "--kept"),
        ("--problems", "--detail"),
        ("--type", "wrong_value_type"),
        ("--left-out", "--type", "wrong_value_type"),
    ],
)
def test_the_list_options_are_exclusive_and_type_needs_problems(client: MagicMock, options: tuple[str, ...]) -> None:
    """Two lists, a list with `--detail`, or `--type` without `--problems` is a client input error."""
    _with_report(client, problem_entries=[])

    result = _invoke(client, "runs", "plan", RUN_ID, *options)

    assert result.exit_code == 2
    assert "error: client-input" in result.output
