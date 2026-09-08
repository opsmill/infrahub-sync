"""Under qualification, a skipped mandatory case is a failure.

Every row of the lifecycle matrix is required. Run directly, the Docker-backed
cases skip where there is no daemon, no built candidate, or no host that can
execute a pinned Linux binary, which is what keeps the suite usable on a
developer's machine. Run as the qualification claim, that same skip is the claim
quietly not being made, and it exits zero.

So the qualification command passes one option, and the option makes the hook
turn a skipped `compose`-marked report into a failed one. What is driven below is
that hook, and what is asserted is the report it hands back: nothing here reads
what pytest printed, and nothing passes if the hook stops changing the report.
"""

from __future__ import annotations

import ast
from typing import Literal

import pytest

from tasks import compose
from tasks.compose import ZERO_SKIP_OPTION, qualification_command
from tasks.image import ImageTaskError
from tests.compose.conftest import REPO_ROOT, pytest_runtest_makereport

NODE_ID = "tests/compose/test_lifecycle.py::test_a_mandatory_case"

LIFECYCLE_SUITE = REPO_ROOT / "tests" / "compose" / "test_lifecycle.py"
BUSY_CASE = "test_a_busy_worker_is_still_ready_at_the_deployment_level"

# The outcomes a report can carry, as pytest types them.
Outcome = Literal["passed", "failed", "skipped"]


class _Config:
    def __init__(self, *, zero_skip: bool) -> None:
        self._zero_skip = zero_skip

    def getoption(self, name: str) -> bool:
        assert name == ZERO_SKIP_OPTION, name
        return self._zero_skip


class _Item:
    """What the hook asks of a test item, and nothing else.

    The stash is real rather than a stand-in: the hook records the call phase's
    outcome there for a teardown to read, and a double that could not hold it
    would pass a hook that never wrote one.
    """

    nodeid = NODE_ID

    def __init__(self, *, marked: bool, zero_skip: bool) -> None:
        self._marked = marked
        self.config = _Config(zero_skip=zero_skip)
        self.stash = pytest.Stash()

    def get_closest_marker(self, name: str) -> object | None:
        # The hook only asks whether there is one, so a stand-in is enough.
        return object() if self._marked and name == "compose" else None


def report_for(outcome: Outcome, item: _Item) -> pytest.TestReport:
    """Put one report through the real hook and return what it hands back."""
    report = pytest.TestReport(
        nodeid=NODE_ID, location=("", None, ""), keywords={}, outcome=outcome, longrepr=None, when="call"
    )
    wrapper = pytest_runtest_makereport(item, None)  # ty: ignore[invalid-argument-type]
    next(wrapper)
    try:
        wrapper.send(report)
    except StopIteration as finished:
        return finished.value
    pytest.fail("the hook wrapper did not finish")


def test_a_skipped_matrix_case_under_the_option_comes_back_failed() -> None:
    """The delivered property: the report itself changes, so the exit code does."""
    report = report_for("skipped", _Item(marked=True, zero_skip=True))

    assert report.outcome == "failed"
    assert NODE_ID in str(report.longrepr)


@pytest.mark.parametrize(
    ("outcome", "item"),
    [
        ("skipped", _Item(marked=True, zero_skip=False)),
        ("skipped", _Item(marked=False, zero_skip=True)),
        ("passed", _Item(marked=True, zero_skip=True)),
    ],
    ids=["no-option", "outside-the-matrix", "not-a-skip"],
)
def test_every_other_report_comes_back_untouched(outcome: Outcome, item: _Item) -> None:
    """Ordinary Docker and platform skips survive, and so does everything else.

    Without these the hook could refuse every skip it sees and still pass the
    case above, which would make the suite unusable anywhere but the qualified
    host.
    """
    report = report_for(outcome, item)

    assert report.outcome == outcome
    assert report.longrepr is None


def test_the_qualification_command_passes_the_option() -> None:
    """An option nothing passes is an option the qualification claim does not have."""
    assert ZERO_SKIP_OPTION in qualification_command()


@pytest.mark.parametrize("record", [None, []])
def test_a_malformed_top_level_candidate_record_requests_a_rebuild(
    monkeypatch: pytest.MonkeyPatch, record: object
) -> None:
    """JSON permits roots that cannot hold the build's digest record."""
    monkeypatch.setattr(compose, "read_digests", lambda: record)

    with pytest.raises(ImageTaskError, match=r"stale or incomplete.*image\.build"):
        compose.candidate_reference()


@pytest.mark.parametrize("entry", [None, [], {}, {"manifest": "sha256:" + "1" * 64}])
def test_an_incomplete_candidate_record_requests_a_rebuild(monkeypatch: pytest.MonkeyPatch, entry: object) -> None:
    """A damaged build record fails in the task's own error family."""
    monkeypatch.setattr(compose, "read_digests", lambda: {"platforms": {"linux/amd64": entry}})

    with pytest.raises(ImageTaskError, match=r"stale or incomplete.*image\.build"):
        compose.candidate_reference()


def test_a_complete_candidate_record_returns_its_configuration_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """The validation keeps the immutable local-reference handoff unchanged."""
    digest = "sha256:" + "1" * 64
    monkeypatch.setattr(compose, "read_digests", lambda: {"platforms": {"linux/amd64": {"config": digest}}})

    assert compose.candidate_reference() == digest


def test_the_hook_records_whether_the_call_phase_failed() -> None:
    """A failed run's diagnostic is written in teardown, which reads what this leaves."""
    from tests.compose.conftest import FAILED

    item = _Item(marked=True, zero_skip=False)
    report_for("failed", item)

    assert item.stash[FAILED] is True


def busy_case() -> ast.FunctionDef:
    """Return the lifecycle matrix's busy-state case, as source."""
    tree = ast.parse(LIFECYCLE_SUITE.read_text(encoding="utf-8"))
    found = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == BUSY_CASE]
    assert len(found) == 1, f"{LIFECYCLE_SUITE.name} defines {len(found)} cases named {BUSY_CASE}"
    return found[0]


def test_the_busy_case_holds_the_queue_it_reads_instead_of_catching_it() -> None:
    """A case that can pass or fail on a race is the claim not reliably made.

    `service.py` derives `busy` from a positive scheduled queue depth, so the
    state ends the moment a worker claims. Waiting to catch it made the verdict
    depend on losing that race -- a plan taken before the first sample reported
    `{'ready'}`. The case prevents the claim for the observation instead, which
    is the same reason the checkout-free row holds a write guard.
    """
    rendered = ast.unparse(busy_case())

    assert "docker(['pause', worker])" in rendered, "the busy case reads a queue depth it did nothing to hold"
    assert rendered.index("docker(['pause', worker])") < rendered.index("observe_worker_states()")
    # In a `finally`, so a failed assertion cannot leave the deployment's only
    # worker paused for every case that runs after this one.
    released = [
        node
        for node in ast.walk(busy_case())
        if isinstance(node, ast.Try)
        for statement in node.finalbody
        if "unpause" in ast.unparse(statement)
    ]
    assert released, "the busy case can strand a paused worker"
