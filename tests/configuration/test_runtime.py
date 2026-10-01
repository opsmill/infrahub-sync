"""Worker runtime construction from declared configuration packages."""

from __future__ import annotations

from infrahub_sync.configuration import ConfigurationPackage


def test_runtime_instance_resolves_declared_credentials_without_ambient_lookup(monkeypatch) -> None:
    """A registered package is executable from its declared identity alone."""
    from infrahub_sync.configuration.runtime import resolve_runtime_instance

    registered = "registered-canary"
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_TOKEN", registered)
    monkeypatch.setenv("NETBOX_TOKEN", "ambient-canary")
    package = ConfigurationPackage.model_validate(
        {
            "format_version": 1,
            "configuration": {
                "name": "registered",
                "source": {
                    "name": "netbox",
                    "settings": {"url": "https://netbox.example", "token": {"$credential": "token"}},
                },
                "destination": {
                    "name": "infrahub",
                    "settings": {"url": "https://infrahub.example", "token": {"$credential": "token"}},
                },
                "order": [],
                "schema_mapping": [],
                "diffsync_flags": [],
                "incremental": None,
            },
            "credentials": {"token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_TOKEN"}},
        }
    )

    instance = resolve_runtime_instance(package, directory="/registered")
    assert instance.source.settings is not None
    assert instance.destination.settings is not None
    assert instance.source.settings["token"] == registered
    assert instance.destination.settings["token"] == registered


def test_null_adapter_settings_still_carry_the_registered_context(monkeypatch) -> None:
    """``settings: null`` is admitted, but must not reopen the ambient credential fallback."""
    from infrahub_sync.configuration.credentials import is_registered_context, select_runtime_credential
    from infrahub_sync.configuration.runtime import resolve_runtime_instance

    monkeypatch.setenv("NETBOX_ADDRESS", "https://ambient-netbox.example")
    monkeypatch.setenv("NETBOX_TOKEN", "ambient-canary")
    package = ConfigurationPackage.model_validate(
        {
            "format_version": 1,
            "configuration": {
                "name": "registered",
                "source": {"name": "netbox", "settings": None},
                "destination": {"name": "infrahub", "settings": None},
                "order": [],
                "schema_mapping": [],
                "diffsync_flags": [],
                "incremental": None,
            },
            "credentials": {},
        }
    )

    instance = resolve_runtime_instance(package, directory="/registered")
    for adapter in (instance.source, instance.destination):
        assert adapter.settings is not None
        assert is_registered_context(adapter.settings)
    assert instance.source.settings is not None
    assert select_runtime_credential(instance.source.settings, "url", ("NETBOX_ADDRESS",)) is None
    assert select_runtime_credential(instance.source.settings, "token", ("NETBOX_TOKEN",)) is None
