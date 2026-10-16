"""Cisco ACI API."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

import requests
import urllib3
from diffsync import Adapter, DiffSyncModel
from requests import Response
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.configuration.credentials import is_registered_context, select_runtime_credential

from .references import owner_kind, reference_is_required, resolve_peer_list
from .utils import get_value

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
        """Build the ACI client with environment-first settings and safe TLS defaults."""
        settings = adapter.settings or {}
        url = select_runtime_credential(settings, "url", ("CISCO_APIC_URL",))
        username = select_runtime_credential(settings, "username", ("CISCO_APIC_USERNAME",))
        password = select_runtime_credential(settings, "password", ("CISCO_APIC_PASSWORD",))
        # Direct use prefers an explicit env var for verify; allow boolean or string values.
        # An empty env value falls through to the declared setting. This is env-first,
        # matching select_runtime_credential and this function's own url/username/password
        # reads. A registered package's declared setting is never overridden.
        verify_raw = None if is_registered_context(settings) else os.environ.get("CISCO_APIC_VERIFY")
        if not verify_raw:
            verify_raw = settings.get("verify", True)
            if verify_raw is None:
                # A declared `verify: null` is "unset", not "disabled"; keep the secure default.
                verify_raw = True
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

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        """Return every object of the mapped ACI class, before any filter.

        A mapping entry without fields loads nothing.
        """
        if not element.fields:
            logger.debug("No fields defined for schema mapping %s, skipping", element.name)
            return []
        resource_name = element.mapping or ""
        try:
            response_data = self.client.get(resource_name)
            objs = response_data.json().get("imdata", [])
        except Exception as exc:
            msg = f"Error fetching data from REST API: {exc!s}"
            raise ValueError(msg) from exc

        logger.debug(json.dumps(objs, indent=2))
        logger.info("%s: Read %d %s", self.type, len(objs), resource_name)
        return objs

    def record_source_id(self, record: Any) -> str | None:
        """Return the ACI ID `record_to_data` uses as `local_id`."""
        return self._extract_aci_id(record) if isinstance(record, dict) else None

    def record_to_data(
        self,
        record: dict[str, Any],
        element: SchemaMappingModel,
        model: type[DiffSyncModel],
    ) -> dict[str, Any]:
        """Convert an object to DiffSync format based on the provided mapping schema."""
        obj_id = self._extract_aci_id(record)

        data: dict[str, Any] = {"local_id": str(obj_id) if obj_id else ""}
        owner = (owner_kind(model, element.name), data["local_id"])

        if not element.fields:
            return data
        for field in element.fields:
            field_is_list = model.is_list(name=field.name)

            if field.static:
                data[field.name] = field.static
            elif field.mapping and not field.reference:
                value = get_value(record, field.mapping)
                if value is not None:
                    data[field.name] = value
            elif field.mapping and field.reference:
                required = reference_is_required(model, field.name)
                if field_is_list:
                    data[field.name] = self._process_list_field(record, field, owner=owner, required=required)
                else:
                    data[field.name] = self._process_single_field(record, field, owner=owner, required=required)
        logger.debug("Converted object to diffsync data: %s", data)
        return data

    def _process_list_field(
        self, obj: dict[str, Any], field: Any, *, owner: tuple[str, str | None], required: bool
    ) -> list[str]:
        """Process a list field and return a sorted list of unique IDs."""
        peer_ids = [str(node) for node in get_value(obj, field.mapping) or [] if node]
        unique_ids, _ = resolve_peer_list(
            self, peer_kind=field.reference, peer_source_ids=peer_ids, owner=owner, field=field.name, required=required
        )
        return unique_ids

    def _process_single_field(
        self, obj: dict[str, Any], field: Any, *, owner: tuple[str, str | None], required: bool
    ) -> str | None:
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

        # Devices might be referenced by name (due to identifier configuration), so a peer
        # with no matching local_id is looked for by name before the base class resolves it.
        if self._admission.peer_index.get((field.reference, node_value)) is None and (
            self._scan_store(field.reference, node_value) is None
        ):
            by_name = next(
                (
                    item
                    for item in self.store.get_all(model=field.reference)
                    if getattr(item, "name", None) == node_value
                ),
                None,
            )
            if by_name is not None:
                return by_name.get_unique_id()

        return self.peer_unique_id(field.reference, node_value, owner=owner, field=field.name, required=required)

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
