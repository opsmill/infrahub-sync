from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests
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
        settings = adapter.settings or {}

        # Apply PeeringManager-specific defaults if not specified
        if "auth_method" not in settings:
            settings["auth_method"] = "token"
        if "api_endpoint" not in settings:
            settings["api_endpoint"] = "/api"
        if "url_env_vars" not in settings:
            settings["url_env_vars"] = ["PEERING_MANAGER_ADDRESS", "PEERING_MANAGER_URL"]
        if "token_env_vars" not in settings:
            settings["token_env_vars"] = ["PEERING_MANAGER_TOKEN"]

        settings.setdefault("response_key_pattern", "results")

        # Save the original settings back to the adapter
        adapter.settings = settings

        super().__init__(target=target, adapter=adapter, config=config, adapter_type="PeeringManager", **kwargs)


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

    def update(self, attrs: dict) -> Self | None:
        """
        Update an object in the Peering Manager system with new attributes.

        This method maps the given attributes to the corresponding target fields
        based on the schema mapping configuration, and sends an update request
        to the API endpoint of the object.
        """
        adapter = self.adapter
        assert adapter is not None
        # `adapter` is typed as the base diffsync.Adapter; `config` and `client` come from the concrete subclass.
        resource_name = self.__class__.get_resource_name(schema_mapping=adapter.config.schema_mapping)  # ty: ignore[unresolved-attribute]

        # Determine the unique identifier for the API request
        unique_identifier = self.local_id if hasattr(self, "local_id") else self.get_unique_id()
        endpoint = f"{resource_name}/{unique_identifier}/"

        # Map incoming attributes to the target attributes based on schema mapping
        mapped_attrs: dict[str, Any] = {}
        for field in adapter.config.schema_mapping:  # ty: ignore[unresolved-attribute]
            if field.name == self.__class__.get_type():
                for field_mapping in field.fields:
                    # Map source field name to target field name
                    if field_mapping.name in attrs:
                        target_field_name = field_mapping.mapping
                        value = attrs[field_mapping.name]

                        # Check if the field is a relationship
                        if field_mapping.reference:
                            all_nodes_for_reference = adapter.store.get_all(model=field_mapping.reference)

                            if isinstance(value, list):
                                # For lists, filter nodes to match the unique IDs in the attribute value
                                filtered_nodes = [
                                    node for node in all_nodes_for_reference if node.get_unique_id() in value
                                ]
                                mapped_attrs[target_field_name] = [node.local_id for node in filtered_nodes]  # ty: ignore[unresolved-attribute]
                            else:
                                # For single references, find the matching node
                                filtered_node = next(
                                    (node for node in all_nodes_for_reference if node.get_unique_id() == value),
                                    None,
                                )
                                if filtered_node:
                                    mapped_attrs[target_field_name] = filtered_node.local_id  # ty: ignore[unresolved-attribute]
                        else:
                            mapped_attrs[target_field_name] = value

        # Attempt to send the update request to the API
        try:
            adapter.client.patch(endpoint, data=mapped_attrs)  # ty: ignore[unresolved-attribute]
            return super().update(attrs)
        except (requests.exceptions.HTTPError, ConnectionError) as exc:
            msg = f"Error during update: {exc!s}"
            raise ValueError(msg) from exc
