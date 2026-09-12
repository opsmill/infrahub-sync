"""The declared readiness bound belongs to Compose, not to the process running it.

Two timeouts are in play whenever this suite starts something, and they are not
interchangeable. `--wait-timeout` is Compose's own decision that a deployment did
not come up: it stops waiting, reports which service is still unhealthy, and
exits non-zero. A subprocess timeout is the harness giving up on the *command*:
it kills Compose part-way through, leaves the deployment in whatever state it
had reached, and produces an exception that says nothing about readiness.

Declaring 420 seconds and then only enforcing it as a subprocess timeout would
mean the second thing while reading like the first. So the bound is passed to
Compose, and the process timeout exists only to let Compose's own answer be
reported and the process reaped.

These need no daemon: the command is built in one place and the call site is
observed with the runner replaced.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.compose import lifecycle as lifecycle_module
from tests.compose.conftest import FIXTURE_PROCESS_CUSHION_SECONDS, FIXTURE_READY_SECONDS, start_command
from tests.compose.lifecycle import PROCESS_CUSHION_SECONDS, READY_TIMEOUT_SECONDS, Deployment

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

DECLARED_BOUND_SECONDS = 420


def flag(argv: Sequence[str], name: str) -> str:
    """Return the value that follows one flag, or '' when it is absent."""
    listed = list(argv)
    return listed[listed.index(name) + 1] if name in listed else ""


@pytest.mark.parametrize("bound", [30, DECLARED_BOUND_SECONDS])
def test_the_waited_start_hands_its_bound_to_compose(bound: int) -> None:
    """Whatever the bound is, Compose is the one holding it."""
    argv = start_command(("sync-api",), bound=bound)

    assert "--wait" in argv, argv
    assert flag(argv, "--wait-timeout") == str(bound), argv


def test_both_declared_bounds_are_the_420_seconds_the_plan_states() -> None:
    """The number is declared once per stack and is the same number in both."""
    assert READY_TIMEOUT_SECONDS == DECLARED_BOUND_SECONDS
    assert FIXTURE_READY_SECONDS == DECLARED_BOUND_SECONDS


@pytest.mark.parametrize(
    ("bound", "cushion"),
    [
        (READY_TIMEOUT_SECONDS, PROCESS_CUSHION_SECONDS),
        (FIXTURE_READY_SECONDS, FIXTURE_PROCESS_CUSHION_SECONDS),
    ],
)
def test_the_process_cushion_only_covers_compose_exiting(bound: int, cushion: int) -> None:
    """It has to exceed the bound, and by an amount that is plainly not a wait.

    Below the bound it would pre-empt Compose's answer. Far above it, it stops
    being a cushion and becomes a second, longer readiness rule that nothing
    declared.
    """
    assert cushion > 0
    assert cushion < bound
    assert bound + cushion > bound


def test_the_deployment_start_uses_the_declared_bound_and_a_cushioned_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The call site, observed: it is the bound that reaches Compose.

    Dropping `--wait-timeout` and relying on the process timeout alone fails
    here, and so does letting the process timeout become the shorter of the two.
    """
    recorded: dict[str, Any] = {}

    def record(argv: Sequence[str], **keywords: Any) -> object:  # noqa: ANN401 -- it stands in for the runner
        recorded["argv"] = list(argv)
        recorded["timeout"] = keywords.get("timeout")
        return None

    monkeypatch.setattr(lifecycle_module, "compose", record)
    environment = tmp_path / "operator.env"
    environment.write_text("INFRAHUB_SYNC_INSTANCE=bounds\n", encoding="utf-8")

    Deployment(instance="bounds", environment_file=environment).up("sync-api", "sync-worker")

    assert flag(recorded["argv"], "--wait-timeout") == str(DECLARED_BOUND_SECONDS), recorded["argv"]
    assert recorded["timeout"] > DECLARED_BOUND_SECONDS, recorded["timeout"]
    assert recorded["timeout"] == DECLARED_BOUND_SECONDS + PROCESS_CUSHION_SECONDS, recorded["timeout"]
