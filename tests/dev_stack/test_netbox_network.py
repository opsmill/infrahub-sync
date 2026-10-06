"""The dev stack's worker joins the local NetBox's network, so it can read NetBox.

NetBox publishes its port on the host's loopback address only, which a container cannot
reach. The tasks connect the worker (`compose.yaml`, service `sync-worker`) to NetBox's
Compose network instead, where NetBox answers at `http://netbox:8080`. These cases check
the Docker commands the tasks run, with a fake runner and no daemon.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING, cast

import pytest
import yaml
from invoke import Context

from tasks import dev, netbox

if TYPE_CHECKING:
    from pathlib import Path

    from invoke.tasks import Task

VALUES = {
    "COMPOSE_PROJECT_NAME": "netbox-test",
    "NETBOX_PORT": "8082",
    "NETBOX_DB_PASSWORD": "netbox",
    "NETBOX_SECRET_KEY": "secret",
    "NETBOX_TOKEN_PEPPER": "pepper",
    "NETBOX_ADMIN_PASSWORD": "admin",
    "NETBOX_TOKEN_KEY": "devnetboxkey",
    "NETBOX_TOKEN": "devnetboxseedtoken0000000000000000000000",
}
NETWORK = "netbox-test_default"


class _Result:
    def __init__(self, stdout: str, *, ok: bool) -> None:
        self.stdout = stdout
        self.ok = ok


class FakeDocker:
    """Answers the read-only Docker queries from fixed state and records every command."""

    def __init__(self, *, network_exists: bool, workers: list[str], attached: set[str]) -> None:
        self.network_exists = network_exists
        self.workers = workers
        self.attached = attached
        self.commands: list[str] = []

    def run(self, command: str, **_kwargs: object) -> _Result:
        self.commands.append(command)
        if command.startswith("docker network inspect"):
            return _Result(NETWORK, ok=self.network_exists)
        if command.startswith("docker ps"):
            return _Result("\n".join(self.workers), ok=True)
        if command.startswith("docker inspect"):
            container = command.rsplit(" ", 1)[1]
            names = (
                f"infrahub-sync-dev_default {NETWORK}" if container in self.attached else "infrahub-sync-dev_default"
            )
            return _Result(names, ok=True)
        return _Result("", ok=True)

    def changes(self) -> list[str]:
        return [
            command
            for command in self.commands
            if command.startswith(("docker network connect", "docker network disconnect"))
        ]


def _context(fake: FakeDocker, monkeypatch: pytest.MonkeyPatch) -> Context:
    context = Context()
    monkeypatch.setattr(context, "run", fake.run)
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    return context


def test_the_worker_reads_netbox_by_its_service_name_and_container_port() -> None:
    """The service is `netbox` and the image listens on 8080 inside its network."""
    assert netbox.WORKER_NETBOX_URL == "http://netbox:8080"
    assert netbox.netbox_network(VALUES) == NETWORK


def test_the_worker_query_names_the_dev_stack_project_and_service(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDocker(network_exists=True, workers=["abc"], attached=set())
    context = Context()
    monkeypatch.setattr(context, "run", fake.run)

    assert netbox.dev_worker_containers(context) == ["abc"]
    assert "label=com.docker.compose.project=infrahub-sync-dev" in fake.commands[0]
    assert "label=com.docker.compose.service=sync-worker" in fake.commands[0]


def test_attach_connects_a_worker_that_is_not_on_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDocker(network_exists=True, workers=["abc"], attached=set())

    assert netbox.attach_dev_worker(_context(fake, monkeypatch), VALUES) is True

    assert fake.changes() == [f"docker network connect {NETWORK} abc"]


def test_attach_leaves_a_connected_worker_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeating `invoke start` or `netbox.up` must not fail on an existing connection."""
    fake = FakeDocker(network_exists=True, workers=["abc"], attached={"abc"})

    assert netbox.attach_dev_worker(_context(fake, monkeypatch), VALUES) is True

    assert fake.changes() == []


@pytest.mark.parametrize(
    ("network_exists", "workers"),
    [(False, ["abc"]), (True, [])],
    ids=["netbox-not-started", "dev-stack-not-started"],
)
def test_attach_does_nothing_when_either_stack_is_absent(
    monkeypatch: pytest.MonkeyPatch, *, network_exists: bool, workers: list[str]
) -> None:
    fake = FakeDocker(network_exists=network_exists, workers=workers, attached=set())

    assert netbox.attach_dev_worker(_context(fake, monkeypatch), VALUES) is False

    assert fake.changes() == []


def test_detach_disconnects_only_a_connected_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker refuses to remove a network that still has a container connected."""
    fake = FakeDocker(network_exists=True, workers=["abc", "def"], attached={"def"})

    netbox.detach_dev_worker(_context(fake, monkeypatch), VALUES)

    assert fake.changes() == [f"docker network disconnect {NETWORK} def"]


def test_detach_disconnects_workers_from_every_attached_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """Teardown finds default and custom project workers through the NetBox network."""
    fake = FakeDocker(network_exists=True, workers=["default-worker"], attached={"default-worker", "custom-worker"})
    context = _context(fake, monkeypatch)

    def run(command: str, **kwargs: object) -> _Result:
        """Simulate Docker filtering workers by project or attached network."""
        result = fake.run(command, **kwargs)
        if command.startswith("docker ps") and "label=com.docker.compose.project=" not in command:
            assert f"--filter network={NETWORK}" in command
            assert "--filter label=com.docker.compose.service=sync-worker" in command
            return _Result("default-worker\ncustom-worker", ok=True)
        return result

    monkeypatch.setattr(context, "run", run)

    netbox.detach_dev_worker(context, VALUES)

    assert fake.changes() == [
        f"docker network disconnect {NETWORK} default-worker",
        f"docker network disconnect {NETWORK} custom-worker",
    ]


def test_detach_does_nothing_when_no_workers_are_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing an absent network or a network without workers needs no disconnects."""
    fake = FakeDocker(network_exists=False, workers=[], attached=set())

    netbox.detach_dev_worker(_context(fake, monkeypatch), VALUES)

    assert fake.changes() == []


def test_netbox_down_disconnects_the_worker_before_removing_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: VALUES)
    monkeypatch.setattr(netbox, "detach_dev_worker", lambda _context, _values: events.append("detach"))
    monkeypatch.setattr(netbox, "_compose", lambda _context, arguments, _values: events.append(arguments))

    cast("Task", netbox.down).body(Context())

    assert events == ["detach", "down --volumes"]


def test_netbox_up_connects_the_worker_once_netbox_is_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: VALUES)
    monkeypatch.setattr(netbox, "_compose", lambda *_args: events.append("compose"))
    monkeypatch.setattr(netbox, "_wait_for_http", lambda *_args: events.append("wait"))
    monkeypatch.setattr(netbox, "attach_dev_worker", lambda _context, _values: events.append("attach") or True)

    cast("Task", netbox.up).body(Context())

    assert events == ["compose", "wait", "attach"]
    assert "URL from the dev stack's worker: http://netbox:8080" in capsys.readouterr().out


@pytest.mark.parametrize("dataset", ["seed", "demo"])
def test_netbox_seed_disconnects_before_the_reset(monkeypatch: pytest.MonkeyPatch, dataset: str) -> None:
    """Both datasets drop NetBox's containers and network before they load."""
    events: list[str] = []
    monkeypatch.setattr(netbox, "detach_dev_worker", lambda _context, _values: events.append("detach"))
    monkeypatch.setattr(netbox, "_compose", lambda _context, arguments, _values: events.append(arguments))
    monkeypatch.setattr(netbox, "_wait_for_http", lambda *_args: None)
    monkeypatch.setattr(netbox, "prepare_restore_sql", lambda source: source)
    context = Context()
    monkeypatch.setattr(context, "run", lambda *_args, **_kwargs: None)

    if dataset == "seed":
        netbox.reset_database(context, VALUES)
    else:
        netbox.restore_demo_database(context, VALUES, netbox.DEMO_SQL_RESTORE_FILE)

    assert events[:2] == ["detach", "down --volumes"]


def test_invoke_start_reconnects_a_recreated_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """`compose up` recreates the worker when its settings change, which drops the connection."""
    events: list[str] = []
    context = Context()
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda command, **_kwargs: events.append(command.split(" ", 2)[1]))
    monkeypatch.setattr(dev, "load_netbox_env", lambda: VALUES)
    monkeypatch.setattr(dev, "attach_dev_worker", lambda _context, _values, **_kwargs: events.append("attach") or False)

    cast("Task", dev.start).body(context)

    assert events == ["compose", "attach"]


@pytest.mark.parametrize("project", ["", netbox.DEV_STACK_PROJECT, "selected-development"])
def test_pinned_start_attaches_only_the_selected_project_worker(monkeypatch: pytest.MonkeyPatch, project: str) -> None:
    """Worker lookup follows the explicit project while the default remains available."""
    selected = project or netbox.DEV_STACK_PROJECT
    fake = FakeDocker(network_exists=True, workers=[], attached=set())
    context = _context(fake, monkeypatch)

    def run(command: str, **kwargs: object) -> _Result:
        """Return a different worker for the default and selected project filters."""
        result = fake.run(command, **kwargs)
        if command.startswith("docker ps"):
            worker = (
                "selected-worker" if f"label=com.docker.compose.project={selected} " in command else "default-worker"
            )
            return _Result(worker, ok=True)
        return result

    monkeypatch.setattr(context, "run", run)
    monkeypatch.setattr(dev, "load_netbox_env", lambda: VALUES)

    dev._start(context, project=project)

    assert fake.changes() == [f"docker network connect {NETWORK} selected-worker"]
    assert f"label=com.docker.compose.project={selected} " in fake.commands[2]
    if project:
        assert f"--project-name {project} up" in fake.commands[0]


def test_demo_package_for_the_dev_stack_writes_the_worker_addresses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "from-netbox.local.yml"
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: VALUES)
    monkeypatch.setattr(netbox, "load_preview_env", lambda: {"PREVIEW_INFRAHUB_PORT": "8080"})
    monkeypatch.setattr(
        netbox, "preview_urls", lambda values: {"infrahub": f"http://localhost:{values['PREVIEW_INFRAHUB_PORT']}"}
    )
    monkeypatch.setattr(netbox, "STATE_DIR", destination.parent)
    monkeypatch.setattr(netbox, "LOCAL_PACKAGE", destination)

    cast("Task", netbox.demo_package).body(Context(), dev_stack=True)

    written = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert written["configuration"]["source"]["settings"]["url"] == "http://netbox:8080"
    assert written["configuration"]["destination"]["settings"]["url"] == "http://host.docker.internal:8080"
