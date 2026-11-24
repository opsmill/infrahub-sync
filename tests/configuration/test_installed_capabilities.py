"""Capability declarations an installed distribution publishes for its own adapter.

A package may name a source adapter that is not bundled when an installed distribution
declares that adapter's capabilities in the ``infrahub_sync.adapter_capabilities`` entry
point group. Bundled adapters win under any spelling the loader would match, the
declaration must come from installed code (judged before it is imported), only a source can
be declared, and anything that is not exactly one admissible declaration for the name
leaves the adapter missing.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from infrahub_sync import plugin_loader
from infrahub_sync.configuration import capabilities as capabilities_module
from infrahub_sync.configuration.capabilities import (
    INSTALLED_CAPABILITIES_GROUP,
    AdapterConfigurationCapabilities,
    UnknownAdapterCapabilitiesError,
    get_adapter_capabilities,
    installed_adapter_capabilities,
)
from infrahub_sync.configuration.validation import collect_findings
from tests.configuration.validation_packages import package, package_data

NAME = "examplesource"
SOURCE_ONLY = frozenset({"source"})


def _declaration(name: str = NAME, roles: frozenset[str] = SOURCE_ONLY) -> AdapterConfigurationCapabilities:
    return AdapterConfigurationCapabilities(
        adapter_name=name,
        roles=roles,  # ty: ignore[invalid-argument-type]
        allowed_settings=frozenset({"token", "url"}),
        credential_setting_paths=("token",),
    )


def declare() -> AdapterConfigurationCapabilities:
    return _declaration()


@dataclass
class _EntryPoint:
    name: str
    target: Callable[[], Any] | Exception
    module: str = __name__
    group: str = INSTALLED_CAPABILITIES_GROUP
    loaded: list[bool] = field(default_factory=list)

    def load(self) -> object:
        self.loaded.append(True)
        if isinstance(self.target, Exception):
            raise self.target
        return self.target


class _EntryPoints:
    def __init__(self, entries: list[_EntryPoint]) -> None:
        self._entries = entries

    def select(self, *, group: str, name: str) -> list[_EntryPoint]:
        return [entry for entry in self._entries if entry.group == group and entry.name == name]


@dataclass
class _Judged:
    before_import: list[str] = field(default_factory=list)
    after_import: list[str] = field(default_factory=list)


def _install(monkeypatch: pytest.MonkeyPatch, *entries: _EntryPoint, installed: bool = True) -> _Judged:
    """Publish ``entries`` as the installed entry points, and admit their modules if ``installed``.

    With ``installed`` false the real installed-code rule judges them.
    """
    judged = _Judged()
    monkeypatch.setattr(capabilities_module, "entry_points", lambda: _EntryPoints(list(entries)))
    if installed:

        def _installed(spec_path: str) -> bool:
            judged.before_import.append(spec_path)
            return True

        def _admitted(_module: object, spec_path: str) -> bool:
            judged.after_import.append(spec_path)
            return True

        monkeypatch.setattr(plugin_loader, "is_installed_distribution_module", _installed)
        monkeypatch.setattr(plugin_loader, "module_origin_is_admitted", _admitted)
    return judged


def _entry(target: Callable[[], Any] | Exception = declare, name: str = NAME, module: str = __name__) -> _EntryPoint:
    return _EntryPoint(name=name, target=target, module=module)


def _findings(data: dict[str, Any]) -> list[tuple[str, str]]:
    return [(finding.code, finding.location) for finding in collect_findings(package(data))]


def _source_named(name: str) -> list[tuple[str, str]]:
    data = package_data()
    data["configuration"]["source"]["name"] = name
    return _findings(data)


def test_an_installed_declaration_makes_its_adapter_admissible(monkeypatch: pytest.MonkeyPatch) -> None:
    judged = _install(monkeypatch, _entry())

    assert _source_named(NAME) == []
    assert get_adapter_capabilities(NAME) == _declaration()
    # The entry point's own module is judged, before and after it is imported.
    assert set(judged.before_import) == {__name__}
    assert set(judged.after_import) == {__name__}


def test_without_a_declaration_the_adapter_is_still_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch)

    assert _source_named(NAME) == [("missing-adapter", "/configuration/source")]
    with pytest.raises(UnknownAdapterCapabilitiesError):
        get_adapter_capabilities(NAME)


@pytest.mark.parametrize("name", ["netbox", "NetBox", "net_box", "net-box", "generic_rest_api", "ipfabric_sync"])
def test_a_bundled_adapter_is_never_resolved_from_an_installed_declaration(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    # Under any spelling the loader would resolve to a bundled adapter, the bundled one stands.
    entry = _entry(lambda: _declaration(name), name=name)
    _install(monkeypatch, entry)

    assert installed_adapter_capabilities(name) is None
    assert entry.loaded == []


def test_code_outside_an_installed_distribution_is_refused_before_it_is_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No distribution ships this module, so the real rule refuses it without loading it,
    # whatever module the callable claims to come from.
    def spoofed() -> AdapterConfigurationCapabilities:
        return _declaration()

    spoofed.__module__ = "infrahub_sync.configuration.capabilities"
    entry = _entry(spoofed, module="a_checkout_only_module.declarations")
    _install(monkeypatch, entry, installed=False)

    assert installed_adapter_capabilities(NAME) is None
    assert entry.loaded == []
    assert "a_checkout_only_module.declarations" not in sys.modules
    assert _source_named(NAME) == [("missing-adapter", "/configuration/source")]


def test_the_loaded_module_is_judged_by_the_entry_points_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # Whatever module the callable claims, the module judged after loading is the entry point's.
    def declared_elsewhere() -> AdapterConfigurationCapabilities:
        return _declaration()

    declared_elsewhere.__module__ = "some.other.module"
    judged = _install(monkeypatch, _entry(declared_elsewhere))

    assert installed_adapter_capabilities(NAME) == _declaration()
    assert judged.after_import == [__name__]


def test_an_entry_point_into_the_bundled_package_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _entry(module="infrahub_sync.configuration.capabilities")
    _install(monkeypatch, entry)

    assert installed_adapter_capabilities(NAME) is None
    assert entry.loaded == []


def test_a_test_module_is_not_installed_code(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _entry()
    _install(monkeypatch, entry, installed=False)

    assert installed_adapter_capabilities(NAME) is None
    assert entry.loaded == []


@pytest.mark.parametrize(
    "target",
    [
        pytest.param(ImportError("no such module"), id="fails-to-load"),
        pytest.param(lambda: (_ for _ in ()).throw(RuntimeError("boom")), id="raises"),
        pytest.param(lambda: {"adapter_name": NAME}, id="not-a-declaration"),
        pytest.param(lambda: _declaration("someotheradapter"), id="declares-another-name"),
        pytest.param(lambda: _declaration(roles=frozenset({"destination"})), id="declares-a-destination"),
        pytest.param(lambda: _declaration(roles=frozenset({"source", "destination"})), id="declares-both-roles"),
        pytest.param(
            lambda: replace(
                _declaration(), destination_schema_validation=True, destination_schema_accessor=lambda *_: None
            ),
            id="carries-a-destination-schema-check",
        ),
        pytest.param(
            lambda: replace(_declaration(), destination_access_accessor=lambda *_: None),
            id="carries-a-destination-accessor",
        ),
    ],
)
def test_a_declaration_that_is_not_a_source_declaration_for_the_name_declares_nothing(
    monkeypatch: pytest.MonkeyPatch, target: Callable[[], Any] | Exception
) -> None:
    _install(monkeypatch, _entry(target))

    assert installed_adapter_capabilities(NAME) is None
    assert _source_named(NAME) == [("missing-adapter", "/configuration/source")]


def test_a_subclass_of_the_declaration_is_not_a_declaration(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Loose(AdapterConfigurationCapabilities):
        pass

    loose = _Loose(adapter_name=NAME, roles=SOURCE_ONLY)  # ty: ignore[invalid-argument-type]
    _install(monkeypatch, _entry(lambda: loose))

    assert installed_adapter_capabilities(NAME) is None


def test_two_declarations_for_one_name_declare_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _entry(), _entry())

    assert installed_adapter_capabilities(NAME) is None


def test_an_installed_adapter_cannot_be_the_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _entry())
    data = package_data()
    data["configuration"]["destination"]["name"] = NAME

    destination = [finding for finding in _findings(data) if finding[1].startswith("/configuration/destination")]

    # A source-only declaration does not qualify an adapter as the destination.
    assert destination == [("adapter-role-mismatch", "/configuration/destination")]


def test_the_declaration_is_read_when_asked_for_not_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    # A stored version validated again later is judged against what is installed then.
    _install(monkeypatch, _entry())
    assert _source_named(NAME) == []

    _install(monkeypatch)
    assert _source_named(NAME) == [("missing-adapter", "/configuration/source")]


def test_an_installed_declaration_judges_its_adapters_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _entry())
    data = package_data()
    data["configuration"]["source"]["name"] = NAME
    data["configuration"]["source"]["settings"]["bogus"] = 1

    assert _findings(data) == [("undeclared-setting", "/configuration/source/settings/bogus")]
