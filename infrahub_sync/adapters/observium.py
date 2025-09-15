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

from .generic_rest_api import GenericRestApiAdapter, GenericRestApiModel

if TYPE_CHECKING:
    from collections.abc import Mapping

    from diffsync import Adapter


class ObserviumAdapter(GenericRestApiAdapter):
    """Observium adapter that extends the generic REST API adapter."""

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, **kwargs) -> None:
        # Set Observium-specific defaults
        settings = adapter.settings or {}

        # Apply Observium-specific defaults if not specified
        if "auth_method" not in settings:
            settings["auth_method"] = "basic"
        if "api_endpoint" not in settings:
            settings["api_endpoint"] = "/api/v0"
        if "url_env_vars" not in settings:
            settings["url_env_vars"] = ["OBSERVIUM_ADDRESS", "OBSERVIUM_URL"]
        if "token_env_vars" not in settings:
            settings["token_env_vars"] = ["OBSERVIUM_TOKEN"]
        if "username_env_vars" not in settings:
            settings["username_env_vars"] = ["OBSERVIUM_USERNAME"]
        if "password_env_vars" not in settings:
            settings["password_env_vars"] = ["OBSERVIUM_PASSWORD"]

        settings.setdefault("response_key_pattern", "{resource}")

        # Create a new adapter with updated settings
        updated_adapter = SyncAdapter(name=adapter.name, settings=settings)

        super().__init__(target=target, adapter=updated_adapter, config=config, adapter_type="Observium", **kwargs)


class ObserviumModel(GenericRestApiModel):
    """Observium model that extends the generic REST API model."""

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
