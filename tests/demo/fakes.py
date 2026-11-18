"""A fake Sync client and a fake schema loader for the demo preparation tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from infrahub_sync.client import RegisteredConfigurationResource, RegisteredVersionResource
from infrahub_sync.client.models import ConfigurationPage, ConfigurationSummaryResource

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from infrahub_sync.client import ConfigMutationRequest
    from infrahub_sync.demo.prepare import SchemaState

CREATED_AT = "2026-11-18T12:00:00+00:00"


def _checksum(package: object) -> str:
    return hashlib.sha256(repr(package).encode()).hexdigest()


def _reference(config_id: str, version: int, package: object) -> dict[str, object]:
    return {
        "config_id": config_id,
        "registry_version": version,
        "package_checksum": _checksum(package),
        "created_at": CREATED_AT,
    }


@dataclass
class FakeConfig:
    """One registered configuration and its versions' packages, oldest first."""

    config_id: str
    versions: list[dict[str, object]]


@dataclass
class FakeSyncClient:
    """Keeps configurations in memory, newest first, as the Sync API lists them."""

    configs: list[FakeConfig] = field(default_factory=list)
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, config_id: str, package: dict[str, object]) -> None:
        """Make a configuration the newest one."""
        self.configs.insert(0, FakeConfig(config_id, [package]))

    def list_configs(
        self, name: str | None = None, limit: int | None = None, cursor: str | None = None
    ) -> ConfigurationPage:
        """Return the configurations named `name`, newest first, in one page."""
        del limit, cursor
        self.calls.append(("list_configs", name or "", ""))
        items = [
            ConfigurationSummaryResource.model_validate(
                {
                    "config_id": config.config_id,
                    "created_at": CREATED_AT,
                    "name": package_name_of(config.versions[-1]),
                    "latest_version": len(config.versions),
                }
            )
            for config in self.configs
            if name is None or package_name_of(config.versions[-1]) == name
        ]
        return ConfigurationPage(items=tuple(items))

    def register_config(self, request: ConfigMutationRequest, idempotency_key: str) -> RegisteredConfigurationResource:
        """Register a new configuration at version 1."""
        config_id = f"cfg-{len(self.configs) + 1}"
        self.calls.append(("register_config", config_id, idempotency_key))
        self.add(config_id, request.package)
        return RegisteredConfigurationResource.model_validate(
            {
                "configuration": {"config_id": config_id, "created_at": CREATED_AT, "latest_version": 1},
                "version": _reference(config_id, 1, request.package),
            }
        )

    def create_config_version(
        self, config_id: str, request: ConfigMutationRequest, idempotency_key: str
    ) -> RegisteredVersionResource:
        """Add a version unless the newest version holds the same package."""
        self.calls.append(("create_config_version", config_id, idempotency_key))
        config = next(config for config in self.configs if config.config_id == config_id)
        created = config.versions[-1] != request.package
        if created:
            config.versions.append(request.package)
        return RegisteredVersionResource.model_validate(
            {"version": _reference(config_id, len(config.versions), request.package), "created": created}
        )


def package_name_of(package: dict[str, object]) -> str:
    """The `configuration.name` of a parsed package."""
    configuration = package.get("configuration")
    assert isinstance(configuration, dict)
    return str(cast("dict[str, object]", configuration)["name"])


@dataclass
class FakeSchemaLoader:
    """Answers `loaded` the first time and `unchanged` after, or raises the given error."""

    error: Exception | None = None
    calls: list[tuple[str, str, int, float]] = field(default_factory=list)

    def __call__(self, address: str, token: str, files: Sequence[Path], timeout: float) -> SchemaState:
        """Record the call and answer like a schema load."""
        self.calls.append((address, token, len(files), timeout))
        if self.error is not None:
            raise self.error
        return "loaded" if len(self.calls) == 1 else "unchanged"


__all__ = ["FakeSchemaLoader", "FakeSyncClient", "package_name_of"]
