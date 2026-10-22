"""The `dev` namespace: the settings it reads from Compose and the commands it runs.

`docker compose config --format json` and every other Docker command are replaced by a
fake runner, so no daemon is needed. The fake answers `config` with the shape Compose
prints for the development file (FR-018, FR-020, research R6).
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, cast

import pytest
from invoke import Context

from tasks import dev

if TYPE_CHECKING:
    from invoke.tasks import Task

ADMIN_TOKEN = "06438eb2-8019-4776-878c-0941b1f1d1ec"  # noqa: S105 -- the development default


def _resolved(*, project: str = "infrahub-sync-dev", infrahub: bool = True) -> dict[str, Any]:
    """What `docker compose config --format json` prints, trimmed to the fields the tasks read."""
    services: dict[str, Any] = {
        "sync-api": {
            "ports": [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 8000, "published": "8030"}],
            "environment": {
                "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS": '{"dev": {"token": "infrahub-sync-dev-token"}}',
                "INFRAHUB_SYNC_SERVICE_WORK_POOL": "infrahub-sync",
            },
        },
        "prefect-server": {
            "ports": [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 4200, "published": "4230"}],
        },
    }
    if infrahub:
        services["infrahub-server"] = {
            "ports": [{"mode": "ingress", "host_ip": "0.0.0.0", "target": 8000, "published": "8091"}],  # noqa: S104
            "environment": {"INFRAHUB_INITIAL_ADMIN_TOKEN": ADMIN_TOKEN},
        }
    return {"name": project, "services": services}


class _Result:
    def __init__(self, stdout: str = "", *, ok: bool = True) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.ok = ok


class FakeDocker:
    """Records every command; answers `config`, `image inspect` and the network probes."""

    def __init__(self, *, label: str | None = None, project: str = "infrahub-sync-dev") -> None:
        self.label = label
        self.project = project
        self.commands: list[str] = []
        self.envs: list[dict[str, str] | None] = []

    def run(self, command: str, **kwargs: Any) -> _Result:  # noqa: ANN401 -- Invoke's keyword arguments
        self.commands.append(command)
        self.envs.append(kwargs.get("env"))
        if command.endswith("config --format json"):
            return _Result(json.dumps(_resolved(project=self.project, infrahub="--profile infrahub" in command)))
        if command.startswith("docker image inspect"):
            return _Result(self.label or "", ok=self.label is not None)
        if command.startswith("docker network inspect"):
            return _Result("lab_default")
        if command.startswith("docker ps"):
            return _Result("worker-id")
        if command.startswith("docker inspect"):
            return _Result("")
        return _Result()

    def compose_commands(self) -> list[str]:
        return [
            command for command in self.commands if command.startswith("docker compose") and "config" not in command
        ]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    monkeypatch.delenv(dev.INFRAHUB_NETWORK_VARIABLE, raising=False)
    monkeypatch.delenv(dev.INFRAHUB_URL_VARIABLE, raising=False)
    monkeypatch.delenv(dev.INFRAHUB_CREDENTIAL_VARIABLE, raising=False)
    monkeypatch.setattr(dev, "load_netbox_env", lambda: {"COMPOSE_PROJECT_NAME": "netbox-absent"})
    monkeypatch.setattr(dev, "attach_dev_worker", lambda *_args, **_kwargs: False)
    return FakeDocker(label=dev.build_inputs_hash())


@pytest.fixture
def context(fake: FakeDocker, monkeypatch: pytest.MonkeyPatch) -> Context:
    context = Context()
    monkeypatch.setattr(context, "run", fake.run)
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    return context


def _body(task_object: object) -> Any:  # noqa: ANN401 -- an Invoke task body
    return cast("Task", task_object).body


def test_dev_settings_reads_the_resolved_development_file(context: Context, fake: FakeDocker) -> None:
    settings = dev.dev_settings(context)

    assert fake.commands == [
        "docker compose --profile sync --profile infrahub -f development/docker-compose.yml config --format json"
    ]
    assert settings.project == "infrahub-sync-dev"
    assert settings.sync_api == dev.Publication("127.0.0.1", "8030")
    assert settings.prefect == dev.Publication("127.0.0.1", "4230")
    assert settings.infrahub == dev.Publication("0.0.0.0", "8091")  # noqa: S104
    assert settings.infrahub_admin_token == ADMIN_TOKEN
    assert settings.sync_api_url == "http://127.0.0.1:8030"
    assert settings.prefect_url == "http://127.0.0.1:4230"
    # Loopback answers a wildcard publication.
    assert settings.infrahub_url == "http://127.0.0.1:8091"
    assert settings.worker_infrahub_url == "http://infrahub-server:8000"
    assert settings.work_pool == "infrahub-sync"
    assert settings.profiles == ("sync", "infrahub")


def test_dev_settings_follows_a_project_name_from_the_shell(context: Context, fake: FakeDocker) -> None:
    fake.project = "s010-dev"

    assert dev.dev_settings(context).project == "s010-dev"


@pytest.mark.parametrize("source", ["option", "variable"])
def test_an_infrahub_network_drops_the_infrahub_profile(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    monkeypatch.setenv(dev.INFRAHUB_CREDENTIAL_VARIABLE, "lab-token")
    if source == "variable":
        monkeypatch.setenv(dev.INFRAHUB_NETWORK_VARIABLE, "lab_default")
        settings = dev.dev_settings(context)
    else:
        settings = dev.dev_settings(context, infrahub_network="lab_default")

    assert "--profile infrahub" not in fake.commands[0]
    assert settings.infrahub is None
    assert settings.infrahub_network == "lab_default"
    assert settings.worker_infrahub_url == "http://infrahub-server:8000"
    assert settings.infrahub_url == "http://infrahub-server:8000"
    assert settings.infrahub_admin_token == "lab-token"  # noqa: S105
    assert settings.profiles == ("sync",)


def test_an_infrahub_network_takes_its_url_from_the_variable(context: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(dev.INFRAHUB_URL_VARIABLE, "http://infrahub.lab.invalid:8000")

    settings = dev.dev_settings(context, infrahub_network="lab_default")

    assert settings.worker_infrahub_url == "http://infrahub.lab.invalid:8000"


def test_a_failed_config_names_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    context = Context()
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda *_args, **_kwargs: _Result("", ok=False))

    with pytest.raises(dev.DevStackError, match="config --format json"):
        dev.dev_settings(context)


@pytest.mark.parametrize(
    ("host_ip", "url"),
    [("", "http://127.0.0.1:8080"), ("172.17.0.1", "http://172.17.0.1:8080"), ("fd00::1", "http://[fd00::1]:8080")],
)
def test_a_publication_url_uses_its_bind_address(host_ip: str, url: str) -> None:
    assert dev.Publication(host_ip, "8080").url == url


def test_dev_build_labels_the_image_with_its_build_inputs(context: Context, fake: FakeDocker) -> None:
    _body(dev.dev_build)(context)

    assert fake.compose_commands() == [
        "docker compose --profile sync --project-name infrahub-sync-dev -f development/docker-compose.yml "
        f"build --build-arg BUILD_INPUTS={dev.build_inputs_hash()} prefect-server"
    ]


def test_dev_start_with_a_current_image_starts_every_profile_without_a_build(
    context: Context, fake: FakeDocker
) -> None:
    _body(dev.dev_start)(context)

    commands = fake.compose_commands()
    assert commands == [
        "docker compose --profile sync --profile infrahub --project-name infrahub-sync-dev "
        "-f development/docker-compose.yml up --detach --wait --wait-timeout 420"
    ]
    # The worker's Infrahub credential defaults to the stack's own Infrahub admin token.
    assert {dev.INFRAHUB_CREDENTIAL_VARIABLE: ADMIN_TOKEN} in fake.envs


def test_dev_start_with_a_stale_image_builds_first(context: Context, fake: FakeDocker) -> None:
    fake.label = "0" * 64

    _body(dev.dev_start)(context)

    commands = fake.compose_commands()
    assert len(commands) == 2
    assert " build --build-arg BUILD_INPUTS=" in commands[0]
    assert " up --detach --wait " in commands[1]


def test_dev_start_keeps_a_credential_the_shell_exports(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(dev.INFRAHUB_CREDENTIAL_VARIABLE, "exported")

    _body(dev.dev_start)(context)

    assert all(not env for env in fake.envs)


def test_dev_start_on_an_infrahub_network_attaches_the_worker(context: Context, fake: FakeDocker) -> None:
    _body(dev.dev_start)(context, infrahub_network="lab_default")

    up = fake.compose_commands()[0]
    assert up.startswith("docker compose --profile sync --project-name infrahub-sync-dev ")
    assert "--profile infrahub" not in up
    assert "docker network connect lab_default worker-id" in fake.commands


def test_dev_restart_recreates_the_sync_containers(context: Context, fake: FakeDocker) -> None:
    _body(dev.dev_restart)(context)

    assert fake.compose_commands() == [
        "docker compose --profile sync --profile infrahub --project-name infrahub-sync-dev "
        "-f development/docker-compose.yml up --detach --wait --wait-timeout 420 --force-recreate --no-deps "
        "sync-bootstrap sync-api sync-worker"
    ]
    assert not any(command.startswith("docker image inspect") for command in fake.commands)


@pytest.mark.parametrize(
    ("task_object", "arguments"),
    [
        (dev.dev_stop, "stop"),
        (dev.dev_destroy, "down --volumes --remove-orphans"),
        (dev.dev_status, "ps --all"),
    ],
)
def test_lifecycle_tasks_select_every_profile(
    context: Context, fake: FakeDocker, task_object: object, arguments: str
) -> None:
    _body(task_object)(context)

    assert fake.compose_commands() == [
        "docker compose --profile sync --profile infrahub --project-name infrahub-sync-dev "
        f"-f development/docker-compose.yml {arguments}"
    ]


def test_dev_destroy_keeps_the_image(context: Context, fake: FakeDocker) -> None:
    _body(dev.dev_destroy)(context)

    assert not any("rmi" in command or "--rmi" in command for command in fake.commands)


def test_dev_logs_tails_one_service(context: Context, fake: FakeDocker) -> None:
    _body(dev.dev_logs)(context, name="sync-worker", lines=20)

    assert fake.compose_commands()[0].endswith(" logs --no-color --tail 20 sync-worker")


def test_dev_smoke_seeds_then_runs_the_suite_against_the_dev_stack(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, ...]] = []
    monkeypatch.setattr(dev, "_infrahub_reachable", lambda _address: True)
    monkeypatch.setattr(
        dev, "load_smoke_dataset", lambda _context, address, token: events.append(("seed", address, token))
    )

    _body(dev.dev_smoke)(context)

    assert events == [("seed", "http://127.0.0.1:8091", ADMIN_TOKEN)]
    assert fake.commands[-1] == dev.SMOKE_COMMAND
    assert fake.envs[-1] == {dev.SMOKE_STACK_VARIABLE: "dev"}


def test_dev_seed_refuses_an_infrahub_on_another_network(context: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(dev.INFRAHUB_NETWORK_VARIABLE, "lab_default")

    with pytest.raises(dev.DevStackError, match="lab_default"):
        _body(dev.dev_seed)(context)


def test_the_dev_namespace_holds_the_contract_tasks() -> None:
    assert set(dev.ns.task_names) == {
        "build",
        "start",
        "restart",
        "stop",
        "destroy",
        "status",
        "logs",
        "seed",
        "smoke",
    }
