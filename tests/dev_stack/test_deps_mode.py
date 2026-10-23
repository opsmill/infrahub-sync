"""Dependencies mode: `dev.deps`, the host processes `dev.api` and `dev.worker`, and `dev.smoke`.

No Docker: a fake runner answers `docker compose config --format json` with the shape
Compose prints for the development file, and `httpx.get` is replaced, so the tests see
every command the tasks run and every URL they call (FR-020, FR-021, FR-022, D-2).
"""

from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from invoke import Context

from tasks import dev

if TYPE_CHECKING:
    from pathlib import Path

    from invoke.tasks import Task

ADMIN_TOKEN = "06438eb2-8019-4776-878c-0941b1f1d1ec"  # noqa: S105 -- the development default
SYNC_ENV = {
    "INFRAHUB_SYNC_DATABASE_URL": "postgresql://postgres:devpassword@postgres:5432/infrahub_sync",
    "INFRAHUB_SYNC_S3_BUCKET": "infrahub-sync",
    "INFRAHUB_SYNC_S3_ENDPOINT_URL": "http://object-store:9000",
    "AWS_ACCESS_KEY_ID": "devaccesskey",
    "INFRAHUB_SYNC_SERVICE_WORK_POOL": "infrahub-sync",
    "PREFECT_API_URL": "http://prefect-server:4200/api",
    "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN": "",
}
CORE_RUNNING = "postgres\nobject-store\nprefect-server\ninfrahub-server\n"


def _port(target: int, published: str, host_ip: str = "127.0.0.1") -> list[dict[str, Any]]:
    return [{"mode": "ingress", "host_ip": host_ip, "target": target, "published": published}]


def _resolved(*, infrahub: bool) -> dict[str, Any]:
    """What `docker compose config --format json` prints, trimmed to the fields the tasks read."""
    services: dict[str, Any] = {
        "postgres": {"ports": _port(5432, "5440")},
        "object-store": {"ports": _port(9000, "9010")},
        "prefect-server": {"image": "infrahub-sync:s010-deps", "ports": _port(4200, "4230")},
        "sync-api": {
            "ports": _port(8000, "8030"),
            "environment": {
                **SYNC_ENV,
                "INFRAHUB_SYNC_SERVICE_HOST": "0.0.0.0",  # noqa: S104 -- the container's value
                "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS": '{"dev": {"token": "infrahub-sync-dev-token"}}',
            },
        },
        "sync-worker": {"environment": {**SYNC_ENV, "PREFECT_WORKER_HEARTBEAT_SECONDS": "10"}},
    }
    if infrahub:
        services["infrahub-server"] = {
            "ports": _port(8000, "8000"),
            "environment": {"INFRAHUB_INITIAL_ADMIN_TOKEN": ADMIN_TOKEN},
        }
    return {"name": "infrahub-sync-dev", "services": services}


class _Result:
    def __init__(self, stdout: str = "", *, ok: bool = True) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.ok = ok


class FakeDocker:
    """Records every command with its keyword arguments; answers `config`, `image inspect` and `ps`."""

    def __init__(self) -> None:
        self.label: str | None = dev.build_inputs_hash()
        self.running = ""
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, command: str, **kwargs: Any) -> _Result:  # noqa: ANN401 -- Invoke's keyword arguments
        self.calls.append((command, kwargs))
        if command.endswith("config --format json"):
            return _Result(json.dumps(_resolved(infrahub="--profile infrahub" in command)))
        if command.startswith("docker image inspect"):
            return _Result(self.label or "", ok=self.label is not None)
        if command.endswith("ps --services --status running"):
            return _Result(self.running)
        return _Result()

    @property
    def commands(self) -> list[str]:
        return [command for command, _ in self.calls]

    def compose_commands(self) -> list[str]:
        return [
            command
            for command in self.commands
            if command.startswith("docker compose") and "config" not in command and " ps " not in command
        ]


class FakeHttp:
    """Stands in for `httpx.get`: records every URL and answers `/status` as told."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.status: dict[str, Any] | None = {"service": "ready", "worker": {"state": "ready"}}

    def get(self, url: str, **_kwargs: Any) -> httpx.Response:  # noqa: ANN401 -- httpx's keyword arguments
        self.urls.append(url)
        if self.status is None:
            msg = "connection refused"
            raise httpx.ConnectError(msg, request=httpx.Request("GET", url))
        return httpx.Response(200, json=self.status, request=httpx.Request("GET", url))


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    monkeypatch.delenv(dev.INFRAHUB_NETWORK_VARIABLE, raising=False)
    monkeypatch.delenv(dev.INFRAHUB_URL_VARIABLE, raising=False)
    monkeypatch.delenv(dev.INFRAHUB_CREDENTIAL_VARIABLE, raising=False)
    return FakeDocker()


@pytest.fixture
def context(fake: FakeDocker, monkeypatch: pytest.MonkeyPatch) -> Context:
    context = Context()
    monkeypatch.setattr(context, "run", fake.run)
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    return context


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    fake_http = FakeHttp()
    monkeypatch.setattr(httpx, "get", fake_http.get)
    return fake_http


def _body(task_object: object) -> Any:  # noqa: ANN401 -- an Invoke task body
    return cast("Task", task_object).body


# ----- dev.deps -----------------------------------------------------------------------------


def test_dev_deps_starts_core_and_infrahub_then_bootstraps_and_returns(
    context: Context, fake: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    _body(dev.dev_deps)(context)

    assert fake.compose_commands() == [
        "docker compose --profile infrahub --project-name infrahub-sync-dev "
        "-f development/docker-compose.yml up --detach --wait --wait-timeout 420",
        "docker compose --profile infrahub --project-name infrahub-sync-dev "
        "-f development/docker-compose.yml run --rm --no-deps sync-bootstrap",
    ]
    assert not any("--profile sync" in command for command in fake.compose_commands())
    # The image it checks is the one the resolved file names.
    assert next(c for c in fake.commands if c.startswith("docker image inspect")).endswith(" infrahub-sync:s010-deps")
    output = capsys.readouterr().out
    for url in ("http://127.0.0.1:8030", "http://127.0.0.1:4230", "http://127.0.0.1:8000", "http://127.0.0.1:9010"):
        assert url in output
    assert "127.0.0.1:5440" in output


def test_dev_deps_with_a_stale_image_builds_first(context: Context, fake: FakeDocker) -> None:
    fake.label = "0" * 64

    _body(dev.dev_deps)(context)

    commands = fake.compose_commands()
    assert " build --build-arg BUILD_INPUTS=" in commands[0]
    assert " up --detach --wait " in commands[1]


def test_dev_deps_on_an_infrahub_network_starts_no_infrahub(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(dev.INFRAHUB_NETWORK_VARIABLE, "lab_default")
    monkeypatch.setenv(dev.INFRAHUB_URL_VARIABLE, "http://infrahub.lab.invalid:8000")

    _body(dev.dev_deps)(context)

    assert fake.compose_commands()
    assert not any("--profile" in command for command in fake.compose_commands())
    assert "http://infrahub.lab.invalid:8000" in capsys.readouterr().out


# ----- dev.api and dev.worker ---------------------------------------------------------------


def test_dev_api_runs_in_the_foreground_against_the_loopback_publications(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    _body(dev.dev_api)(context)

    command, kwargs = fake.calls[-1]
    assert command == (
        f"{sys.executable} -m uvicorn --factory infrahub_sync.service.serve:build_app --host 127.0.0.1 --port 8030"
    )
    assert kwargs["pty"] is True
    assert not kwargs.get("asynchronous")
    assert not kwargs.get("disown")
    environment = kwargs["env"]
    assert environment["INFRAHUB_SYNC_DATABASE_URL"] == "postgresql://postgres:devpassword@127.0.0.1:5440/infrahub_sync"
    assert environment["INFRAHUB_SYNC_S3_ENDPOINT_URL"] == "http://127.0.0.1:9010"
    assert environment["PREFECT_API_URL"] == "http://127.0.0.1:4230/api"
    assert environment["INFRAHUB_SYNC_SERVICE_HOST"] == "127.0.0.1"
    assert environment["INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"]
    assert environment[dev.INFRAHUB_CREDENTIAL_VARIABLE] == ADMIN_TOKEN
    # No PID file, no other file either.
    assert list(tmp_path.iterdir()) == []


def test_dev_worker_runs_in_the_foreground_against_the_loopback_publications(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    _body(dev.dev_worker)(context)

    command, kwargs = fake.calls[-1]
    assert command == f"{sys.executable} -m infrahub_sync.service.worker --pool infrahub-sync"
    assert kwargs["pty"] is True
    assert not kwargs.get("asynchronous")
    assert not kwargs.get("disown")
    environment = kwargs["env"]
    assert environment["INFRAHUB_SYNC_DATABASE_URL"] == "postgresql://postgres:devpassword@127.0.0.1:5440/infrahub_sync"
    assert environment["INFRAHUB_SYNC_S3_ENDPOINT_URL"] == "http://127.0.0.1:9010"
    assert environment["PREFECT_API_URL"] == "http://127.0.0.1:4230/api"
    assert environment["PREFECT_WORKER_HEARTBEAT_SECONDS"] == "10"
    assert "INFRAHUB_SYNC_SERVICE_HOST" not in environment
    assert list(tmp_path.iterdir()) == []


def test_a_credential_the_shell_exports_reaches_the_host_worker(
    context: Context, fake: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(dev.INFRAHUB_CREDENTIAL_VARIABLE, "exported")

    _body(dev.dev_worker)(context)

    # Left out of `env`, so the exported value the process inherits applies.
    assert dev.INFRAHUB_CREDENTIAL_VARIABLE not in fake.calls[-1][1]["env"]


def test_host_processes_need_the_loopback_publications() -> None:
    settings = dev.parse_dev_settings(
        {"services": {k: v for k, v in _resolved(infrahub=True)["services"].items() if k != "object-store"}}
    )

    with pytest.raises(dev.DevStackError, match="object-store"):
        dev.host_environment(settings, "sync-worker")


# ----- dev.smoke in dependencies mode -------------------------------------------------------


@pytest.fixture
def smoke(monkeypatch: pytest.MonkeyPatch, fake: FakeDocker) -> list[str]:
    seeded: list[str] = []
    fake.running = CORE_RUNNING
    monkeypatch.setattr(dev, "_infrahub_reachable", lambda _address: True)
    monkeypatch.setattr(dev, "load_smoke_dataset", lambda _context, address, _token: seeded.append(address))
    return seeded


def test_dev_smoke_names_dev_api_when_status_does_not_answer(
    context: Context, fake: FakeDocker, http: FakeHttp, smoke: list[str]
) -> None:
    http.status = None

    with pytest.raises(dev.DevStackError, match=r"dev\.api"):
        _body(dev.dev_smoke)(context)

    assert http.urls == ["http://127.0.0.1:8030/status"]
    assert dev.SMOKE_COMMAND not in fake.commands
    assert smoke == []


@pytest.mark.parametrize("state", ["no-live-worker", "unavailable"])
def test_dev_smoke_names_dev_worker_when_status_reports_no_available_worker(
    context: Context, fake: FakeDocker, http: FakeHttp, smoke: list[str], state: str
) -> None:
    http.status = {"service": "ready", "worker": {"state": state, "detail_available": state != "unavailable"}}

    with pytest.raises(dev.DevStackError, match=r"dev\.worker"):
        _body(dev.dev_smoke)(context)

    assert http.urls == ["http://127.0.0.1:8030/status"]
    assert dev.SMOKE_COMMAND not in fake.commands
    assert smoke == []


def test_dev_smoke_with_both_host_processes_runs_the_suite_for_a_host_worker(
    context: Context, fake: FakeDocker, http: FakeHttp, smoke: list[str]
) -> None:
    _body(dev.dev_smoke)(context)

    # Only the Sync API's /status; never the Prefect API on 4230.
    assert http.urls == ["http://127.0.0.1:8030/status"]
    assert not any(":4230" in command for command in fake.commands)
    assert smoke == ["http://127.0.0.1:8000"]
    command, kwargs = fake.calls[-1]
    assert command == dev.SMOKE_COMMAND
    assert kwargs["env"] == {dev.SMOKE_STACK_VARIABLE: dev.DEPS_SMOKE_STACK}


@pytest.mark.usefixtures("smoke")
def test_dev_smoke_with_the_sync_containers_skips_the_host_process_check(
    context: Context, fake: FakeDocker, http: FakeHttp
) -> None:
    fake.running = CORE_RUNNING + "sync-api\nsync-worker\n"

    _body(dev.dev_smoke)(context)

    assert http.urls == []
    assert fake.calls[-1][1]["env"] == {dev.SMOKE_STACK_VARIABLE: "dev"}


def test_the_dev_namespace_holds_the_dependencies_mode_tasks() -> None:
    assert {"deps", "api", "worker"} <= set(dev.ns.task_names)
