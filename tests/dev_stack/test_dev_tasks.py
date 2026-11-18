"""The `dev` namespace: the settings it reads from Compose and the commands it runs.

`docker compose config --format json` and every other Docker command are replaced by a
fake runner, so no daemon is needed. The fake answers `config` with the shape Compose
prints for the development file (FR-018, FR-020, research R6).
"""

from __future__ import annotations

import dataclasses
import json
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, cast

import pytest
from invoke import Context

from tasks import dev

if TYPE_CHECKING:
    from invoke.tasks import Task

ADMIN_TOKEN = dev.PUBLIC_INFRAHUB_ADMIN_TOKEN
# What `infrahub-sync demo prepare --json` prints on a first run, trimmed to what `dev.demo` reads.
PREPARED = {
    "schema": "loaded",
    "configuration": "registered",
    "config_id": "cfg-demo-1",
    "version": 1,
    "name": "from-netbox",
}


def _resolved(
    *,
    project: str = "infrahub-sync-dev",
    infrahub: bool = True,
    infrahub_host_ip: str = "127.0.0.1",
    infrahub_token: str = ADMIN_TOKEN,
) -> dict[str, Any]:
    """What `docker compose config --format json` prints, trimmed to the fields the tasks read."""
    services: dict[str, Any] = {
        "sync-api": {
            "ports": [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 8000, "published": "8001"}],
            "environment": {
                "INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD": "infrahub-sync-dev-password",
                "INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN": "infrahub-sync-dev-token",
                "INFRAHUB_SYNC_SERVICE_WORK_POOL": "infrahub-sync",
            },
        },
        "prefect-server": {
            "ports": [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 4200, "published": "4230"}],
        },
    }
    if infrahub:
        services["infrahub-server"] = {
            "ports": [{"mode": "ingress", "host_ip": infrahub_host_ip, "target": 8000, "published": "8091"}],
            "environment": {"INFRAHUB_INITIAL_ADMIN_TOKEN": infrahub_token},
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
        # `_resolved` keyword arguments for `infrahub-server`, such as `infrahub_host_ip`.
        self.infrahub: dict[str, str] = {}
        # The services `ps --services --status running` reports; a running `infrahub-server`
        # makes a task without a preset follow the `infrahub` preset.
        self.running: set[str] = {"infrahub-server"}
        # The services `ps --services --all` reports; None means the running ones.
        self.existing: set[str] | None = None
        # False makes the stack's `ps --services` probes fail, as with no Docker daemon.
        self.probe_ok = True
        # Whether the local NetBox (`netbox-absent` project, see the `fake` fixture) runs.
        self.netbox_running = False
        # False makes the local NetBox probes fail, as when Docker does not answer them.
        self.netbox_probe_ok = True
        # What `infrahub-sync demo prepare --json` prints, and whether it succeeds.
        self.prepare_stdout = json.dumps(PREPARED)
        self.prepare_ok = True
        self.commands: list[str] = []
        self.envs: list[dict[str, str] | None] = []

    def _preset_probe(self, command: str) -> _Result | None:
        """Answer the probes the preset follows: the stack's running services and the local NetBox."""
        if command.endswith("ps --services --status running"):
            return _Result("\n".join(sorted(self.running)), ok=self.probe_ok)
        if command.endswith("ps --services --all"):
            existing = self.running if self.existing is None else self.existing
            return _Result("\n".join(sorted(existing)), ok=self.probe_ok)
        if "label=com.docker.compose.project=netbox-absent" in command:
            running_probe = command.startswith("docker ps --quiet")
            return _Result("netbox-id" if self.netbox_running and running_probe else "", ok=self.netbox_probe_ok)
        return None

    def _prepare_answer(self, command: str) -> _Result | None:
        """Answer `infrahub-sync demo prepare`, which `dev.demo` runs on the host."""
        if command.startswith(dev.DEMO_PREPARE_COMMAND):
            return _Result(self.prepare_stdout, ok=self.prepare_ok)
        return None

    def run(self, command: str, **kwargs: Any) -> _Result:  # noqa: ANN401 -- Invoke's keyword arguments
        self.commands.append(command)
        self.envs.append(kwargs.get("env"))
        probe = self._preset_probe(command) or self._prepare_answer(command)
        if probe is not None:
            return probe
        if command.endswith("config --format json"):
            resolved = _resolved(
                project=self.project,
                infrahub="--profile infrahub" in command,
                **self.infrahub,
            )
            return _Result(json.dumps(resolved))
        if command.startswith("docker image inspect"):
            return _Result(self.label or "", ok=self.label is not None)
        if command.startswith("docker network inspect"):
            return _Result("lab_default")
        if command.startswith("docker ps"):
            return _Result("worker-id")
        # Everything else, `docker inspect` included, prints nothing and succeeds.
        return _Result()

    def compose_commands(self) -> list[str]:
        return [
            command
            for command in self.commands
            if command.startswith("docker compose") and "config" not in command and " ps --services " not in command
        ]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    monkeypatch.delenv(dev.PRESET_VARIABLE, raising=False)
    monkeypatch.delenv(dev.NETBOX_CREDENTIAL_VARIABLE, raising=False)
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
    fake.infrahub["infrahub_host_ip"] = "0.0.0.0"  # noqa: S104 -- reading the settings starts nothing, so it is not refused
    settings = dev.dev_settings(context)

    assert (
        "docker compose --profile sync --profile infrahub -f development/docker-compose.yml config --format json"
    ) in fake.commands
    assert settings.preset.name == "infrahub"
    assert settings.project == "infrahub-sync-dev"
    assert settings.sync_api == dev.Publication("127.0.0.1", "8001")
    assert settings.prefect == dev.Publication("127.0.0.1", "4230")
    assert settings.infrahub == dev.Publication("0.0.0.0", "8091")  # noqa: S104
    assert settings.infrahub_admin_token == ADMIN_TOKEN
    assert settings.sync_api_url == "http://127.0.0.1:8001"
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

    assert "--profile infrahub" not in fake.commands[-1]
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
    monkeypatch.setenv(dev.PRESET_VARIABLE, "infrahub")
    context = Context()
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda *_args, **_kwargs: _Result("", ok=False))

    with pytest.raises(dev.DevStackError, match="config --format json"):
        dev.dev_settings(context)


@pytest.mark.parametrize(
    ("host_ip", "url"),
    [
        ("", "http://127.0.0.1:8080"),
        ("172.17.0.1", "http://172.17.0.1:8080"),
        ("fd00::1", "http://[fd00::1]:8080"),
        # An IPv6-only loopback publication does not answer at 127.0.0.1.
        ("::1", "http://[::1]:8080"),
    ],
)
def test_a_publication_url_uses_its_bind_address(host_ip: str, url: str) -> None:
    assert dev.Publication(host_ip, "8080").url == url


@pytest.mark.parametrize(
    ("admin_token", "label"),
    [
        (dev.API_TOKEN, dev.API_TOKEN),
        (f"{dev.API_TOKEN}-of-my-own", "from ADMIN_ACCOUNT_TOKEN"),
        ("", "from ADMIN_ACCOUNT_TOKEN"),
    ],
    ids=["exact-dev-token", "dev-token-as-substring", "no-token"],
)
def test_the_urls_print_the_dev_bearer_token_only_on_an_exact_match(admin_token: str, label: str) -> None:
    assert dev.bearer_label(admin_token) == label


CUSTOM_TOKEN = "a-token-of-the-developer"  # noqa: S105 -- a test value
LOOPBACK_ADDRESSES = ["127.0.0.1", "127.10.0.3", "::1", "[::1]", "localhost"]
EXPOSED_ADDRESSES = ["", "0.0.0.0", "::", "172.17.0.1", "192.0.2.10", "fd00::1"]  # noqa: S104


@pytest.mark.parametrize("host_ip", LOOPBACK_ADDRESSES)
@pytest.mark.parametrize("token", [ADMIN_TOKEN, "", CUSTOM_TOKEN])
def test_a_loopback_infrahub_is_allowed_with_any_token(host_ip: str, token: str) -> None:
    dev.require_private_infrahub(dev.Publication(host_ip, "8000"), token)


@pytest.mark.parametrize("host_ip", EXPOSED_ADDRESSES)
@pytest.mark.parametrize("token", [ADMIN_TOKEN, "", "  "])
def test_an_exposed_infrahub_is_refused_with_the_public_or_no_token(host_ip: str, token: str) -> None:
    with pytest.raises(dev.DevStackError, match="INFRAHUB_INITIAL_ADMIN_TOKEN") as refused:
        dev.require_private_infrahub(dev.Publication(host_ip, "8000"), token)

    assert "INFRAHUB_API_TOKEN" in str(refused.value)
    assert "dev.destroy" in str(refused.value)
    assert ADMIN_TOKEN not in str(refused.value)


@pytest.mark.parametrize("host_ip", EXPOSED_ADDRESSES)
def test_an_exposed_infrahub_is_allowed_with_a_token_of_its_own(host_ip: str) -> None:
    dev.require_private_infrahub(dev.Publication(host_ip, "8000"), CUSTOM_TOKEN)


def test_an_infrahub_on_another_network_is_not_checked() -> None:
    dev.require_private_infrahub(None, "")


@pytest.mark.parametrize("task_object", [dev.dev_start, dev.dev_deps])
def test_starting_an_exposed_infrahub_with_the_public_token_runs_nothing(
    context: Context, fake: FakeDocker, task_object: object
) -> None:
    fake.infrahub["infrahub_host_ip"] = "0.0.0.0"  # noqa: S104

    with pytest.raises(dev.DevStackError, match="INFRAHUB_BIND_ADDRESS") as refused:
        _body(task_object)(context)

    assert fake.compose_commands() == []
    assert ADMIN_TOKEN not in str(refused.value)


@pytest.mark.parametrize("task_object", [dev.dev_start, dev.dev_deps])
def test_starting_an_exposed_infrahub_with_a_token_of_its_own_proceeds(
    context: Context, fake: FakeDocker, task_object: object
) -> None:
    fake.infrahub["infrahub_host_ip"] = "172.17.0.1"
    fake.infrahub["infrahub_token"] = CUSTOM_TOKEN

    _body(task_object)(context)

    assert any(" up --detach" in command for command in fake.compose_commands())


def test_stopping_an_exposed_infrahub_is_not_refused(context: Context, fake: FakeDocker) -> None:
    fake.infrahub["infrahub_host_ip"] = "0.0.0.0"  # noqa: S104

    _body(dev.dev_stop)(context)

    assert any(command.endswith(" stop") for command in fake.compose_commands())


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
        "infrahub",
        "preview",
        "restart",
        "stop",
        "destroy",
        "status",
        "logs",
        "seed",
        "smoke",
        "deps",
        "api",
        "worker",
        "frontend",
        "demo",
    }


def test_dev_frontend_runs_the_dev_server_against_the_stacks_sync_api(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev, "_frontend_submodule_present", lambda: True)

    _body(dev.dev_frontend)(context)

    assert fake.commands[-2:] == ["pnpm install --frozen-lockfile", "pnpm dev"]
    assert fake.envs[-1] == {dev.FRONTEND_URL_VARIABLE: "http://127.0.0.1:8001"}
    assert not any(command.startswith("git submodule") for command in fake.commands)


def test_dev_frontend_fetches_a_missing_submodule_first(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev, "_frontend_submodule_present", lambda: False)

    _body(dev.dev_frontend)(context)

    assert fake.commands[-3] == f"git submodule update --init {dev.FRONTEND_SUBMODULE}"


def test_dev_frontend_prints_how_to_sign_in_never_a_configured_token(
    context: Context,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    resolve = dev.dev_settings
    monkeypatch.setattr(dev, "dev_settings", lambda ctx: dataclasses.replace(resolve(ctx), admin_token=CUSTOM_TOKEN))
    monkeypatch.setattr(dev, "_frontend_submodule_present", lambda: True)

    _body(dev.dev_frontend)(context)

    output = capsys.readouterr().out
    assert "Sign in on the page as admin, with the password in ADMIN_ACCOUNT_PASSWORD" in output
    assert f"(by default {dev.ADMIN_PASSWORD})" in output
    assert CUSTOM_TOKEN not in output


# ----- Presets ------------------------------------------------------------------------------


def test_without_a_preset_or_running_infrahub_dev_start_runs_the_sync_preset(
    context: Context, fake: FakeDocker
) -> None:
    fake.running = set()

    _body(dev.dev_start)(context)

    assert fake.compose_commands() == [
        "docker compose --profile sync --project-name infrahub-sync-dev "
        "-f development/docker-compose.yml up --detach --wait --wait-timeout 420"
    ]
    # No local Infrahub, so no Infrahub credential defaults to its admin token.
    assert all(not env for env in fake.envs)


def test_the_sync_preset_reaches_an_infrahub_on_this_host(context: Context, fake: FakeDocker) -> None:
    fake.running = set()

    settings = dev.dev_settings(context)

    assert settings.preset.name == "sync"
    assert settings.infrahub is None
    assert not settings.infrahub_network
    assert settings.worker_infrahub_url == dev.HOST_INFRAHUB_URL


def test_the_sync_preset_takes_its_infrahub_url_from_the_variable(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.running = set()
    monkeypatch.setenv(dev.INFRAHUB_URL_VARIABLE, "http://infrahub.lab.invalid:8000")

    assert dev.dev_settings(context).worker_infrahub_url == "http://infrahub.lab.invalid:8000"


@pytest.mark.parametrize("source", ["option", "variable"])
def test_a_named_preset_wins_over_the_running_containers(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    if source == "variable":
        monkeypatch.setenv(dev.PRESET_VARIABLE, "sync")
        _body(dev.dev_start)(context)
    else:
        _body(dev.dev_start)(context, preset="sync")

    assert "--profile infrahub" not in fake.compose_commands()[0]
    # Infrahub from the earlier preset still runs; the task says so and how to stop it.
    assert "dev.stop" in capsys.readouterr().out


def test_the_preset_variable_selects_a_local_infrahub(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.running = set()
    monkeypatch.setenv(dev.PRESET_VARIABLE, "infrahub")

    _body(dev.dev_start)(context)

    assert "--profile sync --profile infrahub " in fake.compose_commands()[0]


def test_an_unknown_preset_is_refused_before_anything_starts(context: Context, fake: FakeDocker) -> None:
    with pytest.raises(dev.DevStackError, match="expected one of sync, infrahub, preview, demo"):
        _body(dev.dev_start)(context, preset="everything")

    assert fake.compose_commands() == []


@pytest.mark.parametrize("preset", ["infrahub", "preview", "demo"])
def test_a_local_infrahub_preset_and_an_infrahub_network_are_refused_together(
    context: Context, fake: FakeDocker, preset: str
) -> None:
    with pytest.raises(dev.DevStackError, match="lab_default"):
        _body(dev.dev_start)(context, preset=preset, infrahub_network="lab_default")

    assert fake.compose_commands() == []


def test_an_infrahub_network_wins_over_a_running_infrahub(context: Context, fake: FakeDocker) -> None:
    assert "infrahub-server" in fake.running

    settings = dev.dev_settings(context, infrahub_network="lab_default")

    assert settings.preset.name == "sync"
    assert settings.worker_infrahub_url == "http://infrahub-server:8000"


@pytest.mark.parametrize(
    ("running", "existing", "expected"),
    [
        (set(), set(), "sync"),
        ({"infrahub-server", "sync-api"}, None, "infrahub"),
        # A stopped stack starts again as it was: `dev.stop`, then a bare `dev.start`.
        (set(), {"infrahub-server", "sync-api"}, "infrahub"),
        # A lighter preset started over a stopped Infrahub stays that preset.
        ({"sync-api"}, {"infrahub-server", "sync-api"}, "sync"),
    ],
    ids=["nothing", "infrahub-running", "stack-stopped", "infrahub-left-stopped"],
)
def test_a_task_without_a_preset_follows_the_stacks_containers(
    context: Context, fake: FakeDocker, running: set[str], existing: set[str] | None, expected: str
) -> None:
    fake.running = running
    fake.existing = existing

    assert dev.dev_settings(context).preset.name == expected


def test_the_containers_never_select_the_preview_preset(context: Context, fake: FakeDocker) -> None:
    # A NetBox started on its own (`netbox.seed`) next to the `infrahub` preset must not make a
    # bare `dev.start` seed Infrahub; the worker still gets the local NetBox's token.
    fake.netbox_running = True

    settings = dev.dev_settings(context)

    assert settings.preset.name == "infrahub"
    assert settings.local_netbox
    assert settings.netbox_credential


def test_a_failed_container_probe_is_refused_rather_than_read_as_no_infrahub(
    context: Context, fake: FakeDocker
) -> None:
    fake.probe_ok = False

    with pytest.raises(dev.DevStackError, match="--preset"):
        _body(dev.dev_restart)(context)

    assert fake.compose_commands() == []


def test_a_failed_netbox_probe_does_not_stop_the_settings(
    context: Context, fake: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    """The NetBox check only sets a credential default; tasks such as `dev.api` still run."""
    fake.netbox_probe_ok = False

    settings = dev.dev_settings(context)

    assert settings.preset.name == "infrahub"
    assert not settings.netbox_credential
    assert "the worker gets no NetBox credential by default" in capsys.readouterr().out


@pytest.mark.parametrize("task_object", [dev.dev_stop, dev.dev_destroy, dev.dev_logs])
def test_tasks_on_every_container_do_not_probe_the_preset(
    context: Context, fake: FakeDocker, task_object: object
) -> None:
    fake.probe_ok = False

    _body(task_object)(context)

    assert not any(" ps --services " in command for command in fake.commands)


def test_dev_restart_while_the_local_netbox_runs_keeps_both_credentials(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.netbox_running = True
    monkeypatch.setattr(dev, "netbox_token", lambda _values: "nbt_dev.token")

    _body(dev.dev_restart)(context)

    assert {
        dev.INFRAHUB_CREDENTIAL_VARIABLE: ADMIN_TOKEN,
        dev.NETBOX_CREDENTIAL_VARIABLE: "nbt_dev.token",
    } in fake.envs


def test_a_bare_dev_start_next_to_a_running_netbox_seeds_nothing(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.netbox_running = True
    calls: list[str] = []
    monkeypatch.setattr(dev, "netbox_token", lambda _values: "nbt_dev.token")
    monkeypatch.setattr(dev, "ensure_netbox", lambda *_args: calls.append("netbox"))
    monkeypatch.setattr(dev, "load_smoke_dataset", lambda *_args: calls.append("seed"))

    _body(dev.dev_start)(context)

    assert calls == []


def test_dev_infrahub_starts_the_infrahub_preset(context: Context, fake: FakeDocker) -> None:
    fake.running = set()

    _body(dev.dev_infrahub)(context)

    assert "--profile sync --profile infrahub " in fake.compose_commands()[0]
    assert {dev.INFRAHUB_CREDENTIAL_VARIABLE: ADMIN_TOKEN} in fake.envs


def test_dev_preview_starts_netbox_then_the_stack_then_seeds_infrahub(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.running = set()
    events: list[str] = []
    monkeypatch.setattr(dev, "netbox_token", lambda _values: "nbt_dev.token")
    monkeypatch.setattr(dev, "ensure_netbox", lambda *_args: events.append("netbox"))
    monkeypatch.setattr(dev, "load_smoke_dataset", lambda _context, address, _token: events.append(f"seed {address}"))
    real_run = fake.run

    def run(command: str, **kwargs: Any) -> _Result:  # noqa: ANN401 -- Invoke's keyword arguments
        if " up --detach" in command:
            events.append("up")
        return real_run(command, **kwargs)

    monkeypatch.setattr(context, "run", run)

    _body(dev.dev_preview)(context)

    assert events == ["netbox", "up", "seed http://127.0.0.1:8091"]
    assert "--profile sync --profile infrahub " in fake.compose_commands()[0]
    assert {
        dev.INFRAHUB_CREDENTIAL_VARIABLE: ADMIN_TOKEN,
        dev.NETBOX_CREDENTIAL_VARIABLE: "nbt_dev.token",
    } in fake.envs


def test_the_preview_preset_keeps_a_netbox_credential_the_shell_exports(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(dev.NETBOX_CREDENTIAL_VARIABLE, "exported")
    monkeypatch.setattr(dev, "ensure_netbox", lambda *_args: None)
    monkeypatch.setattr(dev, "load_smoke_dataset", lambda *_args: None)

    _body(dev.dev_preview)(context)

    assert all(dev.NETBOX_CREDENTIAL_VARIABLE not in (env or {}) for env in fake.envs)


def test_dev_seed_without_a_local_infrahub_names_the_preset_to_start(context: Context, fake: FakeDocker) -> None:
    fake.running = set()

    with pytest.raises(dev.DevStackError, match="--preset infrahub"):
        _body(dev.dev_seed)(context)


# ----- The `demo` preset and `dev.demo` -----------------------------------------------------


def test_the_demo_preset_runs_infrahub_and_netbox_without_the_smoke_dataset() -> None:
    demo = dev.preset_named("demo")

    assert demo.local_infrahub
    assert demo.netbox
    assert not demo.smoke_dataset
    # `preview` is unchanged: the same services, and the smoke dataset.
    preview = dev.preset_named("preview")
    assert (preview.local_infrahub, preview.netbox, preview.smoke_dataset) == (True, True, True)


def test_dev_start_with_the_demo_preset_starts_netbox_and_writes_nothing_into_infrahub(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.running = set()
    events: list[str] = []
    monkeypatch.setattr(dev, "netbox_token", lambda _values: "nbt_dev.token")
    monkeypatch.setattr(dev, "ensure_netbox", lambda *_args: events.append("netbox"))
    monkeypatch.setattr(dev, "load_smoke_dataset", lambda *_args: events.append("seed"))

    _body(dev.dev_start)(context, preset="demo")

    assert events == ["netbox"]
    assert "--profile sync --profile infrahub " in fake.compose_commands()[0]
    assert {
        dev.INFRAHUB_CREDENTIAL_VARIABLE: ADMIN_TOKEN,
        dev.NETBOX_CREDENTIAL_VARIABLE: "nbt_dev.token",
    } in fake.envs


@pytest.fixture
def demo(fake: FakeDocker, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace what `dev.demo` does outside Docker; returns the NetBox and seed events it records."""
    fake.running = set()
    events: list[str] = []
    monkeypatch.delenv(dev.SYNC_TOKEN_VARIABLE, raising=False)
    monkeypatch.setattr(dev, "netbox_token", lambda _values: "nbt_dev.token")
    monkeypatch.setattr(dev, "ensure_netbox", lambda *_args: events.append("netbox"))
    monkeypatch.setattr(dev, "load_smoke_dataset", lambda *_args: events.append("seed"))
    monkeypatch.setattr(dev, "_frontend_submodule_present", lambda: True)
    monkeypatch.setattr(dev, "recorded_dataset", lambda: None)
    return events


def _prepare_call(fake: FakeDocker) -> tuple[str, dict[str, str]]:
    (index,) = [i for i, command in enumerate(fake.commands) if command.startswith(dev.DEMO_PREPARE_COMMAND)]
    return fake.commands[index], fake.envs[index] or {}


def test_dev_demo_runs_demo_prepare_on_the_host_with_the_tokens_in_the_environment(
    context: Context, fake: FakeDocker, demo: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    _body(dev.dev_demo)(context)

    command, env = _prepare_call(fake)
    assert command == (
        "uv run infrahub-sync demo prepare --netbox-url http://netbox:8080 "
        "--infrahub-url http://infrahub-server:8000 --infrahub-address http://127.0.0.1:8091 --json"
    )
    assert env == {
        dev.INFRAHUB_TOKEN_VARIABLE: ADMIN_TOKEN,
        dev.SYNC_URL_VARIABLE: "http://127.0.0.1:8001",
        dev.SYNC_TOKEN_VARIABLE: dev.API_TOKEN,
    }
    assert ADMIN_TOKEN not in command
    assert dev.API_TOKEN not in command
    # The stack starts before the preparation runs, and nothing seeds the smoke dataset.
    assert demo == ["netbox"]
    output = capsys.readouterr().out
    assert "schema: loaded" in output
    assert "configuration: registered" in output
    assert "Web interface  http://127.0.0.1:8001" in output
    assert "uv run infrahubctl branch create netbox-demo-1" in output
    assert (
        'uv run infrahub-sync plan --config-id cfg-demo-1 --version 1 --branch netbox-demo-1 --reason "demo"' in output
    )


@pytest.mark.usefixtures("demo")
def test_dev_demo_with_a_new_netbox_says_it_loaded_the_demo_dataset(
    context: Context, fake: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.netbox_running = False

    _body(dev.dev_demo)(context)

    output = capsys.readouterr().out
    assert "loaded the 'demo' dataset" in output
    assert "netbox.seed --dataset demo" not in output


@pytest.mark.parametrize(
    ("recorded", "expected"),
    [
        (None, "the dataset it holds is not known"),
        ("the 'seed' dataset at tier S", "it holds the 'seed' dataset at tier S"),
    ],
    ids=["unknown", "recorded"],
)
@pytest.mark.usefixtures("demo")
def test_dev_demo_keeps_an_existing_netbox_and_prints_the_reload_command(  # noqa: PLR0913, PLR0917 -- pytest fixtures
    context: Context,
    fake: FakeDocker,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorded: str | None,
    expected: str,
) -> None:
    fake.netbox_running = True
    monkeypatch.setattr(dev, "recorded_dataset", lambda: recorded)

    _body(dev.dev_demo)(context)

    output = capsys.readouterr().out
    assert "keeps its data" in output
    assert expected in output
    assert "uv run invoke netbox.seed --dataset demo" in output


@pytest.mark.usefixtures("demo")
def test_dev_demo_prints_no_token_the_user_set(
    context: Context,
    fake: FakeDocker,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake.infrahub["infrahub_token"] = CUSTOM_TOKEN
    sync_token = "a-sync-token-of-the-developer"  # noqa: S105 -- a test value
    monkeypatch.setenv(dev.SYNC_TOKEN_VARIABLE, sync_token)
    resolve = dev.dev_settings
    monkeypatch.setattr(
        dev,
        "dev_settings",
        lambda *args, **kwargs: dataclasses.replace(
            resolve(*args, **kwargs), bearer_tokens=json.dumps({"mine": {"token": sync_token}})
        ),
    )

    _body(dev.dev_demo)(context)

    command, env = _prepare_call(fake)
    # The shell's Sync token reaches the command through the inherited environment, not `env`.
    assert dev.SYNC_TOKEN_VARIABLE not in env
    assert env[dev.INFRAHUB_TOKEN_VARIABLE] == CUSTOM_TOKEN
    assert CUSTOM_TOKEN not in command
    assert sync_token not in command
    output = capsys.readouterr().out
    assert CUSTOM_TOKEN not in output
    assert sync_token not in output
    assert dev.API_TOKEN not in output
    assert "from BEARER_TOKENS" in output
    assert f"from {dev.INFRAHUB_ADMIN_TOKEN_VARIABLE}" in output


@pytest.mark.usefixtures("demo")
def test_dev_demo_prints_the_development_tokens_when_they_are_in_use(
    context: Context, capsys: pytest.CaptureFixture[str]
) -> None:
    _body(dev.dev_demo)(context)

    output = capsys.readouterr().out
    assert f"INFRAHUB_SYNC_API_TOKEN={dev.API_TOKEN}" in output
    assert f"INFRAHUB_API_TOKEN={ADMIN_TOKEN}" in output


def test_dev_demo_without_a_sync_token_for_bearer_tokens_stops_before_starting(
    context: Context, fake: FakeDocker, demo: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    resolve = dev.dev_settings
    monkeypatch.setattr(
        dev,
        "dev_settings",
        lambda *args, **kwargs: dataclasses.replace(
            resolve(*args, **kwargs), bearer_tokens=json.dumps({"mine": {"token": CUSTOM_TOKEN}})
        ),
    )

    with pytest.raises(dev.DevStackError, match=dev.SYNC_TOKEN_VARIABLE):
        _body(dev.dev_demo)(context)

    assert fake.compose_commands() == []
    assert demo == []


@pytest.mark.parametrize(
    "answer",
    [("", False), ("not json", True), (json.dumps({"schema": "loaded"}), True)],
    ids=["failed", "not-json", "no-configuration"],
)
@pytest.mark.usefixtures("demo")
def test_dev_demo_refuses_a_preparation_it_cannot_read(
    context: Context, fake: FakeDocker, answer: tuple[str, bool]
) -> None:
    fake.prepare_stdout, fake.prepare_ok = answer

    with pytest.raises(dev.DevStackError, match="demo prepare"):
        _body(dev.dev_demo)(context)


@pytest.mark.usefixtures("demo")
def test_dev_demo_shows_the_last_lines_of_a_preparation_that_printed_no_json_object(
    context: Context, fake: FakeDocker
) -> None:
    fake.prepare_stdout = "line one\nline two\nline three\nline four\nwarning: printed instead"

    with pytest.raises(dev.DevStackError) as raised:
        _body(dev.dev_demo)(context)

    assert "warning: printed instead" in str(raised.value)
    assert "line two" in str(raised.value)
    assert "line one" not in str(raised.value)
