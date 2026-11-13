"""Each Sync service entry point trusts the CA bundle before it builds any client, and stops on a refusal."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from infrahub_sync.service import bootstrap, serve, worker
from infrahub_sync.service.trust_store import TrustStoreError

if TYPE_CHECKING:
    from types import ModuleType

# Each entry point, and the first step that would build a client after the bundle is in place.
_ENTRY_POINTS = [(serve, "build_app"), (worker, "ServiceProcessWorker"), (bootstrap, "_converge")]
_IDS = ["serve", "worker", "bootstrap"]


def _run(module: ModuleType) -> int:
    """Run one entry point and return its exit status."""
    try:
        result = module.main()
    except SystemExit as stopped:
        code = stopped.code
        return code if isinstance(code, int) else 1
    return result if isinstance(result, int) else 0


@pytest.mark.parametrize(("module", "first_client_step"), _ENTRY_POINTS, ids=_IDS)
def test_the_ca_bundle_is_installed_before_any_client_is_built(
    module: ModuleType, first_client_step: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class _StopError(Exception):
        """Ends the entry point once the order is known."""

    def install() -> None:
        calls.append("install")

    def build(*_arguments: object, **_settings: object) -> None:
        calls.append("client")
        raise _StopError

    monkeypatch.setattr(module, "install_custom_ca_bundle", install)
    monkeypatch.setattr(module, first_client_step, build)
    monkeypatch.setattr(worker, "_pool_argument", lambda _argv: "default")

    with pytest.raises(_StopError):
        module.main()

    assert calls == ["install", "client"]


@pytest.mark.parametrize(("module", "first_client_step"), _ENTRY_POINTS, ids=_IDS)
def test_a_refused_ca_directory_stops_the_entry_point_with_a_non_zero_status(
    module: ModuleType, first_client_step: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse() -> None:
        message = "ca.pem holds a private key; the CA directory takes public certificates only"
        raise TrustStoreError(message)

    def never(*_arguments: object, **_settings: object) -> None:
        message = "a client was built after the CA directory was refused"
        raise AssertionError(message)

    monkeypatch.setattr(module, "install_custom_ca_bundle", refuse)
    monkeypatch.setattr(module, first_client_step, never)

    assert _run(module) != 0
