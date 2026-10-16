from __future__ import annotations

import logging
from typing import Any

from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.configuration.credentials import select_runtime_credential

from .references import owner_kind, reference_is_required, resolve_peer_list
from .rest_api_client import RestApiClient
from .utils import derive_identifier_key, get_value

logger = logging.getLogger(__name__)


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
        url_env_vars = settings.get("url_env_vars", ["GENERICRESTAPI_URL", "GENERICRESTAPI_ADDRESS"])
        url = select_runtime_credential(settings, "url", tuple(url_env_vars))

        # Get settings with defaults
        api_endpoint = settings.get("api_endpoint", "/api/v0")
        auth_method = settings.get("auth_method", "token")
        token_env_vars = settings.get("token_env_vars", ["GENERICRESTAPI_TOKEN"])
        api_token = select_runtime_credential(settings, "token", tuple(token_env_vars))
        username_env_vars = settings.get("username_env_vars", ["GENERICRESTAPI_USERNAME"])
        username = select_runtime_credential(settings, "username", tuple(username_env_vars))
        password_env_vars = settings.get("password_env_vars", ["GENERICRESTAPI_PASSWORD"])
        password = select_runtime_credential(settings, "password", tuple(password_env_vars))

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

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        """Return every object of the mapped resource endpoint, before any filter."""
        resource_name = element.mapping or ""
        try:
            response_data = self.client.get(endpoint=resource_name, params=self.params)
            objs = self._extract_objects_from_response(
                response_data=response_data, resource_name=resource_name, element=element
            )
        except Exception as exc:
            msg = f"Error fetching data from REST API: {exc!s}"
            raise ValueError(msg) from exc

        logger.info("%s: Read %d %s", self.type, len(objs), resource_name)
        return objs

    def record_source_id(self, record: Any) -> str | None:
        """Return the identifier `record_to_data` uses as `local_id`, or None when there is none."""
        if not isinstance(record, dict):
            return None
        try:
            return str(derive_identifier_key(obj=record))
        except ValueError:
            return None

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

        # Filter each branch to dicts so `record_to_data` (which calls `.get(...)`) never sees non-dict items.
        if isinstance(objs, dict):
            return [v for v in objs.values() if isinstance(v, dict)]
        if isinstance(objs, list):
            return [v for v in objs if isinstance(v, dict)]
        return []

    def record_to_data(
        self, record: dict[str, Any], element: SchemaMappingModel, model: type[DiffSyncModel]
    ) -> dict[str, Any]:
        """
        Convert an object to DiffSync format.

        This method handles the common pattern of converting API objects to DiffSync models
        with support for field mapping, static values, and references. References resolve
        through `peer_unique_id`.
        """
        obj_id = derive_identifier_key(obj=record)
        data: dict[str, Any] = {"local_id": str(obj_id)}
        owner = (owner_kind(model, element.name), str(obj_id))

        if not element.fields:
            msg = f"No fields defined in schema mapping for model {model.__name__}"
            raise ValueError(msg)

        for field in element.fields:
            field_is_list = model.is_list(name=field.name)  # ty: ignore[unresolved-attribute]

            if field.static:
                data[field.name] = field.static
            elif not field_is_list and field.mapping and not field.reference:
                value = get_value(record, field.mapping)
                if value is not None:
                    data[field.name] = value
            elif field_is_list and field.mapping and not field.reference:
                msg = "it's not supported yet to have an attribute of type list with a simple mapping"
                raise NotImplementedError(msg)

            elif field.mapping and field.reference:
                required = reference_is_required(model, field.name)
                if not field_is_list:
                    node = get_value(record, field.mapping)
                    if not node:
                        continue
                    if not isinstance(node, dict):
                        # Some link are referencing the node identifier directly without the id (i.e location in device)
                        data[field.name] = node
                        continue
                    unique_id = self.peer_unique_id(
                        field.reference, str(node.get("id", None)), owner=owner, field=field.name, required=required
                    )
                    if unique_id is not None:
                        data[field.name] = unique_id
                else:
                    peer_ids = [node.get("id", None) for node in get_value(record, field.mapping) or [] if node]
                    data[field.name], _ = resolve_peer_list(
                        self,
                        peer_kind=field.reference,
                        peer_source_ids=peer_ids,
                        owner=owner,
                        field=field.name,
                        required=required,
                    )

        return data


class GenericrestapiModel(DiffSyncModelMixin, DiffSyncModel):
    """
    A generic model class for REST API adapters.
    """
