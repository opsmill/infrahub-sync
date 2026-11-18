"""Preparing the NetBox demo: schema first, then an idempotent registration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import infrahub_sdk
import pytest
from infrahub_sdk.exceptions import ServerNotReachableError
from infrahub_sdk.schema import SchemaLoadResponse

from infrahub_sync.demo import assets, prepare
from infrahub_sync.demo.errors import DemoSchemaError
from infrahub_sync.demo.package import local_package_text
from tests.demo.fakes import FakeSchemaLoader, FakeSyncClient

NETBOX_URL = "http://netbox:8080"
INFRAHUB_URL = "http://infrahub-server:8000"
INFRAHUB_TOKEN = "canary-infrahub-token-7f3a91"  # noqa: S105 -- test canary, not a secret
DEMO_MODULES = sorted((Path(prepare.__file__).parent).glob("*.py"))


def _sha12(netbox_url: str = NETBOX_URL, infrahub_url: str = INFRAHUB_URL) -> str:
    text = local_package_text(assets.netbox_package_text(), netbox_url, infrahub_url)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _prepare(
    client: FakeSyncClient, loader: FakeSchemaLoader, netbox_url: str = NETBOX_URL
) -> prepare.PreparationResult:
    return prepare.prepare(
        client,
        netbox_url=netbox_url,
        infrahub_url=INFRAHUB_URL,
        infrahub_address=None,
        token=INFRAHUB_TOKEN,
        schema_loader=loader,
    )


def test_an_empty_infrahub_and_no_configuration_load_the_schema_and_register_version_1() -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()

    result = _prepare(client, loader)

    assert (result.schema, result.schema_files) == ("loaded", 16)
    assert (result.configuration, result.config_id, result.version) == ("registered", "cfg-1", 1)
    assert (result.name, result.netbox_url, result.infrahub_url) == ("from-netbox", NETBOX_URL, INFRAHUB_URL)
    assert client.calls[-1] == ("register_config", "cfg-1", f"demo-prepare-register-{_sha12()}")
    assert loader.calls == [(INFRAHUB_URL, INFRAHUB_TOKEN, 16, prepare.DEFAULT_SCHEMA_TIMEOUT)]


def test_a_second_run_with_the_same_addresses_changes_nothing() -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()
    _prepare(client, loader)

    result = _prepare(client, loader)

    assert (result.schema, result.configuration, result.config_id, result.version) == (
        "unchanged",
        "unchanged",
        "cfg-1",
        1,
    )
    assert client.calls[-1] == ("create_config_version", "cfg-1", f"demo-prepare-version-cfg-1-{_sha12()}")


def test_another_netbox_address_adds_version_2_under_its_own_key() -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()
    _prepare(client, loader)

    result = _prepare(client, loader, netbox_url="http://netbox.example:8080")

    assert (result.configuration, result.config_id, result.version) == ("versioned", "cfg-1", 2)
    expected_key = f"demo-prepare-version-cfg-1-{_sha12(netbox_url='http://netbox.example:8080')}"
    assert client.calls[-1] == ("create_config_version", "cfg-1", expected_key)
    assert expected_key != f"demo-prepare-version-cfg-1-{_sha12()}"


def test_two_configurations_with_the_package_name_version_the_newest_and_name_it() -> None:
    client, loader = FakeSyncClient(), FakeSchemaLoader()
    package: dict[str, Any] = {"format_version": 1, "configuration": {"name": "from-netbox"}}
    client.add("cfg-old", package)
    client.add("cfg-new", package)

    result = _prepare(client, loader)

    assert (result.config_id, result.configuration, result.version, result.same_name) == ("cfg-new", "versioned", 2, 2)
    assert client.calls[-1][:2] == ("create_config_version", "cfg-new")


def test_a_schema_load_error_stops_before_any_sync_client_call() -> None:
    client = FakeSyncClient()
    loader = FakeSchemaLoader(error=DemoSchemaError("Infrahub refused the demo schema"))

    with pytest.raises(DemoSchemaError, match="refused the demo schema"):
        _prepare(client, loader)

    assert client.calls == []


def test_the_result_never_holds_the_token() -> None:
    result = _prepare(FakeSyncClient(), FakeSchemaLoader())

    assert INFRAHUB_TOKEN not in json.dumps(result.as_dict())


def test_nothing_in_the_demo_package_touches_the_approval_setting() -> None:
    assert DEMO_MODULES
    for module in DEMO_MODULES:
        assert "INFRAHUB_SYNC_REQUIRE_APPROVAL" not in module.read_text(encoding="utf-8"), module


class _FakeInfrahub:
    """Stands in for `InfrahubClientSync`: records its settings, answers one schema load."""

    answer: SchemaLoadResponse | Exception = SchemaLoadResponse()
    seen: dict[str, Any] = {}  # noqa: RUF012 -- one test-scoped record, reset by the fixture

    def __init__(self, address: str, config: infrahub_sdk.Config) -> None:
        _FakeInfrahub.seen = {"address": address, "config": config}
        self.schema = SimpleNamespace(load=self._load)

    def _load(self, schemas: list[dict[str, Any]], wait_until_converged: bool) -> SchemaLoadResponse:  # noqa: FBT001 -- SDK keyword
        _FakeInfrahub.seen.update(schemas=schemas, wait=wait_until_converged)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def fake_infrahub(monkeypatch: pytest.MonkeyPatch) -> type[_FakeInfrahub]:
    monkeypatch.setattr(infrahub_sdk, "InfrahubClientSync", _FakeInfrahub)
    _FakeInfrahub.seen = {}
    return _FakeInfrahub


@pytest.mark.parametrize(
    ("answer", "state"),
    [
        pytest.param(SchemaLoadResponse(hash="b", previous_hash="a"), "loaded", id="new-hash"),
        pytest.param(SchemaLoadResponse(hash="a", previous_hash="a"), "unchanged", id="same-hash"),
    ],
)
def test_load_schema_reports_whether_the_schema_hash_changed(
    monkeypatch: pytest.MonkeyPatch, fake_infrahub: type[_FakeInfrahub], answer: SchemaLoadResponse, state: str
) -> None:
    monkeypatch.setattr(fake_infrahub, "answer", answer)
    files = assets.schema_files()

    assert prepare.load_schema("http://127.0.0.1:8000", INFRAHUB_TOKEN, files, 45) == state
    assert fake_infrahub.seen["address"] == "http://127.0.0.1:8000"
    assert fake_infrahub.seen["config"].api_token == INFRAHUB_TOKEN
    assert fake_infrahub.seen["config"].schema_converge_timeout == 45
    assert fake_infrahub.seen["wait"] is True
    assert len(fake_infrahub.seen["schemas"]) == 16


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(
            SchemaLoadResponse(errors={"errors": [{"message": f"token {INFRAHUB_TOKEN} denied"}]}), id="errors"
        ),
        pytest.param(ServerNotReachableError(address=f"http://x?token={INFRAHUB_TOKEN}"), id="unreachable"),
    ],
)
def test_load_schema_errors_raise_without_the_token(
    monkeypatch: pytest.MonkeyPatch, fake_infrahub: type[_FakeInfrahub], answer: object
) -> None:
    monkeypatch.setattr(fake_infrahub, "answer", answer)

    with pytest.raises(DemoSchemaError) as raised:
        prepare.load_schema("http://127.0.0.1:8000", INFRAHUB_TOKEN, assets.schema_files(), 5)

    assert "http://127.0.0.1:8000" in str(raised.value)
    assert INFRAHUB_TOKEN not in str(raised.value)
    assert "<redacted>" in str(raised.value)
