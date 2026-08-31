"""A PEP 420 namespace distribution is a normal way to ship an adapter.

Its parent packages carry no ``__init__.py``, so the origin walk has to combine namespace
portions across ``sys.path`` the way the import system does — while still refusing a
target no distribution ships and still letting a regular package shadow what follows it.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from infrahub_sync import SyncAdapter
from infrahub_sync.plugin_loader import (
    PluginLoader,
    PluginLoadError,
    effective_module_origin,
    is_installed_distribution_module,
    resolve_installed_adapter_class,
    resolve_installed_model_base,
)
from tests.runtime_schema.installed_distribution import ADAPTER_SOURCE, install_namespace_distribution

DOTTED = "vendor_ns.sync_plugins.adapter"
OTHER_SOURCE = ADAPTER_SOURCE.replace('type = "Wheel"', 'type = "Other"')


def _installed_only(dotted: str) -> type:
    return PluginLoader.installed_only_loader().resolve(dotted)


# --- acceptance --------------------------------------------------------------------------


def test_an_installed_namespace_module_resolves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    installed = tmp_path / "site-packages"
    dotted = install_namespace_distribution(
        monkeypatch, portions={installed: {"adapter": ADAPTER_SOURCE}}, dotted=DOTTED, installed_root=installed
    )

    assert not (installed / "vendor_ns" / "__init__.py").exists()
    assert effective_module_origin(dotted) == installed / "vendor_ns" / "sync_plugins" / "adapter.py"
    assert is_installed_distribution_module(dotted) is True
    assert _installed_only(dotted).__name__ == "WheelAdapter"


def test_an_installed_namespace_module_supplies_both_adapter_and_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    installed = tmp_path / "site-packages"
    dotted = install_namespace_distribution(
        monkeypatch, portions={installed: {"adapter": ADAPTER_SOURCE}}, dotted=DOTTED, installed_root=installed
    )
    declared = SyncAdapter(name="plugin", adapter=dotted)

    assert resolve_installed_adapter_class(declared).__name__ == "WheelAdapter"
    assert resolve_installed_model_base(declared).__name__ == "WheelModel"


def test_namespace_portions_combine_across_the_import_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Two portions of the same namespace, each shipping a different module. The target
    # lives in the second, which is only reachable if portions combine.
    first = tmp_path / "portion-one"
    installed = tmp_path / "portion-two"
    dotted = install_namespace_distribution(
        monkeypatch,
        portions={installed: {"adapter": ADAPTER_SOURCE}, first: {"unrelated": OTHER_SOURCE}},
        dotted=DOTTED,
        installed_root=installed,
    )

    assert effective_module_origin(dotted) == installed / "vendor_ns" / "sync_plugins" / "adapter.py"
    assert is_installed_distribution_module(dotted) is True
    assert _installed_only(dotted).__name__ == "WheelAdapter"
    # The combined namespace really does span both portions.
    assert importlib.import_module("vendor_ns.sync_plugins.unrelated").WheelAdapter.type == "Other"


# --- refusal ----------------------------------------------------------------------------


def test_an_unowned_module_in_an_earlier_portion_cannot_masquerade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Both portions supply `adapter`; the earlier one wins the import and no distribution
    # ships it, so the shipped file behind it must not launder the unowned one.
    installed = tmp_path / "site-packages"
    earlier = tmp_path / "checkout"
    dotted = install_namespace_distribution(
        monkeypatch,
        portions={installed: {"adapter": ADAPTER_SOURCE}, earlier: {"adapter": OTHER_SOURCE}},
        dotted=DOTTED,
        installed_root=installed,
    )

    assert effective_module_origin(dotted) == earlier / "vendor_ns" / "sync_plugins" / "adapter.py"
    assert is_installed_distribution_module(dotted) is False
    with pytest.raises(PluginLoadError, match="installed distribution"):
        _installed_only(dotted)
    assert dotted not in sys.modules


def test_a_regular_package_shadow_blocks_a_later_namespace_portion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A checkout package that claims the top-level name is a regular package, so the scan
    # stops there; the installed namespace portion behind it is unreachable.
    installed = tmp_path / "site-packages"
    shadow = tmp_path / "checkout"
    dotted = install_namespace_distribution(
        monkeypatch, portions={installed: {"adapter": ADAPTER_SOURCE}}, dotted=DOTTED, installed_root=installed
    )
    (shadow / "vendor_ns").mkdir(parents=True)
    (shadow / "vendor_ns" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(shadow))

    assert effective_module_origin(dotted) is None
    assert is_installed_distribution_module(dotted) is False
    with pytest.raises(PluginLoadError, match="installed distribution"):
        _installed_only(dotted)


def test_a_namespace_package_itself_has_no_admitted_origin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # `vendor_ns.sync_plugins` is a namespace package: it is a directory, not a file, so
    # there is nothing a distribution could have shipped for it.
    installed = tmp_path / "site-packages"
    install_namespace_distribution(
        monkeypatch, portions={installed: {"adapter": ADAPTER_SOURCE}}, dotted=DOTTED, installed_root=installed
    )

    assert effective_module_origin("vendor_ns.sync_plugins") is None
    assert is_installed_distribution_module("vendor_ns.sync_plugins") is False


def test_a_module_cannot_carry_a_submodule(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    installed = tmp_path / "site-packages"
    install_namespace_distribution(
        monkeypatch, portions={installed: {"adapter": ADAPTER_SOURCE}}, dotted=DOTTED, installed_root=installed
    )

    assert effective_module_origin(f"{DOTTED}.deeper") is None


def test_a_preloaded_poison_namespace_module_still_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The post-load guard covers namespace targets too.
    import types

    from diffsync import Adapter

    installed = tmp_path / "site-packages"
    dotted = install_namespace_distribution(
        monkeypatch, portions={installed: {"adapter": ADAPTER_SOURCE}}, dotted=DOTTED, installed_root=installed
    )
    poison = types.ModuleType(dotted)

    class PoisonAdapter(Adapter):
        type = "Poison"

    PoisonAdapter.__module__ = dotted
    poison.PoisonAdapter = PoisonAdapter  # ty: ignore[unresolved-attribute]
    poison.__file__ = str(tmp_path / "elsewhere" / "adapter.py")
    poison.__spec__ = None
    monkeypatch.setitem(sys.modules, dotted, poison)

    with pytest.raises(PluginLoadError):
        _installed_only(dotted)


def test_the_legacy_loader_still_resolves_an_unowned_namespace_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    earlier = tmp_path / "checkout"
    installed = tmp_path / "site-packages"
    dotted = install_namespace_distribution(
        monkeypatch,
        portions={installed: {"adapter": ADAPTER_SOURCE}, earlier: {"adapter": OTHER_SOURCE}},
        dotted=DOTTED,
        installed_root=installed,
    )

    assert PluginLoader().resolve(dotted).type == "Other"
