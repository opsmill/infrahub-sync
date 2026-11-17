"""`netbox.netbox_state` and `netbox.ensure_netbox`: what the `preview` preset does to the local NetBox.

Docker is replaced by a fake runner, so no daemon is needed.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from invoke import Context

from tasks import netbox

VALUES = {"COMPOSE_PROJECT_NAME": "netbox-dev"}


class _Result:
    def __init__(self, stdout: str = "", *, ok: bool = True) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.ok = ok


class FakeNetboxDocker:
    """Answers the three probes of `netbox_state`: running containers, any containers, volumes."""

    def __init__(self, *, running: str = "", containers: str = "", volumes: str = "", ok: bool = True) -> None:
        self.running = running
        self.containers = containers
        self.volumes = volumes
        self.ok = ok
        self.commands: list[str] = []

    def run(self, command: str, **_kwargs: Any) -> _Result:  # noqa: ANN401 -- Invoke's keyword arguments
        self.commands.append(command)
        if command.startswith("docker ps --quiet"):
            return _Result(self.running, ok=self.ok)
        if command.startswith("docker ps --all --quiet"):
            return _Result(self.containers, ok=self.ok)
        if command.startswith("docker volume ls"):
            return _Result(self.volumes, ok=self.ok)
        return _Result()


def _context(fake: FakeNetboxDocker, monkeypatch: pytest.MonkeyPatch) -> Context:
    context = Context()
    monkeypatch.setattr(context, "run", fake.run)
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    return context


@pytest.mark.parametrize(
    ("fake", "state"),
    [
        (FakeNetboxDocker(), "absent"),
        (FakeNetboxDocker(volumes="netbox-dev_netbox-postgres-data"), "stopped"),
        (FakeNetboxDocker(containers="netbox-id"), "stopped"),
        (FakeNetboxDocker(running="netbox-id", containers="netbox-id"), "running"),
    ],
    ids=["nothing", "volume-only", "stopped-container", "running"],
)
def test_netbox_state_reads_containers_and_volumes(
    fake: FakeNetboxDocker, state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert netbox.netbox_state(_context(fake, monkeypatch), VALUES) == state
    assert all("label=com.docker.compose.project=netbox-dev" in command for command in fake.commands)


def test_netbox_state_refuses_when_docker_does_not_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(netbox.NetboxError, match="Docker did not answer"):
        netbox.netbox_state(_context(FakeNetboxDocker(ok=False), monkeypatch), VALUES)


@pytest.mark.parametrize(
    ("fake", "expected"),
    [
        (FakeNetboxDocker(running="netbox-id", containers="netbox-id"), ["ready"]),
        (FakeNetboxDocker(volumes="netbox-dev_netbox-postgres-data"), ["start"]),
        (FakeNetboxDocker(), ["fetch", "restore", "ready"]),
    ],
    ids=["running-is-kept", "existing-data-is-started", "absent-gets-the-demo-dataset"],
)
def test_ensure_netbox_never_replaces_existing_data(
    fake: FakeNetboxDocker, expected: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    monkeypatch.setattr(netbox, "_ready", lambda *_args: events.append("ready"))
    monkeypatch.setattr(netbox, "_start_netbox", lambda *_args: events.append("start"))
    monkeypatch.setattr(netbox, "fetch_demo_sql", lambda: events.append("fetch") or Path("demo.sql"))
    monkeypatch.setattr(netbox, "restore_demo_database", lambda *_args: events.append("restore"))

    netbox.ensure_netbox(_context(fake, monkeypatch), VALUES)

    assert events == expected
