"""Ambient `CISCO_APIC_VERIFY` precedence against a declared `verify` setting."""

from __future__ import annotations

import pytest

from infrahub_sync import SyncAdapter
from infrahub_sync.adapters.aci import AciAdapter

BASE_SETTINGS: dict[str, object] = {
    "url": "https://apic.example.test",
    "username": "admin",
    "password": "secret",
}


def _create_client(monkeypatch: pytest.MonkeyPatch, *, env_value: str | None, declared_verify: object) -> bool:
    """Build a client through `_create_aci_client` and return the resolved `verify` flag."""
    if env_value is None:
        monkeypatch.delenv("CISCO_APIC_VERIFY", raising=False)
    else:
        monkeypatch.setenv("CISCO_APIC_VERIFY", env_value)
    adapter = object.__new__(AciAdapter)
    settings = dict(BASE_SETTINGS)
    settings["verify"] = declared_verify
    client = adapter._create_aci_client(SyncAdapter(name="aci", settings=settings))
    return client.verify


@pytest.mark.parametrize(
    ("env_value", "declared_verify", "expected"),
    [
        # Unset: the declared setting is authoritative.
        (None, False, False),
        (None, True, True),
        # Empty: falls through to the declared setting rather than enabling verification.
        ("", False, False),
        ("", True, True),
        # A real falsy value in the environment overrides a declared True.
        ("false", True, False),
        ("0", True, False),
        ("no", True, False),
        # A real truthy value in the environment overrides a declared False.
        ("true", False, True),
        # A non-string declared setting (not just str/bool) still normalizes via bool().
        (None, 0, False),
        (None, 1, True),
    ],
)
def test_verify_precedence(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
    declared_verify: object,
    expected: bool,  # noqa: FBT001 - one parametrized dimension of the resolved flag.
) -> None:
    assert _create_client(monkeypatch, env_value=env_value, declared_verify=declared_verify) is expected


def test_empty_env_does_not_override_declared_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: an empty `CISCO_APIC_VERIFY` must not silently enable TLS verification."""
    assert _create_client(monkeypatch, env_value="", declared_verify=False) is False
