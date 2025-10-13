"""Cisco ACI API."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, ClassVar

import requests  # type: ignore[import]
import urllib3
from diffsync import Adapter, DiffSyncModel
from requests import Response  # type: ignore[import]
from requests.adapters import HTTPAdapter  # type: ignore[import]
from typing_extensions import Self
from urllib3.util.retry import Retry  # type: ignore[import]

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)

from .utils import get_value

if TYPE_CHECKING:
    import builtins

logger = logging.getLogger(__name__)


class InvalidAciCredentialError(ValueError):
    """Exception raised for invalid ACI credentials."""

    def __init__(self, field: str) -> None:
        # Accept the field name (recommended) and produce a consistent message
        msg = f"{field} must be a non-empty string"
        super().__init__(msg)


class AciApiClient:
    """Representation and methods for interacting with aci."""

    def __init__(
        self,
        username: str,
        password: str,
        base_url: str,
        verify: bool | str | None = True,
    ) -> None:
        """Initialization of AciApiClient.

        Args:
          username: APIC username
          password: APIC password
          base_url: Base URL of the APIC API (must include trailing slash handling)
          verify: SSL verification flag. Accepts bool or string values (for env parsing).
        """
        # Basic input validation to fail fast and provide clear errors
        if not isinstance(username, str) or not username:
            field_name = "username"
            raise InvalidAciCredentialError(field_name)
        if not isinstance(password, str) or not password:
            field_name = "password"
            raise InvalidAciCredentialError(field_name)
        if not isinstance(base_url, str) or not base_url:
            field_name = "base_url"
            raise InvalidAciCredentialError(field_name)

        # Normalize verify to a bool (support string env values like "false", "0")
        # Normalize verify to a boolean. Accepts strings like "false", "0", "no".
        self.verify = verify.lower() not in ("0", "false", "no") if isinstance(verify, str) else bool(verify)

        self.username = username
        self.password = password
        self.base_url = base_url
        self.cookies = ""
        self.last_login: datetime | None = None
        self.refresh_timeout: int | None = None
        # Suppress TLS warnings only when verification is disabled
        if not self.verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        # Use a requests Session for connection pooling and to preserve cookies
        self.session: requests.Session = requests.Session()
        # Configure retries with backoff for idempotent operations and common transient errors
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _login(self) -> Response:
        """Method to log into the ACI fabric and retrieve the token."""
        payload = {"aaaUser": {"attributes": {"name": self.username, "pwd": self.password}}}
        url = self.base_url + "aaaLogin.json"
        resp = self._handle_request(url, request_type="post", data=payload)
        if resp.ok:
            self.cookies = resp.cookies
            # Store last login time as UTC to avoid timezone related attribute errors
            self.last_login = datetime.now(tz=timezone.utc)
            self.refresh_timeout = int(resp.json()["imdata"][0]["aaaLogin"]["attributes"]["refreshTimeoutSeconds"])
        return resp

    def _handle_request(
        self,
        url: str,
        params: dict | None = None,
        request_type: str = "get",
        data: dict | None = None,
    ) -> Response:
        """Send a REST API call to the APIC."""
        try:
            resp = self.session.request(
                method=request_type.upper(),
                url=url,
                cookies=self.cookies,
                params=params,
                verify=self.verify,
                json=data,
                timeout=30,
            )
            resp.raise_for_status()
        except requests.exceptions.HTTPError as http_err:
            # Log and propagate HTTP errors with context (include traceback)
            msg = f"HTTP error when calling {url}: {http_err}"
            logger.exception(msg)
            raise
        except requests.exceptions.ConnectionError as conn_err:
            msg = f"Connection error when calling {url}: {conn_err}"
            logger.exception(msg)
            raise ConnectionError(msg) from conn_err
        except requests.exceptions.RequestException as req_err:
            # Catch-all for other requests exceptions (timeout, too many redirects, etc.)
            msg = f"Request failed when calling {url}: {req_err}"
            logger.exception(msg)
            raise ConnectionError(msg) from req_err
        return resp

    def _request(self, method: str, uri: str, params: dict | None = None, data: dict | None = None) -> Response:
        """Helper to perform authenticated requests. Ensures login/refresh before sending."""
        url = self.base_url + uri
        # Ensure we are logged in; if login fails we'll raise in _handle_error
        if self._refresh_token():
            login_resp = self._login()
            if not login_resp.ok:
                self._handle_error(login_resp)

        resp = self._handle_request(url, params=params, request_type=method, data=data)
        if resp.ok:
            return resp
        self._handle_error(resp)
        # This line should never be reached since _handle_error raises
        msg = "Unhandled error in AciApiClient._request"
        raise requests.exceptions.HTTPError(msg)

    def _refresh_token(self) -> bool:
        """Private method to check if the login token needs refreshed.
        Returns: True if login needs refresh."""
        if not self.last_login or not self.refresh_timeout:
            # If we've never logged in or refresh timeout is unavailable, force login
            return True
        # Compare using UTC-aware datetimes
        return datetime.now(tz=timezone.utc) - self.last_login > timedelta(seconds=self.refresh_timeout)

    def _handle_error(self, response: Response) -> None:
        """Private method to handle HTTP errors by raising an HTTPError.

        Args:
          response: requests.Response instance triggering the error
        """
        msg = (
            f"There was an HTTP error while performing operation on {self.base_url}: "
            f"Error: {response.status_code}, Reason: {response.reason}"
        )
        logger.error(msg)
        raise requests.exceptions.HTTPError(msg)

    def get(self, uri: str, params: dict | None = None) -> Response:
        """Retrieve data from the ACI fabric and return the raw Response."""
        return self._request("get", uri, params=params)

    def post(self, uri: str, params: dict | None = None, data: dict | None = None) -> Response:
        """Post data to the ACI fabric and return the raw Response."""
        return self._request("post", uri, params=params, data=data)


class AciAdapter(DiffSyncMixin, Adapter):
    """Adapter for Cisco ACI API"""

    type = "CiscoAci"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args: Any, **kwargs: Any) -> None:
        """Initialize aci adapter"""
        super().__init__(*args, **kwargs)
        self.target = target
        self.client = self._create_aci_client(adapter=adapter)
        self.config = config
        # Build device mapping once during initialization
        self.device_mapping = self._build_device_mapping()
        AciModel.set_device_mapping(self.device_mapping)

    def _create_aci_client(self, adapter: SyncAdapter) -> AciApiClient:
        settings = adapter.settings or {}
        url = os.environ.get("CISCO_APIC_URL") or settings.get("url")
        username = os.environ.get("CISCO_APIC_USERNAME") or settings.get("username")
        password = os.environ.get("CISCO_APIC_PASSWORD") or settings.get("password")
        # Prefer explicit env var for verify; allow boolean or string values
        verify_raw = os.environ.get("CISCO_APIC_VERIFY")
        if verify_raw is None:
            verify_raw = settings.get("verify", True)
        verify = verify_raw.lower() not in ("0", "false", "no") if isinstance(verify_raw, str) else bool(verify_raw)
        api_endpoint = settings.get("api_endpoint", "api")  # Default endpoint, change if necessary

        if not url:
            msg = "url must be specified!"
            raise ValueError(msg)
        if not username or not password:
            msg = "username and password must be specified!"
            raise ValueError(msg)

        full_base_url = f"{url.rstrip('/')}/{api_endpoint.rstrip('/')}/"
        return AciApiClient(
            base_url=full_base_url,
            username=str(username),
            password=str(password),
            verify=verify,
        )

    def model_loader(self, model_name: str, model: builtins.type[AciModel]) -> None:  # type: ignore[valid-type]
        """
        Load and process models using schema mapping filters and transformations.

        This method retrieves data from ACI, applies filters and transformations
        as specified in the schema mapping, and loads the processed data into the adapter.
        """
        # Retrieve schema mapping for this model
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue
            if not element.fields:
                logger.debug("No fields defined for schema mapping %s, skipping", element.name)
                continue

            # Use the resource endpoint from the schema mapping
            resource_name = element.mapping

            try:
                # Retrieve all objects
                response_data = self.client.get(resource_name)
                objs = response_data.json().get("imdata", [])
            except Exception as exc:
                msg = f"Error fetching data from REST API: {exc!s}"
                raise ValueError(msg) from exc

            logger.debug(json.dumps(objs, indent=2))
            total = len(objs)
            # Always apply filters and transforms for the source adapter
            # Check if this is the source adapter by comparing source name with adapter type
            is_source_adapter = self.config.source.name.lower() == "aci"
            if is_source_adapter:
                # Filter records
                filtered_objs = model.filter_records(records=objs, schema_mapping=element)  # type: ignore[attr-defined]
                logger.info("%s: Loading %d/%d %s", self.type, len(filtered_objs), total, resource_name)
                # Transform records
                transformed_objs = model.transform_records(records=filtered_objs, schema_mapping=element)  # type: ignore[attr-defined]
            else:
                logger.info("%s: Loading all %d %s", self.type, total, resource_name)
                transformed_objs = objs

            # Create model instances after filtering and transforming
            for obj in transformed_objs:
                data = self.obj_to_diffsync(obj=obj, mapping=element, model=model)
                item = model(**data)  # type: ignore[misc]
                self.add(item)

    def obj_to_diffsync(
        self,
        obj: dict[str, Any],
        mapping: SchemaMappingModel,
        model: builtins.type[AciModel],  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Convert an object to DiffSync format based on the provided mapping schema."""
        obj_id = self._extract_aci_id(obj)

        data: dict[str, Any] = {"local_id": str(obj_id) if obj_id else ""}

        if not mapping.fields:
            return data
        for field in mapping.fields:
            field_is_list = model.is_list(name=field.name)  # type: ignore[attr-defined]

            if field.static:
                data[field.name] = field.static
            elif field.mapping and not field.reference:
                value = get_value(obj, field.mapping)
                if value is not None:
                    data[field.name] = value
            elif field.mapping and field.reference:
                all_nodes_for_reference = self.store.get_all(model=field.reference)
                nodes = list(all_nodes_for_reference)

                if not nodes and all_nodes_for_reference:
                    msg = (
                        f"Unable to get '{field.mapping}' with '{field.reference}' reference from store. "
                        f"The available models are {self.store.get_all_model_names()}"
                    )
                    raise ValueError(msg)

                if field_is_list:
                    data[field.name] = self._process_list_field(obj, field, nodes)
                else:
                    data[field.name] = self._process_single_field(obj, field, nodes)
        logger.debug("Converted object to diffsync data: %s", data)
        return data

    def _process_list_field(self, obj: dict[str, Any], field: Any, nodes: list[Any]) -> list[str]:
        """Process a list field and return a list of unique IDs."""
        unique_ids = []
        nodes_in_obj = get_value(obj, field.mapping) or []
        for node in nodes_in_obj:
            if not node:
                continue
            node_id = str(node) if node else ""
            matching_nodes = [item for item in nodes if item.local_id == node_id]
            if not matching_nodes:
                msg = f"Unable to locate the node {field.reference} {node_id}"
                raise ValueError(msg)
            unique_ids.append(matching_nodes[0].get_unique_id())
        return sorted(unique_ids)

    def _process_single_field(self, obj: dict[str, Any], field: Any, nodes: list[Any]) -> str | None:
        """Process a single field and return its unique ID."""
        # Check if this field has already been transformed (e.g., by Jinja filters)
        if field.name in obj:
            # Use the already transformed value from the object
            node_value = str(obj[field.name])
        else:
            # Fall back to extracting from the original mapping
            node = get_value(obj, field.mapping)
            if not node:
                return None
            node_value = str(node)

        # For device references, try matching against both local_id and name
        # since devices might be referenced by name (due to identifier configuration)
        matching_nodes = [item for item in nodes if item.local_id == node_value]

        if not matching_nodes:
            # Try matching against the name field for device references
            matching_nodes = [item for item in nodes if getattr(item, "name", None) == node_value]

        if not matching_nodes:
            available_ids = [getattr(item, "local_id", "N/A") for item in nodes[:5]]
            available_names = [getattr(item, "name", "N/A") for item in nodes[:5]]
            msg = (
                f"Unable to locate the node {field.reference} {node_value} for field '{field.name}'. "
                f"Available node IDs (first 5): {available_ids}, "
                f"Available names (first 5): {available_names}"
            )
            logger.error(msg)
            raise ValueError(msg)

        return matching_nodes[0].get_unique_id()

    def _build_device_mapping(self) -> dict[str, str]:
        """Build device mapping from ACI fabricNode data during initialization."""
        device_mapping = {}

        try:
            # Query ACI for fabricNode data once
            response = self.client.get("class/fabricNode.json")
            fabric_nodes = response.json().get("imdata", [])

            # Build mapping from fabricNode data
            for obj in fabric_nodes:
                if "fabricNode" in obj:
                    fabric_node = obj["fabricNode"]
                    if isinstance(fabric_node, dict) and "attributes" in fabric_node:
                        attrs = fabric_node["attributes"]
                        node_id = attrs.get("id")
                        node_name = attrs.get("name")

                        if node_id and node_name:
                            device_mapping[str(node_id)] = node_name

            logger.info("Built ACI device mapping with %d entries", len(device_mapping))
            logger.debug("Device mapping: %s", device_mapping)

        except (requests.exceptions.RequestException, ValueError, KeyError) as e:
            logger.warning("Failed to build device mapping from fabricNode data: %s", e)

        return device_mapping

    def _extract_aci_id(self, obj: dict[str, Any]) -> str | None:
        """Extract ID from ACI objects, handling the nested structure."""
        if not obj:
            return None

        # Check for fabricNode structure first
        if "fabricNode" in obj:
            fabric_node = obj["fabricNode"]
            if isinstance(fabric_node, dict) and "attributes" in fabric_node:
                attrs = fabric_node["attributes"]
                node_id = attrs.get("id")
                if node_id:
                    return str(node_id)

        # Fallback to generic id field
        obj_id = obj.get("id")
        if obj_id:
            return str(obj_id)

        return None


class AciModel(DiffSyncModelMixin, DiffSyncModel):
    """ACI Model with ACI-specific Jinja filters."""

    # Class-level storage for device mapping (set by AciAdapter)
    _device_mapping: ClassVar[dict[str, str]] = {}

    @classmethod
    def set_device_mapping(cls, device_mapping: dict[str, str]) -> None:
        """Set the device mapping for ACI models."""
        cls._device_mapping = device_mapping

    @classmethod
    def _add_custom_filters(cls, native_env: Any, item: dict[str, Any]) -> None:  # noqa: ARG003
        """Add ACI-specific filters to the Jinja environment."""

        def aci_device_name(node_id: str) -> str:
            """
            Custom Jinja filter to resolve ACI node IDs to device names using pre-built mapping.

            Args:
                node_id: The ACI node ID (e.g., "101", "102")

            Returns:
                Device name if found, otherwise returns the original node_id
            """
            return cls._device_mapping.get(str(node_id), node_id)

        native_env.filters["aci_device_name"] = aci_device_name

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
        # TODO: To implement
        return super().update(attrs)
