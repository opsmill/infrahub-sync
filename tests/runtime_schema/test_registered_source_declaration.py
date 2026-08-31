"""F3: registered packages may declare an installed source adapter, never a filesystem one."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from infrahub_sync.configuration import (
    ConfigurationPackage,
    ConfigurationPackageParseError,
    parse_configuration_package,
)
from infrahub_sync.plugin_loader import PluginLoader
from tests.configuration.validation_packages import package_data

INSTALLED_SOURCE_SPECS = (
    pytest.param("infrahub_sync.adapters.infrahub", id="dotted-module"),
    pytest.param("infrahub_sync.adapters.infrahub:InfrahubAdapter", id="dotted-module-and-class"),
    pytest.param("registered_entry_point_adapter", id="entry-point-name"),
)

FILESYSTEM_SOURCE_SPECS = (
    pytest.param("./examples/custom_adapter/custom_adapter_src/custom_adapter.py:MockdbAdapter", id="relative-file"),
    pytest.param("/opt/adapters/custom_adapter.py:MockdbAdapter", id="absolute-file"),
    pytest.param("adapters/custom.py", id="relative-directory"),
    pytest.param("custom_adapter.py", id="bare-python-file"),
    pytest.param("pkg.mod.custom_adapter.py", id="dotted-python-file"),
    pytest.param("~/adapters/custom", id="home-relative"),
    pytest.param("..\\adapters\\custom", id="windows-relative"),
    pytest.param("", id="empty"),
    pytest.param("pkg..mod", id="empty-segment"),
    pytest.param("pkg mod", id="space"),
)


def _content(*, source_adapter: str | None = None, destination_adapter: str | None = None) -> dict[str, Any]:
    content = copy.deepcopy(package_data())
    if source_adapter is not None:
        content["configuration"]["source"]["adapter"] = source_adapter
    if destination_adapter is not None:
        content["configuration"]["destination"]["adapter"] = destination_adapter
    return content


@pytest.mark.parametrize("spec", INSTALLED_SOURCE_SPECS)
def test_an_installed_source_adapter_is_admitted(spec: str) -> None:
    package = parse_configuration_package(_content(source_adapter=spec))

    assert package.configuration.source.adapter == spec


@pytest.mark.parametrize("spec", FILESYSTEM_SOURCE_SPECS)
def test_a_filesystem_source_adapter_is_refused(spec: str) -> None:
    with pytest.raises(
        ConfigurationPackageParseError, match=r"/configuration/source/adapter: unsupported adapter specification"
    ):
        parse_configuration_package(_content(source_adapter=spec))


@pytest.mark.parametrize("spec", INSTALLED_SOURCE_SPECS)
def test_the_destination_adapter_is_never_customizable(spec: str) -> None:
    with pytest.raises(
        ConfigurationPackageParseError, match=r"/configuration/destination/adapter: unsupported declared field"
    ):
        parse_configuration_package(_content(destination_adapter=spec))


def test_adapters_path_stays_refused() -> None:
    content = _content(source_adapter="infrahub_sync.adapters.infrahub")
    content["configuration"]["adapters_path"] = ["/opt/adapters"]

    with pytest.raises(
        ConfigurationPackageParseError, match=r"/configuration/adapters_path: unsupported declared field"
    ):
        parse_configuration_package(content)


def test_a_declared_source_adapter_enters_package_identity() -> None:
    plain = parse_configuration_package(_content())
    declared = parse_configuration_package(_content(source_adapter="infrahub_sync.adapters.infrahub"))
    other = parse_configuration_package(_content(source_adapter="infrahub_sync.adapters.infrahub:InfrahubAdapter"))

    assert declared.checksum() != plain.checksum()
    assert declared.checksum() != other.checksum()
    assert declared.declared_content()["configuration"]["source"]["adapter"] == "infrahub_sync.adapters.infrahub"


def test_a_package_without_a_source_adapter_keeps_its_exact_declared_content() -> None:
    # Admitting the field must not change the identity of every package that omits it.
    content = parse_configuration_package(_content()).declared_content()

    assert "adapter" not in content["configuration"]["source"]
    assert "adapter" not in content["configuration"]["destination"]


# --- resolution of an admitted declaration ---------------------------------------------


class _FakeEntryPoint:
    def __init__(self, name: str, value: type) -> None:
        self.name = name
        self._value = value

    def load(self) -> type:
        return self._value


class _FakeEntryPoints:
    def __init__(self, entry_point: _FakeEntryPoint) -> None:
        self._entry_point = entry_point

    def select(self, *, group: str, name: str) -> tuple[_FakeEntryPoint, ...]:
        if group == "infrahub_sync.adapters" and name == self._entry_point.name:
            return (self._entry_point,)
        return ()


@pytest.fixture(name="registered_entry_point")
def _registered_entry_point(monkeypatch: pytest.MonkeyPatch) -> type:
    """Publish one adapter under the plugin entry-point group, as an install would."""
    from infrahub_sync.adapters.infrahub import InfrahubAdapter

    monkeypatch.setattr(
        "infrahub_sync.plugin_loader.entry_points",
        lambda: _FakeEntryPoints(_FakeEntryPoint("registered_entry_point_adapter", InfrahubAdapter)),
    )
    return InfrahubAdapter


@pytest.mark.parametrize("spec", INSTALLED_SOURCE_SPECS)
def test_every_admitted_declaration_resolves_through_installed_only_loading(
    spec: str, registered_entry_point: type
) -> None:
    package: ConfigurationPackage = parse_configuration_package(_content(source_adapter=spec))
    declared = package.configuration.source.adapter
    assert declared is not None

    resolved = PluginLoader.installed_only_loader().resolve(declared)

    assert resolved is registered_entry_point
