"""Worker runtime construction from declared configuration packages."""

from __future__ import annotations

from infrahub_sync.configuration import ConfigurationPackage


def test_runtime_instance_resolves_declared_credentials_without_ambient_lookup(monkeypatch) -> None:
    """A registered package is executable from its declared identity alone."""
    from infrahub_sync.configuration.runtime import resolve_runtime_instance

    registered = "registered-canary"
    monkeypatch.setenv("TOKEN", registered)
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
            "credentials": {"token": {"provider": "env", "identifier": "TOKEN"}},
        }
    )

    instance = resolve_runtime_instance(package, directory="/registered")
    assert instance.source.settings["token"] == registered
    assert instance.destination.settings["token"] == registered
