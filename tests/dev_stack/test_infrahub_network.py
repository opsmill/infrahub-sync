"""The dev stack's worker joins a local Infrahub's Compose network, so it can write to it.

Infrahub's own Compose file, started with `docker compose -p infrahub`, creates the
`infrahub_default` network, where Infrahub answers at `http://infrahub-server:8000`.
`invoke start` connects the worker (`development/docker-compose.dev.yml`, service
`sync-worker`) to that network, so the same address works on every platform. These cases
check the Docker commands the task runs, with a fake runner and no daemon.
"""

from __future__ import annotations

from contextlib import nullcontext

import pytest
from invoke import Context

from tasks import dev, netbox

DEFAULT_NETWORK = "infrahub_default"


class _Result:
    def __init__(self, stdout: str, *, ok: bool) -> None:
        self.stdout = stdout
        self.ok = ok


class FakeDocker:
    """Answers the read-only Docker queries for one network and records every command."""

    def __init__(self, *, network: str, network_exists: bool, workers: list[str], attached: set[str]) -> None:
        self.network = network
        self.network_exists = network_exists
        self.workers = workers
        self.attached = attached
        self.commands: list[str] = []

    def run(self, command: str, **_kwargs: object) -> _Result:
        self.commands.append(command)
        if command.startswith("docker network inspect"):
            return _Result(self.network, ok=self.network_exists and command.endswith(f" {self.network}"))
        if command.startswith("docker ps"):
            return _Result("\n".join(self.workers), ok=True)
        if command.startswith("docker inspect"):
            container = command.rsplit(" ", 1)[1]
            names = "infrahub-sync-dev_default"
            if container in self.attached:
                names = f"{names} {self.network}"
            return _Result(names, ok=True)
        return _Result("", ok=True)

    def changes(self) -> list[str]:
        return [command for command in self.commands if command.startswith("docker network connect")]


def _context(fake: FakeDocker, monkeypatch: pytest.MonkeyPatch) -> Context:
    context = Context()
    monkeypatch.setattr(context, "run", fake.run)
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    return context


def test_the_worker_writes_to_infrahub_by_its_service_name_and_port() -> None:
    """Infrahub's Compose file names the API service `infrahub-server` and serves port 8000."""
    assert netbox.WORKER_INFRAHUB_URL == "http://infrahub-server:8000"


def test_the_network_defaults_to_the_infrahub_compose_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(dev.INFRAHUB_NETWORK_VARIABLE, raising=False)

    assert dev.infrahub_network() == DEFAULT_NETWORK


def test_an_environment_variable_names_another_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(dev.INFRAHUB_NETWORK_VARIABLE, "lab_default")

    assert dev.infrahub_network() == "lab_default"


def test_attach_connects_a_worker_that_is_not_on_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(dev.INFRAHUB_NETWORK_VARIABLE, raising=False)
    fake = FakeDocker(network=DEFAULT_NETWORK, network_exists=True, workers=["abc"], attached=set())

    assert dev.attach_dev_worker_to_infrahub(_context(fake, monkeypatch)) is True

    assert fake.changes() == [f"docker network connect {DEFAULT_NETWORK} abc"]


def test_attach_leaves_a_connected_worker_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeating `invoke start` must not fail on an existing connection."""
    monkeypatch.delenv(dev.INFRAHUB_NETWORK_VARIABLE, raising=False)
    fake = FakeDocker(network=DEFAULT_NETWORK, network_exists=True, workers=["abc"], attached={"abc"})

    assert dev.attach_dev_worker_to_infrahub(_context(fake, monkeypatch)) is True

    assert fake.changes() == []


@pytest.mark.parametrize(
    ("network_exists", "workers"),
    [(False, ["abc"]), (True, [])],
    ids=["infrahub-not-started", "dev-stack-not-started"],
)
def test_attach_does_nothing_when_either_stack_is_absent(
    monkeypatch: pytest.MonkeyPatch, *, network_exists: bool, workers: list[str]
) -> None:
    monkeypatch.delenv(dev.INFRAHUB_NETWORK_VARIABLE, raising=False)
    fake = FakeDocker(network=DEFAULT_NETWORK, network_exists=network_exists, workers=workers, attached=set())

    assert dev.attach_dev_worker_to_infrahub(_context(fake, monkeypatch)) is False

    assert fake.changes() == []


def test_attach_uses_the_network_the_environment_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(dev.INFRAHUB_NETWORK_VARIABLE, "lab_default")
    fake = FakeDocker(network="lab_default", network_exists=True, workers=["abc"], attached=set())

    assert dev.attach_dev_worker_to_infrahub(_context(fake, monkeypatch)) is True

    assert fake.changes() == ["docker network connect lab_default abc"]
