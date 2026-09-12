"""R1: registered dotted resolution binds a module's origin to an installed distribution.

`sys.path` contains the working directory, so a dotted import alone reaches whatever a
checkout makes importable — including a module that shadows a name an installed
distribution owns. Registered admission answers a provenance question instead: is the
file the import system would load one an installed distribution actually ships? It is
answered from distribution metadata and the filesystem, without importing the candidate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from infrahub_sync import SyncAdapter
from infrahub_sync.plugin_loader import (
    PluginLoader,
    PluginLoadError,
    installed_module_origins,
    is_installed_distribution_module,
    resolve_installed_adapter_class,
    resolve_installed_model_base,
)
from tests.runtime_schema.installed_distribution import install_distribution

CHECKOUT_MODULE = "tests.runtime_schema.installed_source_adapter"


def _no_distributions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("infrahub_sync.plugin_loader.distributions", list)


# --- the property, over real installed metadata ------------------------------------------


@pytest.mark.parametrize(
    ("spec", "admitted"),
    [
        pytest.param("diffsync.store", True, id="installed-distribution-package"),
        pytest.param("diffsync.enum", True, id="installed-distribution-module"),
        pytest.param("infrahub_sync.adapters.infrahub", True, id="bundled-package"),
        pytest.param(CHECKOUT_MODULE, False, id="checkout-module"),
        pytest.param("os.path", False, id="standard-library"),
        pytest.param("nowhere.at.all", False, id="absent"),
    ],
)
def test_the_provenance_rule_answers_from_real_installed_metadata(spec: str, *, admitted: bool) -> None:
    assert is_installed_distribution_module(spec) is admitted


def test_an_installed_distribution_reports_the_exact_file_it_ships() -> None:
    origins = installed_module_origins("diffsync.enum")

    assert origins
    assert all(origin.name == "enum.py" and origin.parent.name == "diffsync" for origin in origins)


# --- acceptance: a module laid out and claimed the way an install lays it out -------------


def test_an_installed_module_on_the_import_path_is_admitted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dotted = install_distribution(
        monkeypatch, site_packages=tmp_path / "site-packages", package="wheelpkg", module="adapter"
    )

    assert is_installed_distribution_module(dotted) is True
    assert PluginLoader.installed_only_loader().resolve(dotted).__name__ == "WheelAdapter"


def test_the_bundled_package_stays_admitted_when_metadata_reports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An editable or source checkout of this project ships no module files in its own
    # metadata, and its bundled adapters must still resolve.
    _no_distributions(monkeypatch)

    assert (
        resolve_installed_adapter_class(
            SyncAdapter(name="infrahub", adapter="infrahub_sync.adapters.infrahub:InfrahubAdapter")
        ).__module__
        == "infrahub_sync.adapters.infrahub"
    )
    assert resolve_installed_adapter_class(SyncAdapter(name="infrahub")).__name__ == "InfrahubAdapter"
    assert resolve_installed_model_base(SyncAdapter(name="infrahub")).__name__ == "InfrahubModel"


# --- refusal: shadowing, and modules no distribution ships -------------------------------


def test_a_local_module_shadowing_an_installed_distribution_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The distribution really ships `shadowpkg/adapter.py`, but a checkout earlier on
    # sys.path provides its own. Ownership of the name is not ownership of the module.
    installed = tmp_path / "site-packages"
    shadow = tmp_path / "checkout"
    dotted = install_distribution(
        monkeypatch, site_packages=installed, package="shadowpkg", module="adapter", on_import_path=False
    )
    (shadow / "shadowpkg").mkdir(parents=True)
    (shadow / "shadowpkg" / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "shadowpkg" / "adapter.py").write_text(
        "raise AssertionError('a shadowing module was imported')\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(installed))
    monkeypatch.syspath_prepend(str(shadow))

    assert is_installed_distribution_module(dotted) is False
    with pytest.raises(PluginLoadError, match="installed distribution"):
        PluginLoader.installed_only_loader().resolve(dotted)
    assert dotted not in sys.modules


def test_a_shadowing_package_refuses_even_without_the_shadowed_submodule(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A checkout package that merely occupies the name still shadows the installed copy,
    # so admission must not fall through to the installed file behind it.
    installed = tmp_path / "site-packages"
    shadow = tmp_path / "checkout"
    dotted = install_distribution(
        monkeypatch, site_packages=installed, package="partialpkg", module="adapter", on_import_path=False
    )
    (shadow / "partialpkg").mkdir(parents=True)
    (shadow / "partialpkg" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(installed))
    monkeypatch.syspath_prepend(str(shadow))

    assert is_installed_distribution_module(dotted) is False


def test_an_uninstalled_checkout_module_refuses_even_though_it_imports() -> None:
    assert __import__(CHECKOUT_MODULE)
    with pytest.raises(PluginLoadError, match="installed distribution"):
        PluginLoader.installed_only_loader().resolve(CHECKOUT_MODULE)


def test_a_refused_dotted_target_is_never_imported(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distributions(monkeypatch)
    for name in list(sys.modules):
        if name == CHECKOUT_MODULE:
            monkeypatch.delitem(sys.modules, name)

    with pytest.raises(PluginLoadError):
        PluginLoader.installed_only_loader().resolve(CHECKOUT_MODULE)

    assert CHECKOUT_MODULE not in sys.modules


def test_owning_the_top_level_name_without_shipping_the_module_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A distribution that owns the top-level name but ships some other module is not
    # proof of this module's provenance: ownership of a name is not ownership of a file.
    install_distribution(
        monkeypatch,
        site_packages=tmp_path / "site-packages",
        package="tests",
        module="something_else",
        on_import_path=False,
    )

    assert is_installed_distribution_module(CHECKOUT_MODULE) is False


def test_a_model_base_from_an_uninstalled_dotted_module_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distributions(monkeypatch)

    with pytest.raises(PluginLoadError):
        resolve_installed_model_base(SyncAdapter(name="plugin", adapter=f"{CHECKOUT_MODULE}:InstalledSourceAdapter"))


def test_the_legacy_loader_still_resolves_a_checkout_module() -> None:
    # Provenance is a registered-admission rule; the local CLI path is unchanged.
    assert PluginLoader().resolve(CHECKOUT_MODULE).__name__ == "InstalledSourceAdapter"


def test_a_registered_run_refuses_a_checkout_dotted_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The execution-level case: admission, not just the resolver, closes the hole."""
    from infrahub_sync.configuration import parse_configuration_package
    from infrahub_sync.configuration.runtime import resolve_runtime_instance
    from infrahub_sync.runtime_schema import build_runtime_model_plan
    from infrahub_sync.runtime_schema import worker as worker_module
    from tests.configuration.validation_packages import package_data

    snapshot = {
        "BuiltinTag": {
            "human_friendly_id": ["name__value"],
            "uniqueness_constraints": [["name__value"]],
            "attributes": {"name": {"kind": "Text", "optional": False, "default_value": None, "unique": True}},
            "relationships": {},
        }
    }
    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", lambda _package, _branch: snapshot)
    monkeypatch.setenv("NETBOX_TOKEN", "provenance-canary")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "provenance-canary")
    content = package_data()
    content["configuration"]["schema_mapping"] = [
        {"name": "BuiltinTag", "mapping": "extras.tags", "fields": [{"name": "name", "mapping": "name"}]}
    ]
    content["configuration"]["source"]["adapter"] = CHECKOUT_MODULE
    package = parse_configuration_package(content)
    instance = resolve_runtime_instance(package, directory=str(tmp_path))

    with pytest.raises(PluginLoadError, match="installed distribution"):
        build_runtime_model_plan(package=package, instance=instance, run_branch=None, scope="both")
