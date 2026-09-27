"""`generate` resolves its Infrahub connection exactly like the runtime adapter.

Address precedence is `INFRAHUB_ADDRESS`, then `INFRAHUB_URL`, then `settings.url`;
token precedence is `INFRAHUB_API_TOKEN`, then `settings.token`. Branch precedence
is `settings.branch`, then `INFRAHUB_DEFAULT_BRANCH`, then `--branch`, then `main`.

Every case drives the real command through `CliRunner`, so the captured `config` is the
SDK `Config` that `get_infrahub_config` actually builds.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from typer.testing import CliRunner

from infrahub_sync import SyncAdapter, SyncConfig
from infrahub_sync.adapters.infrahub import InfrahubAdapter
from infrahub_sync.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()

NETBOX_SIDE: dict[str, Any] = {"name": "netbox", "settings": {"url": "http://netbox.example.com"}}
ENV_ADDRESS = "http://env-address:8000"
ENV_URL = "http://env-url:8000"
CONFIG_URL = "http://config-url:8000"
# Fake API tokens. The names avoid the word "token" so ruff's S105 does not read the
# comparisons below as hardcoded credentials.
ENV_CREDENTIAL = "env-api-value"
CONFIG_CREDENTIAL = "config-api-value"


class FakeSchema:
    """Stand-in for `client.schema` that reports an empty schema."""

    @staticmethod
    def all() -> dict[str, Any]:
        return {}


@pytest.fixture
def captured_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Bind a recording `InfrahubClientSync` in `cli` and clear every `INFRAHUB_*` variable.

    Returns a dict holding, under the key `kwargs`, the keyword arguments the command passed
    to the client, so a test can assert on an omitted `address` as well as on a value.
    """
    for name in [key for key in os.environ if key.startswith("INFRAHUB_")]:
        monkeypatch.delenv(name, raising=False)

    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["kwargs"] = kwargs
            self.schema = FakeSchema()

    monkeypatch.setattr("infrahub_sync.cli.InfrahubClientSync", FakeClient)
    monkeypatch.setattr("infrahub_sync.cli.render_adapter", lambda **_kwargs: [])
    return captured


def write_config(tmp_path: Path, source: dict[str, Any], destination: dict[str, Any]) -> Path:
    """Write a minimal sync configuration with an empty schema mapping and return its path."""
    config = {"name": "test-sync", "source": source, "destination": destination, "schema_mapping": []}
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config), encoding="UTF-8")
    return config_path


def run_generate(config_path: Path, *extra_args: str) -> None:
    """Invoke `generate` against `config_path` and require a clean exit."""
    result = runner.invoke(app, ["generate", "--config-file", str(config_path), *extra_args])
    assert result.exit_code == 0, f"exit={result.exit_code} exception={result.exception!r}\n{result.output}"


def test_env_address_overrides_configured_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_client: dict[str, Any]
) -> None:
    """T1: `INFRAHUB_ADDRESS` wins over a configured `settings.url`."""
    monkeypatch.setenv("INFRAHUB_ADDRESS", ENV_ADDRESS)
    config_path = write_config(
        tmp_path,
        source=NETBOX_SIDE,
        destination={"name": "infrahub", "settings": {"url": CONFIG_URL, "token": CONFIG_CREDENTIAL}},
    )

    run_generate(config_path)

    assert captured_client["kwargs"]["address"] == ENV_ADDRESS


def test_env_address_wins_over_env_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_client: dict[str, Any]
) -> None:
    """T1b: with both variables set, `INFRAHUB_ADDRESS` is checked before `INFRAHUB_URL`."""
    monkeypatch.setenv("INFRAHUB_ADDRESS", ENV_ADDRESS)
    monkeypatch.setenv("INFRAHUB_URL", ENV_URL)
    config_path = write_config(
        tmp_path,
        source=NETBOX_SIDE,
        destination={"name": "infrahub", "settings": {"url": CONFIG_URL, "token": CONFIG_CREDENTIAL}},
    )

    run_generate(config_path)

    assert captured_client["kwargs"]["address"] == ENV_ADDRESS


def test_destination_without_settings_resolves_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_client: dict[str, Any]
) -> None:
    """T2: an Infrahub destination with no `settings` key still resolves address, token and `--branch`."""
    monkeypatch.setenv("INFRAHUB_URL", ENV_URL)
    monkeypatch.setenv("INFRAHUB_API_TOKEN", ENV_CREDENTIAL)
    config_path = write_config(tmp_path, source=NETBOX_SIDE, destination={"name": "infrahub"})

    run_generate(config_path, "--branch", "x")

    kwargs = captured_client["kwargs"]
    assert kwargs["address"] == ENV_URL
    assert kwargs["config"].api_token == ENV_CREDENTIAL
    assert kwargs["config"].default_branch == "x"


def test_configured_connection_is_preserved_without_environment(
    tmp_path: Path, captured_client: dict[str, Any]
) -> None:
    """T3: with no Infrahub environment set, a fully configured side behaves as before."""
    config_path = write_config(
        tmp_path,
        source=NETBOX_SIDE,
        destination={"name": "infrahub", "settings": {"url": CONFIG_URL, "token": CONFIG_CREDENTIAL}},
    )

    run_generate(config_path)

    kwargs = captured_client["kwargs"]
    assert kwargs["address"] == CONFIG_URL
    assert kwargs["config"].api_token == CONFIG_CREDENTIAL


def test_empty_destination_falls_through_to_configured_source(tmp_path: Path, captured_client: dict[str, Any]) -> None:
    """Use the configured Infrahub source when the Infrahub destination has no settings."""
    config_path = write_config(
        tmp_path,
        source={"name": "infrahub", "settings": {"url": CONFIG_URL, "token": CONFIG_CREDENTIAL}},
        destination={"name": "infrahub"},
    )

    run_generate(config_path)

    kwargs = captured_client["kwargs"]
    assert kwargs["address"] == CONFIG_URL
    assert kwargs["config"].api_token == CONFIG_CREDENTIAL


def test_generate_configures_tls_and_timeout_like_runtime(tmp_path: Path, captured_client: dict[str, Any]) -> None:
    """Pass the runtime adapter's TLS choice and 60-second timeout to the SDK."""
    config_path = write_config(
        tmp_path,
        source=NETBOX_SIDE,
        destination={"name": "infrahub", "settings": {"url": CONFIG_URL, "verify_ssl": False}},
    )

    run_generate(config_path)

    sdk_config = captured_client["kwargs"]["config"]
    assert sdk_config.tls_insecure is True
    assert sdk_config.timeout == 60


def test_env_token_used_when_config_has_no_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_client: dict[str, Any]
) -> None:
    """T4: `INFRAHUB_API_TOKEN` reaches the SDK `Config` when the configuration carries no token."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", ENV_CREDENTIAL)
    config_path = write_config(
        tmp_path, source=NETBOX_SIDE, destination={"name": "infrahub", "settings": {"url": CONFIG_URL}}
    )

    run_generate(config_path)

    kwargs = captured_client["kwargs"]
    assert kwargs["address"] == CONFIG_URL
    assert kwargs["config"].api_token == ENV_CREDENTIAL


def test_env_token_overrides_configured_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_client: dict[str, Any]
) -> None:
    """T5: `INFRAHUB_API_TOKEN` wins over a configured `settings.token`."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", ENV_CREDENTIAL)
    config_path = write_config(
        tmp_path,
        source=NETBOX_SIDE,
        destination={"name": "infrahub", "settings": {"url": CONFIG_URL, "token": CONFIG_CREDENTIAL}},
    )

    run_generate(config_path)

    assert captured_client["kwargs"]["config"].api_token == ENV_CREDENTIAL


@pytest.mark.parametrize("settings", [{"url": CONFIG_URL}, {}], ids=["configured-url", "empty-settings"])
def test_infrahub_as_source_resolves_like_destination(
    settings: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_client: dict[str, Any],
) -> None:
    """T6: the source side is selected by adapter name, with or without configured settings."""
    monkeypatch.setenv("INFRAHUB_ADDRESS", ENV_ADDRESS)
    monkeypatch.setenv("INFRAHUB_API_TOKEN", ENV_CREDENTIAL)
    config_path = write_config(tmp_path, source={"name": "infrahub", "settings": settings}, destination=NETBOX_SIDE)

    run_generate(config_path, "--branch", "x")

    kwargs = captured_client["kwargs"]
    assert kwargs["address"] == ENV_ADDRESS
    assert kwargs["config"].api_token == ENV_CREDENTIAL
    assert kwargs["config"].default_branch == "x"


@pytest.mark.parametrize(
    ("settings_branch", "expected_branch"), [(None, "x"), ("y", "y")], ids=["cli-branch", "settings-branch"]
)
def test_branch_precedence_is_unchanged(
    settings_branch: str | None,
    expected_branch: str,
    tmp_path: Path,
    captured_client: dict[str, Any],
) -> None:
    """T7: `settings.branch` still wins over `--branch`."""
    settings: dict[str, Any] = {"url": CONFIG_URL, "token": CONFIG_CREDENTIAL}
    if settings_branch is not None:
        settings["branch"] = settings_branch
    config_path = write_config(tmp_path, source=NETBOX_SIDE, destination={"name": "infrahub", "settings": settings})

    run_generate(config_path, "--branch", "x")

    assert captured_client["kwargs"]["config"].default_branch == expected_branch


def test_environment_branch_is_used_without_cli_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_client: dict[str, Any]
) -> None:
    """Use INFRAHUB_DEFAULT_BRANCH when neither settings nor the command specifies a branch."""
    monkeypatch.setenv("INFRAHUB_DEFAULT_BRANCH", "environment-branch")
    config_path = write_config(
        tmp_path, source=NETBOX_SIDE, destination={"name": "infrahub", "settings": {"url": CONFIG_URL}}
    )

    run_generate(config_path)

    assert captured_client["kwargs"]["config"].default_branch == "environment-branch"


@pytest.mark.parametrize(
    ("settings", "execution"),
    [
        ({"url": CONFIG_URL, "token": CONFIG_CREDENTIAL, "branch": "configured", "verify_ssl": False}, ({}, None)),
        (
            {"url": CONFIG_URL, "token": CONFIG_CREDENTIAL, "verify_ssl": True},
            ({"INFRAHUB_ADDRESS": ENV_ADDRESS, "INFRAHUB_API_TOKEN": ENV_CREDENTIAL}, "cli-branch"),
        ),
        (
            {"url": CONFIG_URL, "token": CONFIG_CREDENTIAL, "verify_ssl": False},
            ({"INFRAHUB_DEFAULT_BRANCH": "environment-branch"}, None),
        ),
    ],
    ids=["configured", "environment-overrides", "environment-branch"],
)
def test_generate_and_runtime_adapter_use_matching_connection(
    settings: dict[str, Any],
    execution: tuple[dict[str, str], str | None],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_client: dict[str, Any],
) -> None:
    """Generation and the runtime adapter resolve the same SDK connection fields."""
    environment, branch = execution
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    destination = {"name": "infrahub", "settings": settings}
    config_path = write_config(tmp_path, source=NETBOX_SIDE, destination=destination)
    run_generate(config_path, *(["--branch", branch] if branch else []))
    generated = captured_client["kwargs"]

    runtime: dict[str, Any] = {}

    class RuntimeSchema:
        @staticmethod
        def all(**_kwargs: object) -> dict[str, Any]:
            return {}

    class RuntimeClient:
        def __init__(self, **kwargs: object) -> None:
            runtime.update(kwargs)
            self.schema = RuntimeSchema()

        @staticmethod
        def get(**_kwargs: object) -> None:
            return None

    monkeypatch.setattr("infrahub_sync.adapters.infrahub.InfrahubClientSync", RuntimeClient)
    sync_config = SyncConfig(
        name="test-sync", source=SyncAdapter(name="netbox"), destination=SyncAdapter(name="infrahub", settings=settings)
    )
    InfrahubAdapter(target="destination", adapter=sync_config.destination, config=sync_config, branch=branch)

    assert runtime["address"] == generated["address"]
    for field in ("api_token", "default_branch", "tls_insecure", "timeout"):
        assert getattr(runtime["config"], field) == getattr(generated["config"], field)


@pytest.mark.parametrize("settings", [{"url": CONFIG_URL}, {"token": CONFIG_CREDENTIAL}])
def test_runtime_adapter_still_requires_url_and_token(
    settings: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared resolver preserves the runtime adapter's missing-connection error."""
    for name in [key for key in os.environ if key.startswith("INFRAHUB_")]:
        monkeypatch.delenv(name, raising=False)
    sync_config = SyncConfig(
        name="test-sync", source=SyncAdapter(name="netbox"), destination=SyncAdapter(name="infrahub", settings=settings)
    )

    with pytest.raises(ValueError, match="Both url and token must be specified!"):
        InfrahubAdapter(target="destination", adapter=sync_config.destination, config=sync_config)
