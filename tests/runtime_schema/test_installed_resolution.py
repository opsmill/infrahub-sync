"""AR9: installed resolution never reaches generated Python in the configuration directory."""

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


def _qualified(cls: type) -> str:
    """Name a resolved class without comparing identity.

    Other suites re-import the bundled adapter modules dynamically, so two live class
    objects for one adapter can coexist in a session; the qualified name is what this
    test is about anyway.
    """
    return f"{cls.__module__}.{cls.__name__}"


def _instance(directory: Path) -> SyncInstance:
    return SyncInstance(
        name="installed-resolution",
        source=SyncAdapter(name="infrahub"),
        destination=SyncAdapter(name="infrahub"),
        directory=str(directory),
    )


def _write_generated_wrapper(directory: Path) -> None:
    package = directory / "infrahub"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    (package / "sync_adapter.py").write_text(
        "class InfrahubSync:\n    generated = True\n",
        encoding="utf-8",
    )


def test_installed_resolution_ignores_a_generated_wrapper(tmp_path: Path) -> None:
    _write_generated_wrapper(tmp_path)
    instance = _instance(tmp_path)

    resolved = resolve_installed_adapter_class(instance.destination)

    assert _qualified(resolved) == "infrahub_sync.adapters.infrahub.InfrahubAdapter"


def test_the_generated_wrapper_still_takes_precedence_for_the_legacy_path(tmp_path: Path) -> None:
    _write_generated_wrapper(tmp_path)
    instance = _instance(tmp_path)

    resolved = import_adapter(sync_instance=instance, adapter=instance.destination)

    assert _qualified(resolved) != "infrahub_sync.adapters.infrahub.InfrahubAdapter"
    assert resolved.generated is True


def test_the_installed_model_base_matches_the_generated_wrapper_spec(tmp_path: Path) -> None:
    instance = _instance(tmp_path)

    assert _qualified(resolve_installed_model_base(instance.destination)) == (
        "infrahub_sync.adapters.infrahub.InfrahubModel"
    )


def test_an_explicit_adapter_spec_resolves_its_module_for_the_model_base(tmp_path: Path) -> None:
    instance = SyncInstance(
        name="explicit-spec",
        source=SyncAdapter(name="infrahub", adapter="infrahub_sync.adapters.infrahub:InfrahubAdapter"),
        destination=SyncAdapter(name="infrahub"),
        directory=str(tmp_path),
    )

    assert _qualified(resolve_installed_model_base(instance.source)) == (
        "infrahub_sync.adapters.infrahub.InfrahubModel"
    )


def test_an_unresolvable_installed_model_base_refuses(tmp_path: Path) -> None:
    instance = SyncInstance(
        name="unknown-base",
        source=SyncAdapter(name="nowhere"),
        destination=SyncAdapter(name="infrahub"),
        directory=str(tmp_path),
    )

    with pytest.raises(PluginLoadError):
        resolve_installed_model_base(instance.source)
