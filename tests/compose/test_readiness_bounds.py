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

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest

from tests.compose import lifecycle as lifecycle_module
from tests.compose.conftest import (
    FIXTURE_PROCESS_CUSHION_SECONDS,
    FIXTURE_READY_SECONDS,
    readiness_times,
    start_command,
)
from tests.compose.lifecycle import PROCESS_CUSHION_SECONDS, READY_TIMEOUT_SECONDS, Deployment

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

DECLARED_BOUND_SECONDS = 420
# The slowest compose test of the 20 measured iterations (plan.md, "Lifecycle
# failure causes", run 38806056523): 220.5 s on amd64. It includes the session's
# first cold `up` and its fixture setup, so every single `up` took less.
MEASURED_SLOWEST_SECONDS = 220.5
# The bound must stay at least half as long again as the slowest measurement.
READINESS_MARGIN = 1.5


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

    @contextmanager
    def watched(project: str) -> Iterator[None]:
        recorded["watched"] = project
        yield
        recorded["watched_after"] = "argv" in recorded

    monkeypatch.setattr(lifecycle_module, "compose", record)
    monkeypatch.setattr(lifecycle_module, "readiness_recorded", watched)
    environment = tmp_path / ".env"
    environment.write_text("VERSION=bounds\n", encoding="utf-8")

    Deployment(instance="bounds", environment_file=environment).up("sync-api", "sync-worker")

    assert flag(recorded["argv"], "--wait-timeout") == str(DECLARED_BOUND_SECONDS), recorded["argv"]
    assert recorded["timeout"] > DECLARED_BOUND_SECONDS, recorded["timeout"]
    assert recorded["timeout"] == DECLARED_BOUND_SECONDS + PROCESS_CUSHION_SECONDS, recorded["timeout"]
    # The per-service readiness times are recorded around the very `up` that waits.
    assert recorded["watched"] == "infrahub-sync-bounds"
    assert recorded["watched_after"] is True


@pytest.mark.parametrize("bound", [READY_TIMEOUT_SECONDS, FIXTURE_READY_SECONDS])
def test_the_bound_keeps_its_margin_over_the_slowest_measured_start(bound: int) -> None:
    """The bound comes from the measurement: the slowest time, half as long again, fits under it.

    A bound lowered below that margin turns an ordinary slow cold start into a
    failure. Lowering it is a change to the measured figure first, made from the
    per-service times the tier now records.
    """
    assert bound >= MEASURED_SLOWEST_SECONDS * READINESS_MARGIN


# Each line is what `docker events` prints in the suite's format: the time in
# nanoseconds, the action, the Compose service, and the exit code of a `die`.
EVENT_LINES = (
    "101000000000\tstart\tpostgres\t",
    "113500000000\thealth_status: healthy\tpostgres\t",
    "114000000000\tstart\tsync-bootstrap\t",
    "150000000000\tdie\tsync-bootstrap\t0",
    "151000000000\tstart\tsync-api\t",
    "152000000000\thealth_status: unhealthy\tsync-api\t",
    "160000000000\thealth_status: healthy\tobject-store\t",
    "not an event line",
    "",
)


def test_each_service_time_to_healthy_is_read_from_the_events_of_one_up() -> None:
    """One line per service, sorted, each saying how far its start got and when."""
    assert readiness_times("\n".join(EVENT_LINES), began=100.0) == [
        "object-store: healthy 60.0 s after up began; its container was already running",
        "postgres: healthy 12.5 s after its container started, 13.5 s after up began",
        "sync-api: started 51.0 s after up began; no healthy status during up, or no health check",
        "sync-bootstrap: exited with code 0 36.0 s after its container started, 50.0 s after up began",
    ]


def test_no_events_means_no_service_lines() -> None:
    """An `up` that started nothing new records nothing per service."""
    assert readiness_times("", began=100.0) == []
