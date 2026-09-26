"""Unit coverage for the local-NetBox task logic that runs without Docker."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from invoke import Context

from tasks import netbox

if TYPE_CHECKING:
    from invoke.tasks import Task

REQUIRED_VALUES = {
    "COMPOSE_PROJECT_NAME": "netbox-test",
    "NETBOX_PORT": "8082",
    "NETBOX_DB_PASSWORD": "netbox",
    "NETBOX_SECRET_KEY": "secret",
    "NETBOX_TOKEN_PEPPER": "pepper",
    "NETBOX_ADMIN_PASSWORD": "admin",
    "NETBOX_TOKEN_KEY": "devnetboxkey",
    "NETBOX_TOKEN": "devnetboxseedtoken0000000000000000000000",
}


def test_dataset_script_resolves_the_seed_dataset() -> None:
    assert netbox.dataset_script("seed") == netbox.DATASETS["seed"]


def test_dataset_script_refuses_an_unknown_dataset() -> None:
    with pytest.raises(netbox.NetboxError, match="unknown dataset 'demo'"):
        netbox.dataset_script("demo")


def test_netbox_url_reads_the_configured_port() -> None:
    assert netbox.netbox_url({"NETBOX_PORT": "8082"}) == "http://localhost:8082"


def test_netbox_token_joins_the_v2_key_and_plaintext() -> None:
    values = {"NETBOX_TOKEN_KEY": "devnetboxkey", "NETBOX_TOKEN": "devnetboxseedtoken0000000000000000000000"}

    assert netbox.netbox_token(values) == "nbt_devnetboxkey.devnetboxseedtoken0000000000000000000000"


def test_load_netbox_env_reports_every_missing_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_env = tmp_path / "netbox.local.env"
    monkeypatch.setattr(netbox, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setattr(netbox, "LOCAL_ENV_FILE", local_env)

    with pytest.raises(netbox.NetboxError, match="COMPOSE_PROJECT_NAME"):
        netbox.load_netbox_env()


def test_load_netbox_env_applies_local_overrides_after_the_shipped_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shipped = tmp_path / "netbox.env"
    shipped.write_text("\n".join(f"{key}={value}" for key, value in REQUIRED_VALUES.items()), encoding="utf-8")
    local_env = tmp_path / "netbox.local.env"
    local_env.write_text("NETBOX_PORT=9999\n", encoding="utf-8")
    monkeypatch.setattr(netbox, "ENV_FILE", shipped)
    monkeypatch.setattr(netbox, "LOCAL_ENV_FILE", local_env)

    values = netbox.load_netbox_env()

    assert values["NETBOX_PORT"] == "9999"
    assert values["COMPOSE_PROJECT_NAME"] == "netbox-test"


def test_the_shipped_env_file_declares_every_required_key() -> None:
    """The committed defaults must be enough to run every task from a fresh clone."""
    values = netbox.load_netbox_env()

    assert set(REQUIRED_VALUES) <= values.keys()


def test_compose_builds_the_expected_docker_compose_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str], bool]] = []
    context = Context()
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda command, *, env, pty: calls.append((command, env, pty)))

    netbox._compose(context, "config", REQUIRED_VALUES)

    assert calls == [
        (
            f"docker compose --project-name netbox-test --env-file {netbox.ENV_FILE} -f {netbox.COMPOSE_FILE} config",
            REQUIRED_VALUES,
            False,
        )
    ]


def test_reset_database_recreates_before_it_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reset step drops volumes, brings the stack back up, and only then waits.

    Order is the property under test: waiting before `up` would check a container that
    is not there yet, and seeding before either step would write to a database `down
    --volumes` is about to discard.
    """
    events: list[tuple[str, ...]] = []
    context = Context()

    monkeypatch.setattr(netbox, "_compose", lambda _context, arguments, _values: events.append(("compose", arguments)))
    monkeypatch.setattr(netbox, "_wait_for_http", lambda _url, description: events.append(("wait", description)))

    netbox.reset_database(context, REQUIRED_VALUES)

    assert events == [
        ("compose", "down --volumes"),
        ("compose", f"up --detach --wait --wait-timeout {netbox.WAIT_TIMEOUT_SECONDS}"),
        ("wait", "NetBox"),
    ]


def test_seed_resets_the_database_before_loading_the_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str]] = []
    context = Context()

    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "reset_database", lambda _context, _values: events.append(("reset", "")))
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda command, **_kwargs: events.append(("run", command)))

    cast("Task", netbox.seed).body(context, dataset="seed")

    assert events[0] == ("reset", "")
    run_event = events[1]
    assert run_event[0] == "run"
    assert str(netbox.DATASETS["seed"]) in run_event[1]
    assert "--url http://localhost:8082" in run_event[1]
    assert "--token nbt_devnetboxkey.devnetboxseedtoken0000000000000000000000" in run_event[1]


def test_seed_prints_the_url_and_token_banner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    context = Context()
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "reset_database", lambda _context, _values: None)
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda _command, **_kwargs: None)

    cast("Task", netbox.seed).body(context, dataset="seed")

    printed = capsys.readouterr().out
    assert "http://localhost:8082" in printed
    assert "nbt_devnetboxkey.devnetboxseedtoken0000000000000000000000" in printed


def test_seed_refuses_an_unknown_dataset_before_touching_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    context = Context()
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "reset_database", lambda _context, _values: events.append("reset"))

    with pytest.raises(netbox.NetboxError, match="unknown dataset"):
        cast("Task", netbox.seed).body(context, dataset="demo")

    assert events == []


def test_up_waits_for_netbox_and_prints_no_seed_step(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []
    context = Context()
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "_compose", lambda *_args, **_kwargs: events.append("compose"))
    monkeypatch.setattr(netbox, "_wait_for_http", lambda *_args, **_kwargs: events.append("wait"))

    cast("Task", netbox.up).body(context)

    assert events == ["compose", "wait"]
    printed = capsys.readouterr().out
    assert "http://localhost:8082" in printed
    assert "nbt_devnetboxkey.devnetboxseedtoken0000000000000000000000" in printed


def test_down_removes_containers_and_volumes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    context = Context()
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "_compose", lambda _context, arguments, _values: calls.append(arguments))

    cast("Task", netbox.down).body(context)

    assert calls == ["down --volumes"]
