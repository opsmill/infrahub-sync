"""Stop failures keep their evidence and do not poison the shared deployment."""

from __future__ import annotations

import inspect
import subprocess  # noqa: S404 -- constructing captured test results only
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from tests.compose import conftest, lifecycle, test_lifecycle
from tests.compose.conftest import DIAGNOSTIC_SAVED, FAILED
from tests.compose.lifecycle import Deployment
from tests.compose.redaction import SECRETS, Captured

if TYPE_CHECKING:
    from collections.abc import Sequence


def captured(stdout: str, *, returncode: int = 0, stderr: str = "") -> Captured:
    """Build a redacted command result without running a subprocess."""
    return Captured(subprocess.CompletedProcess([], returncode, stdout, stderr))


def test_container_evidence_includes_one_offs_and_states_without_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Query by project, so Compose-hidden helpers remain attributable."""
    deployment = Deployment(instance="evidence", environment_file=tmp_path / "operator.env")
    commands: list[list[str]] = []

    def docker(argv: Sequence[str], **_kwargs: object) -> Captured:
        commands.append(list(argv))
        if argv[0] == "ps":
            return captured("service-id\nhelper-id\n")
        return captured('{"id":"helper-id","status":"removing","running":true}\n')

    monkeypatch.setattr(lifecycle, "docker", docker)
    report = lifecycle.container_states(deployment)

    assert "helper-id" in report
    assert '"status":"removing"' in report
    assert "--all" in commands[0]
    assert "label=com.docker.compose.project=infrahub-sync-evidence" in commands[0]
    assert commands[1][-2:] == ["service-id", "helper-id"]
    format_string = commands[1][2]
    assert ".Config.Labels" in format_string
    assert ".State.Restarting" in format_string
    assert ".State.FinishedAt" in format_string
    assert ".Config.Env" not in format_string
    assert ".State.Health" not in format_string


def test_a_disappearing_helper_keeps_other_rows_and_the_inspection_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deployment = Deployment(instance="evidence", environment_file=tmp_path / "operator.env")
    answers = iter(
        [captured("service-id\nhelper-id\n"), captured("service state\n", returncode=1, stderr="no helper-id")]
    )
    monkeypatch.setattr(lifecycle, "docker", lambda *_args, **_kwargs: next(answers))

    report = lifecycle.container_states(deployment)

    assert "service state" in report
    assert "no helper-id" in report


def test_diagnostics_sweep_observations_and_capture_dependency_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deployment = Deployment(instance="evidence", environment_file=tmp_path / "operator.env")
    planted = "canary-diagnostic-label-secret"
    SECRETS.register(planted)
    services: list[str] = []
    monkeypatch.setattr(lifecycle, "container_states", lambda _deployment: captured(f"label={planted}").output)

    def logs(*names: str, tail: int) -> Captured:
        services.extend(names)
        assert tail == 200
        return captured(f"{names[0]}: {planted}")

    monkeypatch.setattr(deployment, "logs", logs)
    destination = tmp_path / "diagnostic.log"
    lifecycle.write_diagnostic(deployment, destination, named={}, observations=("before stop: running",))
    report = destination.read_text(encoding="utf-8")

    assert report.startswith("before stop: running")
    assert planted not in report
    assert "[redacted]" in report
    assert {"postgres", "prefect-server", "sync-bootstrap", "sync-api", "sync-worker"} <= set(services)

    destination.unlink()
    result = lifecycle.write_diagnostic(deployment, destination, named={}, observations=(planted,))
    assert result.startswith("diagnostic withheld")
    assert not destination.exists()


@pytest.mark.parametrize("outcome", ["ready", "degraded", "start-refused", "timed-out", "diagnostic-timeout"])
def test_stop_failure_is_saved_before_recovery_and_never_overwritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str
) -> None:
    """Run the recovery teardown and the general failure hook in their real order."""
    deployment = Deployment(instance="recovery", environment_file=tmp_path / "operator.env", bundle=tmp_path)
    node = SimpleNamespace(stash=pytest.Stash(), name="failed_stop", funcargs={"started": deployment})
    node.stash[FAILED] = True
    sections: list[str] = []
    node.add_report_section = lambda _when, _name, text: sections.append(text)
    request = SimpleNamespace(node=node)
    events: list[str] = []
    monkeypatch.setattr(test_lifecycle, "DIAGNOSTIC_DIR", tmp_path)
    monkeypatch.setattr(test_lifecycle, "generated_credentials", lambda _bundle: {})

    def save(*_args: object, **kwargs: object) -> str:
        events.append("saved")
        assert kwargs["observations"] == ["after stop: helper running"]
        if outcome == "diagnostic-timeout":
            raise subprocess.TimeoutExpired(["logs"], timeout=300)
        return "diagnostic saved"

    def entry_point(_bundle: Path, command: str) -> Captured:
        events.append(command)
        assert node.stash[DIAGNOSTIC_SAVED]
        if outcome == "timed-out":
            raise subprocess.TimeoutExpired(["start"], timeout=900)
        return captured(
            "READY\n" if outcome in {"ready", "diagnostic-timeout"} else "DEGRADED\n",
            returncode=int(outcome == "start-refused"),
        )

    monkeypatch.setattr(test_lifecycle, "write_diagnostic", save)
    monkeypatch.setattr(test_lifecycle, "entry_point", entry_point)
    monkeypatch.setattr(deployment, "down", lambda **_kwargs: events.append("down"))
    recovery = inspect.unwrap(test_lifecycle.stop_observations)(deployment, request)
    next(recovery).append("after stop: helper running")
    if outcome in {"ready", "diagnostic-timeout"}:
        with pytest.raises(StopIteration):
            next(recovery)
        assert events == ["saved", "start", "status"]
    else:
        with pytest.raises(pytest.exit.Exception, match="could not be restored"):
            next(recovery)
        assert events == (["saved", "start", "status", "down"] if outcome == "degraded" else ["saved", "start", "down"])
    assert sections == (
        ["diagnostic unavailable: evidence collection failed before recovery"]
        if outcome == "diagnostic-timeout"
        else ["diagnostic saved"]
    )

    # The general hook runs later. A second write would erase the stopped state.
    monkeypatch.setattr(lifecycle, "write_diagnostic", lambda *_args, **_kwargs: pytest.fail("evidence overwritten"))
    hook = inspect.unwrap(conftest._diagnostic_on_failure)(request)
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)


def test_a_passing_stop_case_needs_no_failure_recovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    node = SimpleNamespace(stash=pytest.Stash())
    node.stash[FAILED] = False
    request = SimpleNamespace(node=node)
    deployment = Deployment(instance="recovery", environment_file=tmp_path / "operator.env")
    monkeypatch.setattr(test_lifecycle, "entry_point", lambda *_args: pytest.fail("unexpected recovery"))
    recovery = inspect.unwrap(test_lifecycle.stop_observations)(deployment, request)
    next(recovery)
    with pytest.raises(StopIteration):
        next(recovery)
