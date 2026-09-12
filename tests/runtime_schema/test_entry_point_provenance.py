"""Installed-only entry points resolve classes from installed module origins."""

from __future__ import annotations

import sys
import types
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from diffsync import Adapter

from infrahub_sync.plugin_loader import PluginLoader, PluginLoadError
from tests.runtime_schema.installed_distribution import install_distribution

ENTRY_POINT = "provenance_entry_point"
POISON_SOURCE = """
from diffsync import Adapter


class PoisonAdapter(Adapter):
    type = "Poison"
"""


class _EntryPoints:
    def __init__(self, entry_point: EntryPoint) -> None:
        self._entry_point = entry_point

    def select(self, *, group: str, name: str) -> tuple[EntryPoint, ...]:
        if group == self._entry_point.group and name == self._entry_point.name:
            return (self._entry_point,)
        return ()


def _publish(monkeypatch: pytest.MonkeyPatch, dotted: str) -> None:
    entry_point = EntryPoint(name=ENTRY_POINT, value=f"{dotted}:PoisonAdapter", group="infrahub_sync.adapters")
    monkeypatch.setattr("infrahub_sync.plugin_loader.entry_points", lambda: _EntryPoints(entry_point))


def _poison_module(dotted: str, origin: Path) -> types.ModuleType:
    module = types.ModuleType(dotted)

    class PoisonAdapter(Adapter):
        type = "Poison"

    PoisonAdapter.__module__ = dotted
    module.PoisonAdapter = PoisonAdapter  # ty: ignore[unresolved-attribute]
    module.__file__ = str(origin)
    module.__spec__ = None
    return module


def test_an_entry_point_target_shadowed_by_a_checkout_refuses_before_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dotted = install_distribution(
        monkeypatch, site_packages=tmp_path / "site-packages", package="entryshadow", module="adapter"
    )
    shadow = tmp_path / "checkout"
    (shadow / "entryshadow").mkdir(parents=True)
    (shadow / "entryshadow" / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "entryshadow" / "adapter.py").write_text(POISON_SOURCE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(shadow))
    _publish(monkeypatch, dotted)

    with pytest.raises(PluginLoadError, match="installed distribution"):
        PluginLoader.installed_only_loader().resolve(ENTRY_POINT)
    assert dotted not in sys.modules
    assert PluginLoader().resolve(ENTRY_POINT).__name__ == "PoisonAdapter"


def test_an_entry_point_target_answered_by_a_preloaded_checkout_module_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dotted = install_distribution(
        monkeypatch, site_packages=tmp_path / "site-packages", package="entrypreload", module="adapter"
    )
    poison = _poison_module(dotted, tmp_path / "checkout" / "adapter.py")
    monkeypatch.setitem(sys.modules, dotted, poison)
    _publish(monkeypatch, dotted)

    with pytest.raises(PluginLoadError, match="installed distribution"):
        PluginLoader.installed_only_loader().resolve(ENTRY_POINT)
    assert sys.modules[dotted] is poison
    assert PluginLoader().resolve(ENTRY_POINT) is poison.PoisonAdapter
