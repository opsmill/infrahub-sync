from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)

from .rest_api_client import RestApiClient
from .utils import derive_identifier_key, get_value

if TYPE_CHECKING:
    from collections.abc import Mapping


class GenericrestapiAdapter(DiffSyncMixin, Adapter):
    """
    A generic REST API adapter that can be configured for different tools and APIs.
    This adapter reduces code duplication by providing a configurable base class
    that can work with various REST APIs that follow similar patterns.
    """

    def __init__(
        self,
        target: str,
        adapter: SyncAdapter,
        config: SyncConfig,
        adapter_type: str | None = "GenericRestApi",
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.target = target
        self.type = adapter_type
        self.settings = adapter.settings or {}
        self.params = self.settings.get("params", {})
        self.client = self._create_rest_client(settings=self.settings)
        self.config = config

    def _create_rest_client(self, settings: dict) -> RestApiClient:
        """
        Create a REST API client with flexible configuration.

        This method supports various configuration patterns through settings:
        - Custom environment variable names
        - Flexible auth methods and endpoints
        - Configurable timeouts and SSL verification
        """
        # Get URL from multiple possible sources
        url_env_vars = settings.get("url_env_vars", ["URL", "ADDRESS"])
        url = None
        for env_var in url_env_vars:
            url = os.environ.get(env_var)
            if url:
                break

        if not url:
            url = settings.get("url")

        # Get API endpoint configuration
        api_endpoint = settings.get("api_endpoint", "/api/v0")

        # Get authentication configuration
        auth_method = settings.get("auth_method", "token")

        # Get authentication credentials from multiple sources
        token_env_vars = settings.get("token_env_vars", ["TOKEN"])
        api_token = None
        for env_var in token_env_vars:
            api_token = os.environ.get(env_var)
            if api_token:
                break

        if not api_token:
            api_token = settings.get("token")

        username_env_vars = settings.get("username_env_vars", ["USERNAME"])
        username = None
        for env_var in username_env_vars:
            username = os.environ.get(env_var)
            if username:
                break

        if not username:
            username = settings.get("username")

        password_env_vars = settings.get("password_env_vars", ["PASSWORD"])
        password = None
        for env_var in password_env_vars:
            password = os.environ.get(env_var)
            if password:
                break

        if not password:
            password = settings.get("password")

        # Other configuration
        timeout = settings.get("timeout", 30)
        verify_ssl = settings.get("verify_ssl", True)

        if not url:
            msg = "url must be specified!"
            raise ValueError(msg)

        # Validate authentication based on method
        if auth_method in ["token", "x-auth-token", "api-key", "key"] and not api_token:
            msg = f"Authentication method '{auth_method}' requires a valid API token!"
            raise ValueError(msg)
        if auth_method == "basic" and (not username or not password):
            msg = "Basic authentication requires both username and password!"
            raise ValueError(msg)

        # Construct full URL
        full_base_url = f"{url.rstrip('/')}/{api_endpoint.strip('/')}"

        return RestApiClient(
            base_url=full_base_url,
            auth_method=auth_method,
            api_token=api_token,
            username=username,
            password=password,
            timeout=timeout,
            verify=verify_ssl,
        )

    def model_loader(self, model_name: str, model: GenericrestapiModel) -> None:
        """
        Load and process models using schema mapping filters and transformations.

        This method retrieves data from REST API, applies filters and transformations
        as specified in the schema mapping, and loads the processed data into the adapter.
        """
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            # Use the resource endpoint from the schema mapping
            resource_name = element.mapping

            try:
                # Fetch data from the specified resource endpoint
                response_data = self.client.get(endpoint=resource_name, params=self.params)

                # Extract objects from response using configurable response extraction
                objs = self._extract_objects_from_response(
                    response_data=response_data, resource_name=resource_name, element=element
                )
            except Exception as exc:
                msg = f"Error fetching data from REST API: {exc!s}"
                raise ValueError(msg) from exc

            total = len(objs)
            if self.config.source.name.title() == self.type.title():
                # Filter records
                filtered_objs = model.filter_records(records=objs, schema_mapping=element)
                print(f"{self.type}: Loading {len(filtered_objs)}/{total} {resource_name}")
                # Transform records
                transformed_objs = model.transform_records(records=filtered_objs, schema_mapping=element)
            else:
                print(f"{self.type}: Loading all {total} {resource_name}")
                transformed_objs = objs

            # Create model instances after filtering and transforming
            for obj in transformed_objs:
                data = self.obj_to_diffsync(obj=obj, mapping=element, model=model)
                item = model(**data)
                self.add(item)

    def _extract_objects_from_response(
        self,
        response_data: dict[str, Any],
        resource_name: str,
        element: SchemaMappingModel,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        """
        Extract objects from API response data.

        This method provides flexible extraction logic that can be customized
        per adapter through settings or schema mapping configuration.
        """
        # Check if there's a custom response key specified in the settings
        response_key_pattern: str | None = self.settings.get("response_key_pattern")
        default_key = resource_name.rstrip("/").rsplit("/", maxsplit=1)[-1]

        response_key = (
            response_key_pattern.format(resource=resource_name, default=default_key)
            if response_key_pattern
            else default_key
        )

        # Try to get data using the response key
        objs = response_data.get(response_key, response_data.get(resource_name, {}))

        # Handle different response formats
        if isinstance(objs, dict):
            # If it's a dict, convert values to list (like Observium)
            objs = list(objs.values())
        elif not isinstance(objs, list):
            # If it's neither dict nor list, wrap in list
            objs = [objs] if objs else []

        return objs

    def obj_to_diffsync(self, obj: dict[str, Any], mapping: SchemaMappingModel, model: GenericrestapiModel) -> dict:
        """
        Convert an object to DiffSync format.

        This method handles the common pattern of converting API objects to DiffSync models
        with support for field mapping, static values, and references.
        """
        obj_id = derive_identifier_key(obj=obj)
        data: dict[str, Any] = {"local_id": str(obj_id)}

        if not mapping.fields:
            msg = f"No fields defined in schema mapping for model {model.__name__}"
            raise ValueError(msg)

        for field in mapping.fields:  # pylint: disable=too-many-nested-blocks
            field_is_list = model.is_list(name=field.name)

            if field.static:
                data[field.name] = field.static
            elif not field_is_list and field.mapping and not field.reference:
                value = get_value(obj, field.mapping)
                if value is not None:
                    data[field.name] = value
            elif field_is_list and field.mapping and not field.reference:
                msg = "it's not supported yet to have an attribute of type list with a simple mapping"
                raise NotImplementedError(msg)

            elif field.mapping and field.reference:
                all_nodes_for_reference = self.store.get_all(model=field.reference)
                nodes = [item for item in all_nodes_for_reference]
                if not nodes and all_nodes_for_reference:
                    msg = (
                        f"Unable to get '{field.mapping}' with '{field.reference}' reference from store."
                        f" The available models are {self.store.get_all_model_names()}"
                    )
                    raise IndexError(msg)
                if not field_is_list:
                    if node := get_value(obj, field.mapping):
                        if isinstance(node, dict):
                            matching_nodes = []
                            node_id = node.get("id", None)
                            matching_nodes = [item for item in nodes if item.local_id == str(node_id)]
                            if len(matching_nodes) == 0:
                                msg = f"Unable to locate the node {model} {node_id}"
                                raise IndexError(msg)
                            node = matching_nodes[0]
                            data[field.name] = node.get_unique_id()
                        else:
                            # Some link are referencing the node identifier directly without the id (i.e location in device)
                            data[field.name] = node

                else:
                    data[field.name] = []
                    for node in get_value(obj, field.mapping):
                        if not node:
                            continue
                        node_id = node.get("id", None)
                        if not node_id and isinstance(node, tuple):
                            node_id = node[1] if node[0] == "id" else None
                            if not node_id:
                                continue
                        matching_nodes = [item for item in nodes if item.local_id == str(node_id)]
                        if len(matching_nodes) == 0:
                            msg = f"Unable to locate the node {field.reference} {node_id}"
                            raise IndexError(msg)
                        data[field.name].append(matching_nodes[0].get_unique_id())
                    data[field.name] = sorted(data[field.name])

        return data


class GenericrestapiModel(DiffSyncModelMixin, DiffSyncModel):
    """
    A generic model class for REST API adapters.
    """

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
