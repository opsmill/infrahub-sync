"""R1 follow-up: a preloaded sys.modules entry cannot bypass origin validation.

Predicting an origin from ``sys.path`` says nothing about a module that is already
imported: ``import_module`` returns the ``sys.modules`` entry without consulting a finder
at all, and a parent package's ``__path__`` can redirect a submodule the same way. So the
module object registered resolution is about to read classes from is validated too.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest
from diffsync import Adapter

from infrahub_sync.plugin_loader import PluginLoader, PluginLoadError
from tests.runtime_schema.installed_distribution import install_distribution

BUNDLED_MODULE = "infrahub_sync.adapters.prometheus"
INSTALLED_MODULE = "diffsync.store"


def _poison(name: str, origin: Path | None) -> types.ModuleType:
    """A module carrying a usable Adapter that no distribution ships."""
    module = types.ModuleType(name)

    class PoisonAdapter(Adapter):
        type = "Poison"

    PoisonAdapter.__module__ = name
    module.PoisonAdapter = PoisonAdapter  # ty: ignore[unresolved-attribute]
    module.__file__ = None if origin is None else str(origin)
    module.__spec__ = None
    return module


def _install_poison(monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path) -> types.ModuleType:
    poison = _poison(name, tmp_path / "checkout" / f"{name.rpartition('.')[2]}.py")
    monkeypatch.setitem(sys.modules, name, poison)
    return poison


def test_a_preloaded_module_no_distribution_ships_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    poison = _install_poison(monkeypatch, INSTALLED_MODULE, tmp_path)

    with pytest.raises(PluginLoadError, match="installed distribution"):
        PluginLoader.installed_only_loader().resolve(INSTALLED_MODULE)
    assert sys.modules[INSTALLED_MODULE] is poison


def test_a_preloaded_module_without_an_origin_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    # A module built in memory has no file at all; nothing can be admitted from it.
    monkeypatch.setitem(sys.modules, INSTALLED_MODULE, _poison(INSTALLED_MODULE, None))

    with pytest.raises(PluginLoadError):
        PluginLoader.installed_only_loader().resolve(INSTALLED_MODULE)


def test_a_preloaded_genuine_installed_module_still_resolves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The check validates the origin, not the fact of being preloaded: importing the
    # installed module first must not change the verdict.
    dotted = install_distribution(
        monkeypatch, site_packages=tmp_path / "site-packages", package="preloadedpkg", module="adapter"
    )
    preloaded = importlib.import_module(dotted)
    assert sys.modules[dotted] is preloaded

    resolved = PluginLoader.installed_only_loader().resolve(dotted)

    assert resolved is preloaded.WheelAdapter


def test_a_preloaded_installed_module_resolves_without_a_base_requirement() -> None:
    # A genuinely installed module already in sys.modules still answers.
    import diffsync.store

    assert sys.modules[INSTALLED_MODULE] is diffsync.store
    resolved = PluginLoader.installed_only_loader().resolve(
        f"{INSTALLED_MODULE}:BaseStore", default_class_candidates=("BaseStore",)
    )

    assert resolved is diffsync.store.BaseStore


def test_a_poisoned_bundled_submodule_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The bundled package is admitted by name before the import; after it, the module has
    # to actually come from the installed bundled package.
    poison = _install_poison(monkeypatch, BUNDLED_MODULE, tmp_path)

    with pytest.raises(PluginLoadError):
        PluginLoader.installed_only_loader().resolve(BUNDLED_MODULE)
    assert sys.modules[BUNDLED_MODULE] is poison


def test_a_genuine_bundled_submodule_still_resolves() -> None:
    resolved = PluginLoader.installed_only_loader().resolve("infrahub_sync.adapters.infrahub:InfrahubAdapter")

    assert resolved.__name__ == "InfrahubAdapter"


def test_a_manipulated_parent_package_path_cannot_redirect_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The distribution ships `pathpkg/adapter.py`, but the already-imported parent's
    # __path__ points somewhere else, so the import loads a different file entirely.
    dotted = install_distribution(
        monkeypatch, site_packages=tmp_path / "site-packages", package="pathpkg", module="adapter"
    )
    redirected = tmp_path / "redirect"
    redirected.mkdir()
    (redirected / "adapter.py").write_text(
        "from diffsync import Adapter\n\n\nclass WheelAdapter(Adapter):\n    type = 'Redirected'\n",
        encoding="utf-8",
    )
    parent = types.ModuleType("pathpkg")
    parent.__path__ = [str(redirected)]
    parent.__file__ = str(redirected / "__init__.py")
    monkeypatch.setitem(sys.modules, "pathpkg", parent)

    with pytest.raises(PluginLoadError, match="installed distribution"):
        PluginLoader.installed_only_loader().resolve(dotted)
    assert sys.modules[dotted].__file__ == str(redirected / "adapter.py")


def test_the_legacy_loader_is_unchanged_by_the_origin_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Origin validation is a registered-admission rule; the local CLI path still resolves
    # whatever the import system gives it.
    poison = _install_poison(monkeypatch, INSTALLED_MODULE, tmp_path)

    assert PluginLoader().resolve(f"{INSTALLED_MODULE}:PoisonAdapter") is poison.PoisonAdapter
