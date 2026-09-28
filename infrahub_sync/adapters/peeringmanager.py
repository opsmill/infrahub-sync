from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import Self

from infrahub_sync.adapters.genericrestapi import GenericrestapiAdapter, GenericrestapiModel

if TYPE_CHECKING:
    from diffsync import Adapter

    from infrahub_sync import (
        SyncAdapter,
        SyncConfig,
    )


class PeeringmanagerAdapter(GenericrestapiAdapter):
    """PeeringManager adapter that extends the generic REST API adapter."""

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, **kwargs) -> None:
        # Set PeeringManager-specific defaults
        settings = dict(adapter.settings or {})

        # Apply PeeringManager-specific defaults if not specified
        if "auth_method" not in settings:
            settings["auth_method"] = "token"
        if "api_endpoint" not in settings:
            settings["api_endpoint"] = "/api"
        if "url_env_vars" not in settings:
            settings["url_env_vars"] = ["PEERING_MANAGER_ADDRESS", "PEERING_MANAGER_URL"]
        if "token_env_vars" not in settings:
            settings["token_env_vars"] = ["PEERING_MANAGER_TOKEN"]
        if "username_env_vars" not in settings:
            settings["username_env_vars"] = ["PEERING_MANAGER_USERNAME"]
        if "password_env_vars" not in settings:
            settings["password_env_vars"] = ["PEERING_MANAGER_PASSWORD"]

        settings.setdefault("response_key_pattern", "results")

        super().__init__(
            target=target,
            adapter=adapter.model_copy(update={"settings": settings}),
            config=config,
            adapter_type="PeeringManager",
            **kwargs,
        )


class PeeringmanagerModel(GenericrestapiModel):
    """PeeringManager model that extends the generic REST API model."""

    @classmethod
    def create(
        cls,
        adapter: Adapter,
        ids: dict[Any, Any],
        attrs: dict[Any, Any],
    ) -> Self | None:
        # TODO: To implement
        return super().create(adapter=adapter, ids=ids, attrs=attrs)
