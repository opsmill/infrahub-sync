"""`infrahub-sync demo prepare netbox`: exit codes, text and JSON output, no token in output."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from infrahub_sync.cli import app
from infrahub_sync.client import APIError, TransportError
from infrahub_sync.demo import prepare
from infrahub_sync.demo.errors import DemoSchemaError
from tests.demo.fakes import FakeBranches, FakeSchemaLoader, FakeSyncClient

RUNNER = CliRunner()
INFRAHUB_TOKEN = "canary-infrahub-token-c41d22"  # noqa: S105 -- test canary, not a secret
SYNC_TOKEN = "canary-sync-token-9be310"  # noqa: S105 -- test canary, not a secret
ARGUMENTS = (
    "demo",
    "prepare",
    "netbox",
    "--netbox-url",
    "http://netbox:8080",
    "--infrahub-url",
    "http://infrahub-server:8000",
    "--infrahub-address",
    "http://127.0.0.1:8000",
)


@pytest.fixture(autouse=True)
def _tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_API_TOKEN", INFRAHUB_TOKEN)
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", SYNC_TOKEN)


def _invoke(
    loader: FakeSchemaLoader,
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
    client: FakeSyncClient | None = None,
    branches: FakeBranches | None = None,
) -> tuple[FakeSyncClient, str]:
    client = client if client is not None else FakeSyncClient()
    monkeypatch.setattr(prepare, "load_schema", loader)
    monkeypatch.setattr(prepare, "InfrahubBranches", lambda: branches if branches is not None else FakeBranches())
    result = RUNNER.invoke(app, [*ARGUMENTS, *extra], obj={"client": client})
    return client, f"{result.exit_code}\n{result.output}"


def _assert_no_token(output: str) -> None:
    assert INFRAHUB_TOKEN not in output
    assert SYNC_TOKEN not in output


def test_without_the_infrahub_token_it_exits_2_naming_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRAHUB_API_TOKEN")
    loader = FakeSchemaLoader()

    client, output = _invoke(loader, monkeypatch)

    assert output.startswith("2\n")
    assert "INFRAHUB_API_TOKEN" in output
    assert loader.calls == []
    assert client.calls == []


def test_a_schema_error_exits_1_with_the_cause_and_registers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    loader = FakeSchemaLoader(error=DemoSchemaError("Infrahub at http://127.0.0.1:8000 refused the demo schema"))

    client, output = _invoke(loader, monkeypatch)

    assert output.startswith("1\n")
    assert "cause: Infrahub at http://127.0.0.1:8000 refused the demo schema" in output
    assert client.calls == []
    _assert_no_token(output)


def test_success_prints_the_text_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    loader = FakeSchemaLoader()

    _, output = _invoke(loader, monkeypatch, "--schema-timeout", "30")

    assert output.splitlines() == [
        "0",
        "schema: loaded (16 files)",
        "configuration: registered from-netbox, config_id cfg-1, version 1",
        "netbox: http://netbox:8080",
        "infrahub: http://infrahub-server:8000",
    ]
    assert loader.calls == [("http://127.0.0.1:8000", INFRAHUB_TOKEN, 16, 30.0)]
    _assert_no_token(output)


def test_the_registered_package_holds_neither_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _invoke(FakeSchemaLoader(), monkeypatch)

    assert len(client.requests) == 1
    _assert_no_token(json.dumps(client.requests[0].package))


def test_json_prints_one_object(monkeypatch: pytest.MonkeyPatch) -> None:
    _, output = _invoke(FakeSchemaLoader(), monkeypatch, "--json")

    exit_code, document = output.split("\n", 1)
    assert exit_code == "0"
    assert json.loads(document) == {
        "config_id": "cfg-1",
        "configuration": "registered",
        "infrahub_url": "http://infrahub-server:8000",
        "name": "from-netbox",
        "netbox_url": "http://netbox:8080",
        "branch": None,
        "branch_state": None,
        "same_name": 0,
        "schema": "loaded",
        "schema_files": 16,
        "version": 1,
    }
    _assert_no_token(output)


def test_a_timeout_that_is_not_positive_exits_2_before_any_call(monkeypatch: pytest.MonkeyPatch) -> None:
    loader = FakeSchemaLoader()

    client, output = _invoke(loader, monkeypatch, "--schema-timeout", "0")

    assert output.startswith("2\n")
    assert "argument: schema_timeout" in output
    assert (loader.calls, client.calls) == ([], [])


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(APIError(401, "unauthenticated"), id="unauthenticated"),
        pytest.param(TransportError("list_configs"), id="connection-error"),
    ],
)
def test_a_sync_api_failure_after_the_schema_load_says_a_re_run_completes_the_registration(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    loader = FakeSchemaLoader()

    _, output = _invoke(loader, monkeypatch, client=FakeSyncClient(error=error))

    assert output.startswith("1\n")
    assert len(loader.calls) == 1
    assert "the demo schema is already loaded; run the command again to complete the registration" in output
    _assert_no_token(output)


@pytest.mark.parametrize("option", ["--netbox-url", "--infrahub-url", "--infrahub-address"])
@pytest.mark.parametrize(
    "address", ["http://admin:userinfo-canary@host:8000", "admin:userinfo-canary@host:8000"], ids=["url", "no-scheme"]
)
def test_an_address_with_userinfo_exits_2_naming_the_option_and_not_the_value(
    monkeypatch: pytest.MonkeyPatch, option: str, address: str
) -> None:
    loader = FakeSchemaLoader()

    client, output = _invoke(loader, monkeypatch, option, address)

    assert output.startswith("2\n")
    assert option in output
    assert "userinfo-canary" not in output
    assert (loader.calls, client.calls) == ([], [])


@pytest.mark.parametrize("branch", ["", "   "], ids=["empty", "blank"])
def test_an_empty_branch_exits_2_before_any_request(monkeypatch: pytest.MonkeyPatch, branch: str) -> None:
    loader, branches = FakeSchemaLoader(), FakeBranches()

    client, output = _invoke(loader, monkeypatch, "--branch", branch, branches=branches)

    assert output.startswith("2\n")
    assert "error: --branch must not be empty" in output
    assert (loader.calls, branches.calls, client.calls) == ([], [], [])


def test_branch_prints_whether_it_was_created(monkeypatch: pytest.MonkeyPatch) -> None:
    branches = FakeBranches()

    _, output = _invoke(FakeSchemaLoader(), monkeypatch, "--branch", "netbox-demo-1", branches=branches)

    assert output.splitlines()[0] == "0"
    assert output.splitlines()[-1] == "branch: created netbox-demo-1"
    assert "netbox-demo-1" in branches.names
    _assert_no_token(output)


def test_a_branch_with_another_schema_exits_1_and_registers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client, output = _invoke(
        FakeSchemaLoader(),
        monkeypatch,
        "--branch",
        "netbox-demo-1",
        branches=FakeBranches(names={"main": "same-schema", "netbox-demo-1": "other-schema"}),
    )

    assert output.startswith("1\n")
    assert "cause: the branch netbox-demo-1 does not hold the schema of main" in output
    assert not any(call[0] in {"register_config", "create_config_version"} for call in client.calls)
