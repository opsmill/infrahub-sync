"""R1: registered dotted resolution admits only installed-distribution modules.

`sys.path` contains the working directory, so a dotted import alone reaches modules that
merely happen to be importable from a checkout. Registered admission answers a provenance
question instead — does an installed distribution own this top-level package — and it
answers it before importing anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from infrahub_sync import SyncAdapter
from infrahub_sync.plugin_loader import (
    PluginLoader,
    PluginLoadError,
    resolve_installed_adapter_class,
    resolve_installed_model_base,
)

CHECKOUT_MODULE = "tests.runtime_schema.installed_source_adapter"


def _seam(monkeypatch: pytest.MonkeyPatch, distributions: dict[str, list[str]]) -> None:
    """Report exactly these top-level packages as owned by an installed distribution."""
    monkeypatch.setattr("infrahub_sync.plugin_loader.packages_distributions", lambda: distributions)


def test_an_uninstalled_checkout_module_refuses_even_though_it_imports() -> None:
    # The module imports fine from this checkout; no installed distribution owns `tests`.
    assert __import__(CHECKOUT_MODULE)
    with pytest.raises(PluginLoadError, match="installed distribution"):
        PluginLoader.installed_only_loader().resolve(CHECKOUT_MODULE)


def test_a_refused_dotted_target_is_never_imported(monkeypatch: pytest.MonkeyPatch) -> None:
    # Provenance is answered before the import, so an uninstalled module cannot run its
    # top level as a side effect of being named.
    _seam(monkeypatch, {})
    for name in list(sys.modules):
        if name == CHECKOUT_MODULE:
            monkeypatch.delitem(sys.modules, name)

    with pytest.raises(PluginLoadError):
        PluginLoader.installed_only_loader().resolve(CHECKOUT_MODULE)

    assert CHECKOUT_MODULE not in sys.modules


def test_the_same_module_resolves_once_a_distribution_owns_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # The controlled metadata seam is the only difference from the refusal above.
    _seam(monkeypatch, {"tests": ["a-plugin-distribution"]})

    resolved = PluginLoader.installed_only_loader().resolve(CHECKOUT_MODULE)

    assert resolved.__name__ == "InstalledSourceAdapter"


def test_a_genuinely_installed_distribution_is_admitted_without_a_seam() -> None:
    # No seam: `diffsync` really is installed, and its metadata says so.
    resolved = PluginLoader.installed_only_loader().resolve("diffsync.store:BaseStore")

    assert resolved.__name__ == "BaseStore"


def test_the_bundled_package_stays_admitted_when_metadata_reports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An editable or source checkout of this project reports no distribution for its own
    # package, and its bundled adapters must still resolve.
    _seam(monkeypatch, {})

    resolved = resolve_installed_adapter_class(
        SyncAdapter(name="infrahub", adapter="infrahub_sync.adapters.infrahub:InfrahubAdapter")
    )

    assert resolved.__module__ == "infrahub_sync.adapters.infrahub"


def test_a_bundled_adapter_name_stays_admitted(monkeypatch: pytest.MonkeyPatch) -> None:
    _seam(monkeypatch, {})

    assert resolve_installed_adapter_class(SyncAdapter(name="infrahub")).__name__ == "InfrahubAdapter"
    assert resolve_installed_model_base(SyncAdapter(name="infrahub")).__name__ == "InfrahubModel"


def test_a_model_base_from_an_uninstalled_dotted_module_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _seam(monkeypatch, {})

    with pytest.raises(PluginLoadError):
        resolve_installed_model_base(SyncAdapter(name="plugin", adapter=f"{CHECKOUT_MODULE}:InstalledSourceAdapter"))


def test_the_legacy_loader_still_resolves_a_checkout_module() -> None:
    # Provenance is a registered-admission rule; the local CLI path is unchanged.
    resolved = PluginLoader().resolve(CHECKOUT_MODULE)

    assert resolved.__name__ == "InstalledSourceAdapter"


@pytest.mark.parametrize(
    ("spec", "admitted"),
    [
        pytest.param("diffsync.store", True, id="installed-distribution"),
        pytest.param("infrahub_sync.adapters.infrahub", True, id="bundled-package"),
        pytest.param("tests.runtime_schema.installed_source_adapter", False, id="checkout-module"),
        pytest.param("os.path", False, id="standard-library"),
    ],
)
def test_the_provenance_rule_answers_by_top_level_package(spec: str, *, admitted: bool) -> None:
    # Stated as a property over the top-level package, not a list of path examples.
    from infrahub_sync.plugin_loader import is_installed_distribution_module

    assert is_installed_distribution_module(spec) is admitted


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
    # A syntactically admitted dotted spec that no installed distribution owns.
    content["configuration"]["source"]["adapter"] = CHECKOUT_MODULE
    package = parse_configuration_package(content)
    instance = resolve_runtime_instance(package, directory=str(tmp_path))

    with pytest.raises(PluginLoadError, match="installed distribution"):
        build_runtime_model_plan(package=package, instance=instance, run_branch=None, scope="both")
