"""AR5: registered credentials win at the real adapter construction seams."""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import pytest

from infrahub_sync import SyncAdapter, SyncConfig, SyncInstance
from infrahub_sync.utils import PlanApplier, get_potenda_from_instance


@dataclass(frozen=True)
class AdapterRow:
    """One bundled adapter's observable credential paths."""

    name: str
    settings: dict[str, str]
    ambient: dict[str, str]
    expected: dict[str, str]
    legacy: dict[str, str]


ROWS = (
    AdapterRow(
        "aci",
        {"url": "https://registered-aci", "username": "registered-user", "password": "registered-pass"},
        {
            "CISCO_APIC_URL": "https://ambient-aci",
            "CISCO_APIC_USERNAME": "ambient-user",
            "CISCO_APIC_PASSWORD": "ambient-pass",
        },
        {"url": "https://registered-aci", "username": "registered-user", "password": "registered-pass"},
        {"url": "https://ambient-aci", "username": "ambient-user", "password": "ambient-pass"},
    ),
    AdapterRow(
        "infrahub",
        {"url": "https://registered-infrahub", "token": "registered-token"},
        {"INFRAHUB_ADDRESS": "https://ambient-infrahub", "INFRAHUB_API_TOKEN": "ambient-token"},
        {"url": "https://registered-infrahub", "token": "registered-token"},
        {"url": "https://ambient-infrahub", "token": "ambient-token"},
    ),
    AdapterRow(
        "netbox",
        {"url": "https://registered-netbox", "token": "registered-token"},
        {"NETBOX_ADDRESS": "https://ambient-netbox", "NETBOX_TOKEN": "ambient-token"},
        {"url": "https://registered-netbox", "token": "registered-token"},
        {"url": "https://ambient-netbox", "token": "ambient-token"},
    ),
    AdapterRow(
        "nautobot",
        {"url": "https://registered-nautobot", "token": "registered-token"},
        {"NAUTOBOT_ADDRESS": "https://ambient-nautobot", "NAUTOBOT_TOKEN": "ambient-token"},
        {"url": "https://registered-nautobot", "token": "registered-token"},
        {"url": "https://ambient-nautobot", "token": "ambient-token"},
    ),
    AdapterRow(
        "prometheus",
        {
            "url": "https://registered-prometheus",
            "username": "registered-user",
            "password": "registered-pass",
            "token": "registered-token",
            "auth_method": "bearer",
        },
        {
            "PROM_URL": "https://ambient-prometheus",
            "PROM_USERNAME": "ambient-user",
            "PROM_PASSWORD": "ambient-pass",
            "PROM_TOKEN": "ambient-token",
        },
        {
            "url": "https://registered-prometheus",
            "username": "registered-user",
            "password": "registered-pass",
            "token": "registered-token",
        },
        {
            "url": "https://ambient-prometheus",
            "username": "ambient-user",
            "password": "ambient-pass",
            "token": "ambient-token",
        },
    ),
    AdapterRow(
        "ipfabricsync",
        {"base_url": "https://registered-ipfabric", "auth": "registered-token"},
        {"IPF_URL": "https://ambient-ipfabric", "IPF_TOKEN": "ambient-token"},
        {"url": "https://registered-ipfabric", "token": "registered-token"},
        {"url": "https://registered-ipfabric", "token": "registered-token"},
    ),
    AdapterRow(
        "peeringmanager",
        {"url": "https://registered-peering", "token": "registered-token"},
        {"PEERING_MANAGER_ADDRESS": "https://ambient-peering", "PEERING_MANAGER_TOKEN": "ambient-token"},
        {"url": "https://registered-peering", "token": "registered-token"},
        {"url": "https://ambient-peering", "token": "ambient-token"},
    ),
    AdapterRow(
        "genericrestapi",
        {"url": "https://registered-generic", "token": "registered-token"},
        {"GENERICRESTAPI_URL": "https://ambient-generic", "GENERICRESTAPI_TOKEN": "ambient-token"},
        {"url": "https://registered-generic", "token": "registered-token"},
        {"url": "https://ambient-generic", "token": "ambient-token"},
    ),
)


def _install_optional_sdk_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make optional imports deterministic before each dynamic adapter import."""
    pynetbox = cast("Any", types.ModuleType("pynetbox"))
    pynetbox.api = lambda *_args, **_kwargs: types.SimpleNamespace()
    pynautobot = cast("Any", types.ModuleType("pynautobot"))
    pynautobot.api = lambda **_kwargs: types.SimpleNamespace()
    core = types.ModuleType("pynautobot.core")
    query = cast("Any", types.ModuleType("pynautobot.core.query"))
    query.RequestError = RuntimeError
    ipfabric = cast("Any", types.ModuleType("ipfabric"))
    ipfabric.IPFClient = lambda **_kwargs: types.SimpleNamespace()
    prometheus_client = types.ModuleType("prometheus_client")
    prometheus_parser = cast("Any", types.ModuleType("prometheus_client.parser"))
    prometheus_parser.text_string_to_metric_families = lambda _text: ()
    for name, module in {
        "pynetbox": pynetbox,
        "pynautobot": pynautobot,
        "pynautobot.core": core,
        "pynautobot.core.query": query,
        "ipfabric": ipfabric,
        "prometheus_client": prometheus_client,
        "prometheus_client.parser": prometheus_parser,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def _adapter_module(monkeypatch: pytest.MonkeyPatch, row: AdapterRow) -> types.ModuleType:
    _install_optional_sdk_stubs(monkeypatch)
    module_name = f"infrahub_sync.adapters.{row.name}"
    # Record the pre-test entry, or its absence, so teardown restores the real module or
    # drops the stub-bound one. `monkeypatch.delitem` records nothing when the key is
    # absent, which is the normal state here, and would leave the stub-bound module cached.
    # The re-import also rebinds the parent package attribute, which `sys.modules` alone
    # does not cover: an attribute walk would still reach the stub-bound module.
    monkeypatch.setitem(sys.modules, module_name, types.ModuleType(module_name))
    monkeypatch.setattr(
        importlib.import_module("infrahub_sync.adapters"), row.name, types.ModuleType(module_name), raising=False
    )
    del sys.modules[module_name]
    return importlib.import_module(module_name)


def _class_name(row: AdapterRow) -> str:
    return "".join(part.title() for part in row.name.split("_")) + "Adapter"


def _instance(row: AdapterRow, settings: Mapping[str, object]) -> SyncInstance:
    return SyncInstance(
        name=f"ar5-{row.name}",
        directory="/registered-runtime",
        source=SyncAdapter(name=row.name, settings=dict(settings)),
        destination=SyncAdapter(name=row.name, settings=dict(settings)),
    )


def _capture_client(
    monkeypatch: pytest.MonkeyPatch, row: AdapterRow, module: types.ModuleType
) -> list[dict[str, object]]:
    observed: list[dict[str, object]] = []
    dynamic_module = cast("Any", module)

    def capture(**kwargs: object) -> object:
        observed.append(kwargs)
        if row.name == "infrahub":
            return types.SimpleNamespace(
                get=lambda *_args, **_kwargs: None,
                schema=types.SimpleNamespace(all=lambda **_kwargs: {}),
            )
        return types.SimpleNamespace(get=lambda *_args, **_kwargs: types.SimpleNamespace(json=lambda: {"imdata": []}))

    if row.name == "aci":
        monkeypatch.setattr(dynamic_module, "AciApiClient", capture)
    elif row.name == "infrahub":

        class MissingNodeError(Exception):
            pass

        monkeypatch.setattr(dynamic_module, "NodeNotFoundError", MissingNodeError)
        monkeypatch.setattr(dynamic_module, "Config", lambda **kwargs: kwargs)
        monkeypatch.setattr(dynamic_module, "InfrahubClientSync", capture)
    elif row.name == "netbox":
        monkeypatch.setattr(dynamic_module.pynetbox, "api", lambda url, token: capture(url=url, token=token))
    elif row.name == "nautobot":
        monkeypatch.setattr(dynamic_module.pynautobot, "api", capture)
    elif row.name == "prometheus":
        monkeypatch.setattr(dynamic_module, "PrometheusScrapeClient", capture)
    elif row.name == "ipfabricsync":
        monkeypatch.setattr(dynamic_module, "IPFClient", capture)
    else:
        monkeypatch.setattr(importlib.import_module("infrahub_sync.adapters.genericrestapi"), "RestApiClient", capture)
    return observed


def _observed(row: AdapterRow, kwargs: dict[str, Any]) -> dict[str, object]:
    if row.name == "aci":
        return {
            "url": kwargs["base_url"].removesuffix("/api/"),
            "username": kwargs["username"],
            "password": kwargs["password"],
        }
    if row.name == "infrahub":
        return {"url": kwargs["address"], "token": kwargs["config"]["api_token"]}
    if row.name in {"netbox", "nautobot"}:
        return {"url": kwargs["url"], "token": kwargs["token"]}
    if row.name == "prometheus":
        return {
            "url": kwargs["base_url"],
            "username": kwargs["username"],
            "password": kwargs["password"],
            "token": kwargs["api_token"],
        }
    if row.name == "ipfabricsync":
        return {"url": kwargs["base_url"], "token": kwargs["auth"]}
    return {"url": str(kwargs["base_url"]).removesuffix("/api/v0").removesuffix("/api"), "token": kwargs["api_token"]}


def _basic_observed(kwargs: dict[str, object]) -> dict[str, object]:
    """Return the Generic REST client's complete basic-auth input."""
    return {
        "url": str(kwargs["base_url"]).removesuffix("/api/v0").removesuffix("/api"),
        "username": kwargs["username"],
        "password": kwargs["password"],
    }


def _patch_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    class Engine:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    monkeypatch.setattr("infrahub_sync.utils.Potenda", Engine)
    monkeypatch.setattr("infrahub_sync.utils.stored_run_dir", lambda *_args, **_kwargs: tmp_path)


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row.name)
def test_registered_values_win_over_ambient_through_get_potenda(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, row: AdapterRow
) -> None:
    """Both source and destination construction observe worker-resolved values."""
    module = _adapter_module(monkeypatch, row)
    observed = _capture_client(monkeypatch, row, module)
    instance = _instance(row, {**row.settings, "_infrahub_sync_registered_context": True})
    for name, value in row.ambient.items():
        monkeypatch.setenv(name, value)
    _patch_engine(monkeypatch, tmp_path)
    monkeypatch.setattr("infrahub_sync.utils.import_adapter", lambda **_kwargs: getattr(module, _class_name(row)))
    get_potenda_from_instance(instance, run_id="ar5-registered")
    assert [_observed(row, call) for call in observed] == [row.expected, row.expected]


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row.name)
def test_legacy_values_keep_committed_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, row: AdapterRow
) -> None:
    """No marker preserves every accepted legacy lookup order."""
    module = _adapter_module(monkeypatch, row)
    observed = _capture_client(monkeypatch, row, module)
    instance = _instance(row, row.settings)
    for name, value in row.ambient.items():
        monkeypatch.setenv(name, value)
    _patch_engine(monkeypatch, tmp_path)
    monkeypatch.setattr("infrahub_sync.utils.import_adapter", lambda **_kwargs: getattr(module, _class_name(row)))
    get_potenda_from_instance(instance, run_id="ar5-legacy")
    assert [_observed(row, call) for call in observed] == [row.legacy, row.legacy]


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row.name)
def test_apply_constructs_only_registered_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, row: AdapterRow
) -> None:
    """Plan apply constructs no source and its destination sees registered values."""
    module = _adapter_module(monkeypatch, row)
    observed = _capture_client(monkeypatch, row, module)
    instance = _instance(row, {**row.settings, "_infrahub_sync_registered_context": True})
    for name, value in row.ambient.items():
        monkeypatch.setenv(name, value)
    _patch_engine(monkeypatch, tmp_path)
    calls: list[str] = []

    def adapter_factory(**kwargs: object) -> type[object]:
        calls.append(cast("SyncAdapter", kwargs["adapter"]).name)
        return getattr(module, _class_name(row))

    monkeypatch.setattr("infrahub_sync.utils.import_adapter", adapter_factory)
    PlanApplier.open_existing(instance, run_id="ar5-apply")
    assert calls == [row.name]
    assert [_observed(row, call) for call in observed] == [row.expected]


@pytest.mark.parametrize("row", [ROWS[5], ROWS[6]], ids=lambda row: row.name)
def test_registered_writebacks_never_copy_ambient_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, row: AdapterRow
) -> None:
    """The two settings write-back paths cannot preserve ambient credentials."""
    module = _adapter_module(monkeypatch, row)
    _capture_client(monkeypatch, row, module)
    instance = _instance(row, {**row.settings, "_infrahub_sync_registered_context": True})
    for name, value in row.ambient.items():
        monkeypatch.setenv(name, value)
    _patch_engine(monkeypatch, tmp_path)
    monkeypatch.setattr("infrahub_sync.utils.import_adapter", lambda **_kwargs: getattr(module, _class_name(row)))
    get_potenda_from_instance(instance, run_id="ar5-writeback")
    for adapter in (instance.source, instance.destination):
        assert adapter.settings is not None
        assert not any(value in row.ambient.values() for value in adapter.settings.values())


def test_peeringmanager_defaults_reach_genericrestapi_without_overriding_registered_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """Delegated custom env-variable defaults cannot trump the registered URL/token."""
    row = ROWS[6]
    module = _adapter_module(monkeypatch, row)
    observed = _capture_client(monkeypatch, row, module)
    instance = _instance(row, {**row.settings, "_infrahub_sync_registered_context": True})
    for name, value in row.ambient.items():
        monkeypatch.setenv(name, value)
    _patch_engine(monkeypatch, tmp_path)
    monkeypatch.setattr("infrahub_sync.utils.import_adapter", lambda **_kwargs: module.PeeringmanagerAdapter)
    get_potenda_from_instance(instance, run_id="ar5-peering")
    assert [_observed(row, call) for call in observed] == [row.expected, row.expected]
    for adapter in (instance.source, instance.destination):
        assert adapter.settings is not None
        assert adapter.settings == {**row.settings, "_infrahub_sync_registered_context": True}


@pytest.mark.parametrize("adapter_name", ["genericrestapi", "peeringmanager"])
@pytest.mark.parametrize("construction", ["normal", "apply"])
def test_registered_basic_auth_wins_over_ambient_at_every_genericrestapi_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    adapter_name: str,
    construction: str,
) -> None:
    """Delegated Generic REST basic auth must not recover ambient credentials."""
    row = next(candidate for candidate in ROWS if candidate.name == adapter_name)
    module = _adapter_module(monkeypatch, row)
    observed = _capture_client(monkeypatch, row, module)
    instance = _instance(
        row,
        {
            "url": f"https://registered-{adapter_name}",
            "auth_method": "basic",
            "username": "registered-basic-user",
            "password": "registered-basic-password",
            "_infrahub_sync_registered_context": True,
        },
    )
    monkeypatch.setenv("USERNAME", "ambient-basic-user")
    monkeypatch.setenv("PASSWORD", "ambient-basic-password")
    _patch_engine(monkeypatch, tmp_path)
    monkeypatch.setattr("infrahub_sync.utils.import_adapter", lambda **_kwargs: getattr(module, _class_name(row)))

    if construction == "normal":
        get_potenda_from_instance(instance, run_id="ar5-basic-normal")
    else:
        PlanApplier.open_existing(instance, run_id="ar5-basic-apply")

    expected = {
        "url": f"https://registered-{adapter_name}",
        "username": "registered-basic-user",
        "password": "registered-basic-password",
    }
    assert [_basic_observed(call) for call in observed] == (
        [expected, expected] if construction == "normal" else [expected]
    )


@pytest.mark.parametrize("adapter_name", ["genericrestapi", "peeringmanager"])
@pytest.mark.parametrize("auth_method", ["token", "basic"])
@pytest.mark.parametrize("environment", ["declared", "namespaced", "alias", "bare"])
def test_rest_adapter_uses_only_its_namespaced_environment(
    monkeypatch: pytest.MonkeyPatch,
    adapter_name: str,
    auth_method: str,
    environment: str,
) -> None:
    """Declared values survive bare ambient names; namespaced values take precedence."""
    environment_prefix = "GENERICRESTAPI" if adapter_name == "genericrestapi" else "PEERING_MANAGER"
    url_name = "GENERICRESTAPI_URL" if adapter_name == "genericrestapi" else "PEERING_MANAGER_ADDRESS"
    row = next(candidate for candidate in ROWS if candidate.name == adapter_name)
    module = _adapter_module(monkeypatch, row)
    observed = _capture_client(monkeypatch, row, module)
    settings = {
        "url": "https://declared-rest",
        "auth_method": auth_method,
        "token": "declared-token",
        "username": "declared-user",
        "password": "declared-password",
    }
    declared = SyncAdapter(name=adapter_name, settings=settings)
    caller_settings = declared.settings
    config = SyncConfig(name="rest-environment", source=declared, destination=SyncAdapter(name="infrahub"))
    for name in ("URL", "ADDRESS", "TOKEN", "USERNAME", "PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    for name in (
        f"{environment_prefix}_URL",
        f"{environment_prefix}_ADDRESS",
        f"{environment_prefix}_TOKEN",
        f"{environment_prefix}_USERNAME",
        f"{environment_prefix}_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    if environment == "bare":
        for name in ("URL", "ADDRESS", "TOKEN", "USERNAME", "PASSWORD"):
            monkeypatch.setenv(name, "https://bare-rest" if name in {"URL", "ADDRESS"} else f"bare-{name.lower()}")
    elif environment in {"namespaced", "alias"}:
        selected_url_name = (
            f"{environment_prefix}_ADDRESS" if adapter_name == "genericrestapi" else "PEERING_MANAGER_URL"
        )
        monkeypatch.setenv(url_name if environment == "namespaced" else selected_url_name, "https://namespaced-rest")
        for name in ("TOKEN", "USERNAME", "PASSWORD"):
            value = "namespaced-user" if name == "USERNAME" else f"namespaced-{name.lower()}"
            monkeypatch.setenv(f"{environment_prefix}_{name}", value)

    constructed = getattr(module, _class_name(row))(target="source", adapter=declared, config=config)

    assert len(observed) == 1
    assert observed[0]["base_url"] == (
        "https://namespaced-rest" if environment in {"namespaced", "alias"} else "https://declared-rest"
    ) + ("/api" if adapter_name == "peeringmanager" else "/api/v0")
    credential_prefix = "namespaced" if environment in {"namespaced", "alias"} else "declared"
    if auth_method == "token":
        assert observed[0]["api_token"] == f"{credential_prefix}-token"
    else:
        assert observed[0]["username"] == f"{credential_prefix}-user"
        assert observed[0]["password"] == f"{credential_prefix}-password"
    assert declared.settings is caller_settings
    assert caller_settings == settings
    if adapter_name == "peeringmanager":
        assert constructed.settings is not caller_settings
        assert constructed.settings["username_env_vars"] == ["PEERING_MANAGER_USERNAME"]
        assert constructed.settings["password_env_vars"] == ["PEERING_MANAGER_PASSWORD"]


@pytest.mark.parametrize("environment", ["declared", "namespaced"])
def test_ipfabric_resolves_into_local_settings_without_mutating_caller(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    """Environment fallback reaches IP Fabric without writing into SyncAdapter settings."""
    row = ROWS[5]
    module = _adapter_module(monkeypatch, row)
    observed = _capture_client(monkeypatch, row, module)
    settings: dict[str, Any] = {"verify_ssl": False}
    if environment == "declared":
        settings.update({"base_url": "https://declared-ipfabric", "auth": "declared-token"})
    declared = SyncAdapter(name="ipfabricsync", settings=settings)
    caller_settings = declared.settings
    config = SyncConfig(name="ipfabric-environment", source=declared, destination=SyncAdapter(name="infrahub"))
    monkeypatch.setenv("IPF_URL", "https://namespaced-ipfabric")
    monkeypatch.setenv("IPF_TOKEN", "namespaced-token")

    getattr(module, _class_name(row))(target="source", adapter=declared, config=config)

    assert observed == [
        {
            "verify_ssl": False,
            "base_url": "https://declared-ipfabric" if environment == "declared" else "https://namespaced-ipfabric",
            "auth": "declared-token" if environment == "declared" else "namespaced-token",
        }
    ]
    assert declared.settings is caller_settings
    assert caller_settings == settings
