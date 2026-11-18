"""A fake Sync client and a fake schema loader for the demo preparation tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from infrahub_sync.client import APIError, RegisteredConfigurationResource, RegisteredVersionResource
from infrahub_sync.client.models import ConfigurationPage, ConfigurationSummaryResource

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from pydantic import BaseModel

    from infrahub_sync.client import ConfigMutationRequest
    from infrahub_sync.demo.prepare import SchemaState

CREATED_AT = "2026-11-18T12:00:00+00:00"


def _checksum(package: object) -> str:
    return hashlib.sha256(json.dumps(package, sort_keys=True).encode()).hexdigest()


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
    """Keeps configurations in memory, newest first, as the Sync API lists them.

    Like the Sync API, it stores the answer to each idempotency key: the same key with the
    same request replays the stored answer, and the same key with another request (another
    operation, target, package or reason) raises the 409 `idempotency-conflict` error. A new
    version whose package matches any stored version returns that version, as the store does.
    """

    configs: list[FakeConfig] = field(default_factory=list)
    calls: list[tuple[str, str, str]] = field(default_factory=list)
    # Every registration or version request, in order, to check what reached the Sync API.
    requests: list[ConfigMutationRequest] = field(default_factory=list)
    # Items per `list_configs` page; None returns every configuration in one page.
    page_size: int | None = None
    # Raised by every call when set, as a Sync client error would be.
    error: Exception | None = None
    _answers: dict[str, tuple[str, BaseModel]] = field(default_factory=dict)

    def add(self, config_id: str, package: dict[str, object]) -> None:
        """Make a configuration the newest one."""
        self.configs.insert(0, FakeConfig(config_id, [package]))

    def list_configs(
        self, name: str | None = None, limit: int | None = None, cursor: str | None = None
    ) -> ConfigurationPage:
        """Return one page of the configurations named `name`, newest first."""
        del limit
        self.calls.append(("list_configs", name or "", cursor or ""))
        if self.error is not None:
            raise self.error
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
        start = int(cursor or 0)
        end = len(items) if self.page_size is None else start + self.page_size
        next_cursor = str(end) if end < len(items) else None
        return ConfigurationPage(items=tuple(items[start:end]), next_cursor=next_cursor)

    def iter_configs(self, name: str | None = None, limit: int | None = None) -> Iterator[ConfigurationSummaryResource]:
        """Yield every configuration named `name`, newest first, page by page."""
        cursor: str | None = None
        while True:
            page = self.list_configs(name=name, limit=limit, cursor=cursor)
            yield from page.items
            cursor = page.next_cursor
            if cursor is None:
                return

    def _replay(self, idempotency_key: str, fingerprint: str) -> BaseModel | None:
        stored = self._answers.get(idempotency_key)
        if stored is None:
            return None
        if stored[0] != fingerprint:
            raise APIError(409, "idempotency-conflict")
        return stored[1]

    def register_config(self, request: ConfigMutationRequest, idempotency_key: str) -> RegisteredConfigurationResource:
        """Register a new configuration at version 1."""
        if self.error is not None:
            raise self.error
        self.requests.append(request)
        fingerprint = _checksum(["register", "", request.package, request.reason])
        replayed = self._replay(idempotency_key, fingerprint)
        if replayed is not None:
            registered = cast("RegisteredConfigurationResource", replayed)
            self.calls.append(("register_config", registered.configuration.config_id, idempotency_key))
            return registered
        config_id = f"cfg-{len(self.configs) + 1}"
        self.calls.append(("register_config", config_id, idempotency_key))
        self.add(config_id, request.package)
        answer = RegisteredConfigurationResource.model_validate(
            {
                "configuration": {"config_id": config_id, "created_at": CREATED_AT, "latest_version": 1},
                "version": _reference(config_id, 1, request.package),
            }
        )
        self._answers[idempotency_key] = (fingerprint, answer)
        return answer

    def create_config_version(
        self, config_id: str, request: ConfigMutationRequest, idempotency_key: str
    ) -> RegisteredVersionResource:
        """Add a version, or return the stored version that holds the same package."""
        self.calls.append(("create_config_version", config_id, idempotency_key))
        if self.error is not None:
            raise self.error
        self.requests.append(request)
        fingerprint = _checksum(["version", config_id, request.package, request.reason])
        replayed = self._replay(idempotency_key, fingerprint)
        if replayed is not None:
            return cast("RegisteredVersionResource", replayed)
        config = next(config for config in self.configs if config.config_id == config_id)
        checksum = _checksum(request.package)
        existing = next(
            (number for number, package in enumerate(config.versions, 1) if _checksum(package) == checksum), None
        )
        if existing is None:
            config.versions.append(request.package)
        version = existing if existing is not None else len(config.versions)
        answer = RegisteredVersionResource.model_validate(
            {"version": _reference(config_id, version, request.package), "created": existing is None}
        )
        self._answers[idempotency_key] = (fingerprint, answer)
        return answer


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
