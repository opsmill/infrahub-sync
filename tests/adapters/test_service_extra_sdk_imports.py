"""The `service` extra installs Prometheus; vendor SDKs use separate optional extras.

`prometheus-client` is declared in `service` for Python 3.11 and newer.
The Slurp'it adapter is tested against an async SDK stub in
`test_slurpit_model_loading.py`, so the default service profile needs no real SDK.

`ipfabric` is declared in its own `ipfabric` extra instead, not part of `service` and so not
part of the default Sync image: every current release depends unconditionally on `niquests`,
whose `urllib3-future` dependency ships a `.pth` hook that replaces the real `urllib3` package
in `site-packages` at interpreter start, for every process, not just the IP Fabric adapter.
That is checked directly below without regard to whether `ipfabric` is installed. The
`ipfabric`-specific tests skip when the `ipfabric` extra hasn't been installed alongside
`service`, since a service-profile-only install (the default image) never has it.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
import sys
from collections.abc import Callable, Iterator
from typing import Any
from unittest import mock

import pytest

import infrahub_sync.adapters as adapters_package
from infrahub_sync import SyncAdapter

requires_service_profile = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="the `service` extra is only declared for python_version >= 3.11"
)
requires_ipfabric_extra = pytest.mark.skipif(
    importlib.util.find_spec("ipfabric") is None,
    reason="the `ipfabric` extra is optional and not installed alongside `service`",
)


@pytest.fixture
def restore_adapter_module() -> Iterator[Callable[[str, str, str], None]]:
    """Undo a successful import's `sys.modules`/package-attribute side effects.

    Importing a real SDK (`ipfabric`, `prometheus_client`) pulls in dozens of its
    own submodules (`ipfabric.api`, `ipfabric.models`, ...), none of which existed in
    `sys.modules` before the test ran. Restoring only the `infrahub_sync.adapters.<name>`
    entry leaves all of those behind, where they leak into later tests. `register` snapshots
    the module keys before import and removes only SDK and adapter modules added afterward.
    Modules that were already loaded are preserved.

    This deliberately does not touch transitive third-party dependencies pulled in alongside
    the SDK (`niquests`, `pandas`, `numpy`, ...): those aren't the adapter's own SDK, other
    tests may depend on them already being importable, and some (`numpy`'s C extension) cannot
    be safely removed from `sys.modules` and reimported within the same process at all.

    `tests/adapters/test_reference_conversion_optional_sdk.py` reimports these same modules
    against stub SDKs and depends on that not having happened yet.
    """
    restores: list[tuple[str, str, str, frozenset[str], bool, Any]] = []

    def register(module_name: str, full_name: str, sdk_name: str) -> None:
        had_attr = hasattr(adapters_package, module_name)
        prior_attr = getattr(adapters_package, module_name, None)
        restores.append((module_name, full_name, sdk_name, frozenset(sys.modules), had_attr, prior_attr))

    yield register

    cleanup = pytest.MonkeyPatch()
    for module_name, full_name, sdk_name, prior_modules, had_attr, prior_attr in reversed(restores):
        for added_name in set(sys.modules) - prior_modules:
            if added_name in {full_name, sdk_name} or added_name.startswith(f"{sdk_name}."):
                cleanup.delitem(sys.modules, added_name, raising=False)
        if had_attr:
            cleanup.setattr(adapters_package, module_name, prior_attr)
        else:
            cleanup.delattr(adapters_package, module_name, raising=False)


@requires_service_profile
@pytest.mark.parametrize(
    ("module_name", "sdk_name"),
    [
        ("prometheus", "prometheus_client"),
    ],
)
def test_adapter_imports_without_error_in_service_profile(
    module_name: str, sdk_name: str, restore_adapter_module
) -> None:
    full_name = f"infrahub_sync.adapters.{module_name}"
    restore_adapter_module(module_name, full_name, sdk_name)
    importlib.import_module(full_name)
    assert sdk_name in sys.modules


@requires_service_profile
def test_service_profile_does_not_install_slurpit_sdk() -> None:
    with pytest.raises(importlib.metadata.PackageNotFoundError):
        importlib.metadata.version("slurpit-sdk")


@requires_service_profile
def test_service_profile_urllib3_is_not_replaced_by_urllib3_future() -> None:
    """`urllib3-future`'s `.pth` hook silently swaps the real `urllib3` for itself at process start.

    `niquests` (an `ipfabric` dependency) requires `urllib3-future`, which is exactly why
    `ipfabric` is declared in its own `ipfabric` extra instead of `service`: every
    requests-based adapter in the default Sync image would otherwise run on the replacement
    without any of them importing `ipfabric` themselves. This must hold in the `service`
    profile regardless of whether `ipfabric` happens to be installed alongside it.
    """
    import urllib3

    installed_version = importlib.metadata.version("urllib3")
    assert getattr(urllib3, "__version__", None) == installed_version
    with pytest.raises(importlib.metadata.PackageNotFoundError):
        importlib.metadata.version("urllib3-future")


@requires_service_profile
@requires_ipfabric_extra
def test_ipfabric_adapter_imports_without_error_when_extra_installed(restore_adapter_module) -> None:
    full_name = "infrahub_sync.adapters.ipfabricsync"
    restore_adapter_module("ipfabricsync", full_name, "ipfabric")
    importlib.import_module(full_name)
    assert "ipfabric" in sys.modules


@requires_service_profile
@requires_ipfabric_extra
def test_ipfabric_adapter_constructs_client_without_live_call(
    restore_adapter_module, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    full_name = "infrahub_sync.adapters.ipfabricsync"
    restore_adapter_module("ipfabricsync", full_name, "ipfabric")
    module = importlib.import_module(full_name)

    # `ipfabric` is only installed when the optional `ipfabric` extra is (see
    # `requires_ipfabric_extra`), so ty can't resolve these outside that profile.
    from ipfabric.api import IPFabricAPI  # ty: ignore[unresolved-import]
    from ipfabric.auth import Setup  # ty: ignore[unresolved-import]
    from ipfabric.models.snapshots import Snapshots  # ty: ignore[unresolved-import]

    # `IPFClient` falls back to `find_dotenv(usecwd=True)` when no `env_file` is given, which
    # walks up from the working directory and can pick up an unrelated real `.env` (e.g. the
    # main checkout's, from a worktree). Point it at a file under `tmp_path` that doesn't
    # exist, and strip any `IPF_*` variables from the real environment, so this test never
    # reads or depends on real credentials.
    for name in list(os.environ):
        if name.startswith("IPF_"):
            monkeypatch.delenv(name, raising=False)

    adapter = SyncAdapter(
        name="ipfabricsync",
        settings={
            "base_url": "https://ipfabric.example",
            "auth": "test-token",
            "verify_ssl": False,
            "env_file": str(tmp_path / "unused.env"),
        },
    )
    instance = module.IpfabricsyncAdapter.__new__(module.IpfabricsyncAdapter)
    with (
        mock.patch.object(Setup, "check_version", return_value=("v8.0", "8.0.1", "v8.0")),
        mock.patch.object(IPFabricAPI, "get_user", return_value=mock.MagicMock()),
        mock.patch.object(IPFabricAPI, "hostname", new_callable=mock.PropertyMock, return_value="mock-host"),
        mock.patch.object(Snapshots, "get_snapshots", return_value={}),
    ):
        client = instance._create_ipfabric_client(adapter)
    assert client.verify is False


@requires_service_profile
@requires_ipfabric_extra
def test_ipfabric_adapter_passes_verify_not_verify_ssl(restore_adapter_module) -> None:
    full_name = "infrahub_sync.adapters.ipfabricsync"
    restore_adapter_module("ipfabricsync", full_name, "ipfabric")
    module = importlib.import_module(full_name)

    adapter = SyncAdapter(
        name="ipfabricsync",
        settings={"base_url": "https://ipfabric.example", "auth": "test-token", "verify_ssl": False},
    )
    instance = module.IpfabricsyncAdapter.__new__(module.IpfabricsyncAdapter)
    with mock.patch.object(module, "IPFClient") as ipf_client:
        instance._create_ipfabric_client(adapter)
    _, kwargs = ipf_client.call_args
    assert kwargs["verify"] is False
    assert "verify_ssl" not in kwargs
