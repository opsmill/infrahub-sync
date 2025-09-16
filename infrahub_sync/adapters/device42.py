from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

from infrahub_sync import (
    SyncAdapter,
    SyncConfig,
    SchemaMappingModel,
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
        
        # Device42 API returns results in an "objects" key for most endpoints
        if "response_key_pattern" not in settings:
            settings["response_key_pattern"] = "objects"

        # Create a new adapter with updated settings
        updated_adapter = SyncAdapter(name=adapter.name, settings=settings)

        super().__init__(target=target, adapter=updated_adapter, config=config, adapter_type="Device42", **kwargs)

    def _extract_objects_from_response(
        self,
        response_data: dict[str, Any],
        resource_name: str,
        element: SchemaMappingModel,
    ) -> list[dict[str, Any]]:
        """
        Extract objects from Device42 API response data.
        
        Device42 API typically returns data in this format:
        {
          "objects": [...],
          "total_count": 123,
          "limit": 100, 
          "offset": 0
        }
        """
        # Device42 typically uses "objects" as the key for the data array
        objs = response_data.get("objects", [])
        
        # Fallback to generic extraction if "objects" key is not present
        if not objs:
            objs = super()._extract_objects_from_response(response_data, resource_name, element)
        
        # Handle pagination if needed (Device42 returns total_count for pagination info)
        total_count = response_data.get("total_count", len(objs))
        limit = response_data.get("limit", len(objs))
        offset = response_data.get("offset", 0)
        
        # For now, we'll handle the first page. In the future, this could be extended
        # to handle pagination automatically by making additional requests
        if total_count > len(objs) and offset + limit < total_count:
            # Log that there are more results available
            remaining = total_count - (offset + len(objs))
            print(f"Device42: Retrieved {len(objs)} of {total_count} {resource_name} records. {remaining} remaining.")
        
        return objs

    def _fetch_paginated_data(self, endpoint: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        Fetch all data from a paginated Device42 endpoint.
        
        This method handles Device42's pagination by making multiple requests
        if necessary to retrieve all available data.
        """
        all_objects = []
        offset = 0
        limit = 100  # Device42 default limit
        
        if params is None:
            params = {}
        
        while True:
            # Add pagination parameters
            paginated_params = params.copy()
            paginated_params.update({"offset": offset, "limit": limit})
            
            try:
                response_data = self.client.get(endpoint=endpoint, params=paginated_params)
                objects = response_data.get("objects", [])
                total_count = response_data.get("total_count", len(objects))
                
                all_objects.extend(objects)
                
                # Check if we've retrieved all objects
                if len(objects) < limit or offset + len(objects) >= total_count:
                    break
                    
                offset += limit
                
            except Exception as exc:
                msg = f"Error fetching paginated data from Device42 API: {exc!s}"
                raise ValueError(msg) from exc
        
        return all_objects


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