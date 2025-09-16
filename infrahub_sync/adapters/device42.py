from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

from infrahub_sync import (
    SyncAdapter,
    SyncConfig,
)

from .genericrestapi import GenericrestapiAdapter, GenericrestapiModel

if TYPE_CHECKING:
    from collections.abc import Mapping

    from diffsync import Adapter


class Device42Adapter(GenericrestapiAdapter):
    """Device42 adapter that extends the generic REST API adapter."""

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, **kwargs) -> None:
        # Set Device42-specific defaults
        settings = adapter.settings or {}

        # Apply Device42-specific defaults if not specified
        if "auth_method" not in settings:
            settings["auth_method"] = "basic"
        if "api_endpoint" not in settings:
            settings["api_endpoint"] = "/api/2.0"
        if "url_env_vars" not in settings:
            settings["url_env_vars"] = ["DEVICE42_ADDRESS", "DEVICE42_URL"]
        if "username_env_vars" not in settings:
            settings["username_env_vars"] = ["DEVICE42_USERNAME"]
        if "password_env_vars" not in settings:
            settings["password_env_vars"] = ["DEVICE42_PASSWORD"]
        
        # Device42 typically returns results with pagination info
        if "response_key_pattern" not in settings:
            settings["response_key_pattern"] = "{resource}"

        # Create a new adapter with updated settings
        updated_adapter = SyncAdapter(name=adapter.name, settings=settings)

        super().__init__(target=target, adapter=updated_adapter, config=config, adapter_type="Device42", **kwargs)


class Device42Model(GenericrestapiModel):
    """Device42 model that extends the generic REST API model."""

    @classmethod
    def create(
        cls,
        adapter: Adapter,
        ids: Mapping[Any, Any],
        attrs: Mapping[Any, Any],
    ) -> Self | None:
        # TODO: To implement
        return super().create(adapter=adapter, ids=ids, attrs=attrs)

    def update(self, attrs: dict) -> Self | None:
        # TODO: To implement
        return super().update(attrs=attrs)


# Aliases for consistency
Device42Sync = Device42Adapter