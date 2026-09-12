"""F2: registered resolution cannot reach filesystem plugins, by construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from infrahub_sync import SyncAdapter, SyncInstance
from infrahub_sync.plugin_loader import (
    PluginLoadError,
    resolve_installed_adapter_class,
    resolve_installed_model_base,
)
from infrahub_sync.utils import import_adapter

_ADAPTER_SOURCE = """
from diffsync import Adapter, DiffSyncModel


class SideloadedModel(DiffSyncModel):
    _modelname = "SideloadedModel"
    _identifiers = ("name",)
    name: str


class SideloadedAdapter(Adapter):
    type = "Sideloaded"
"""


@pytest.fixture(name="sideloaded")
def _sideloaded(tmp_path: Path) -> Path:
    """A working adapter module on disk, reachable only through filesystem resolution."""
    package = tmp_path / "sideloaded"
    package.mkdir()
    (package / "__init__.py").write_text(_ADAPTER_SOURCE, encoding="utf-8")
    return tmp_path


def _instance(name: str, *, adapters_path: list[str] | None = None) -> SyncInstance:
    return SyncInstance(
        name="registered-resolution",
        source=SyncAdapter(name=name),
        destination=SyncAdapter(name="infrahub"),
        adapters_path=adapters_path,
        directory="/nonexistent",
    )


def test_a_configured_adapter_path_cannot_reach_a_filesystem_plugin(sideloaded: Path) -> None:
    instance = _instance("sideloaded", adapters_path=[str(sideloaded)])

    with pytest.raises(PluginLoadError):
        resolve_installed_adapter_class(instance.source)


def test_the_adapter_paths_environment_variable_cannot_reach_a_filesystem_plugin(
    sideloaded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_ADAPTER_PATHS", str(sideloaded))
    instance = _instance("sideloaded")

    with pytest.raises(PluginLoadError):
        resolve_installed_adapter_class(instance.source)


def test_the_working_directory_cannot_reach_a_filesystem_plugin(
    sideloaded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(sideloaded)
    instance = _instance("sideloaded")

    with pytest.raises(PluginLoadError):
        resolve_installed_adapter_class(instance.source)


def test_a_filesystem_model_base_is_unreachable_too(sideloaded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_ADAPTER_PATHS", str(sideloaded))
    instance = _instance("sideloaded", adapters_path=[str(sideloaded)])

    with pytest.raises(PluginLoadError):
        resolve_installed_model_base(instance.source)


def test_the_shipped_filesystem_example_adapter_is_unreachable() -> None:
    # The reviewer's reproduction: the custom-adapter example declares a `./...py:Class`
    # spec, which registered admission must not be able to load.
    instance = SyncInstance(
        name="registered-resolution",
        source=SyncAdapter(
            name="mockdb",
            adapter="./examples/custom_adapter/custom_adapter_src/custom_adapter.py:MockdbAdapter",
        ),
        destination=SyncAdapter(name="infrahub"),
        directory="/nonexistent",
    )

    with pytest.raises(PluginLoadError):
        resolve_installed_adapter_class(instance.source)


def test_a_bundled_adapter_still_resolves() -> None:
    instance = _instance("infrahub")

    resolved = resolve_installed_adapter_class(instance.source)

    assert resolved.__module__ == "infrahub_sync.adapters.infrahub"


def test_a_dotted_installed_adapter_still_resolves() -> None:
    instance = SyncInstance(
        name="registered-resolution",
        source=SyncAdapter(name="custom", adapter="infrahub_sync.adapters.infrahub:InfrahubAdapter"),
        destination=SyncAdapter(name="infrahub"),
        directory="/nonexistent",
    )

    resolved = resolve_installed_adapter_class(instance.source)

    assert resolved.__name__ == "InfrahubAdapter"


# --- the legacy local path keeps the resolution it had -----------------------------------


def test_the_legacy_path_still_resolves_a_configured_adapter_path(sideloaded: Path) -> None:
    # Registered admission is what narrowed; `import_adapter` serves the local CLI, whose
    # adapters_path and environment resolution must keep working until it is removed.
    instance = _instance("sideloaded", adapters_path=[str(sideloaded)])

    resolved = import_adapter(sync_instance=instance, adapter=instance.source)

    assert resolved is not None
    assert resolved.__name__ == "SideloadedAdapter"


def test_the_legacy_path_still_resolves_an_adapter_paths_environment_plugin(
    sideloaded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_ADAPTER_PATHS", str(sideloaded))
    instance = _instance("sideloaded")

    resolved = import_adapter(sync_instance=instance, adapter=instance.source)

    assert resolved is not None
    assert resolved.__name__ == "SideloadedAdapter"


def test_the_legacy_path_still_resolves_an_explicit_filesystem_spec() -> None:
    instance = SyncInstance(
        name="registered-resolution",
        source=SyncAdapter(
            name="mockdb",
            adapter="./examples/custom_adapter/custom_adapter_src/custom_adapter.py:MockdbAdapter",
        ),
        destination=SyncAdapter(name="infrahub"),
        directory="/nonexistent",
    )

    resolved = import_adapter(sync_instance=instance, adapter=instance.source)

    assert resolved is not None
    assert resolved.__name__ == "MockdbAdapter"
