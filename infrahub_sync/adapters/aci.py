"""Cisco ACI API."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

from datetime import datetime, timedelta

import requests
import urllib3
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

from .utils import get_value

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class AciApiClient:
    """Representation and methods for interacting with aci."""

    def __init__(
        self,
        username,
        password,
        base_url,
        verify,
    ):
        """Initialization of aci class."""
        self.username = username
        self.password = password
        self.base_url = base_url
        self.verify = verify
        self.cookies = ""
        self.last_login = None
        self.refresh_timeout = None

    def _login(self):
        """Method to log into the ACI fabric and retrieve the token."""
        payload = {"aaaUser": {"attributes": {"name": self.username, "pwd": self.password}}}
        url = self.base_url + "aaaLogin.json"
        resp = self._handle_request(url, request_type="post", data=payload)
        if resp.ok:
            self.cookies = resp.cookies
            self.last_login = datetime.now(tz=self.last_login.tzinfo)
            self.refresh_timeout = int(resp.json()["imdata"][0]["aaaLogin"]["attributes"]["refreshTimeoutSeconds"])
        return resp

    def _handle_request(
        self,
        url: str,
        params: dict | None = None,
        request_type: str = "get",
        data: dict | None = None,
    ) -> object:
        """Send a REST API call to the APIC."""
        try:
            resp = requests.request(
                method=request_type,
                url=url,
                cookies=self.cookies,
                params=params,
                verify=self.verify,
                json=data,
                timeout=30,
            )
        except requests.exceptions.ConnectionError as error:
            msg = f"Error occurred communicating with {self.base_url}:\n{error}"
            raise requests.exceptions.ConnectionError(msg) from error
        return resp

    def _refresh_token(self):
        """Private method to check if the login token needs refreshed.
        Returns: True if login needs refresh."""
        if not self.last_login:
            return True
        # if time diff b/w now and last login greater than refresh_timeout then refresh login
        return datetime.now(tz=self.last_login.tzinfo) - self.last_login > timedelta(seconds=self.refresh_timeout)

    def _handle_error(self, response: object):
        """Private method to handle HTTP errors."""
        msg = (
            f"There was an HTTP error while performing operation on {self.base_url}:\n"
            f"Error: {response.status_code}, Reason: {response.reason}"
        )
        raise requests.exceptions.HTTPError(msg)

    def get(self, uri: str, params: dict | None = None) -> object:
        """Method to retrieve data from the ACI fabric."""
        url = self.base_url + uri
        if self._refresh_token():
            login_resp = self._login()
            if login_resp.ok:
                resp = self._handle_request(url, params)
                if resp.ok:
                    return resp
                return self._handle_error(resp)
            return self._handle_error(login_resp)
        resp = self._handle_request(url, params)
        if resp.ok:
            return resp
        return self._handle_error(resp)

    def post(self, uri: str, params: dict | None = None, data=None) -> object:
        """Method to post data to the ACI fabric."""
        url = self.base_url + uri
        if self._refresh_token():
            login_resp = self._login()
            if login_resp.ok:
                resp = self._handle_request(url, params, request_type="post", data=data)
                if resp.ok:
                    return resp
                return self._handle_error(resp)
            return self._handle_error(login_resp)
        resp = self._handle_request(url, params, request_type="post", data=data)
        if resp.ok:
            return resp
        return self._handle_error(resp)


class AciAdapter(DiffSyncMixin, Adapter):
    """Adapter for Cisco ACI API"""

    type = "CiscoAci"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        """Initialize aci adapter"""
        super().__init__(*args, **kwargs)
        self.target = target
        self.client = self._create_aci_client(adapter=adapter)
        self.config = config

    def _create_aci_client(self, adapter: SyncAdapter) -> AciApiClient:
        settings = adapter.settings or {}
        url = os.environ.get("CISCO_APIC_ADDRESS") or settings.get("url")
        username = os.environ.get("CISCO_APIC_USERNAME") or settings.get("username")
        password = os.environ.get("CISCO_APIC_PASSWORD") or settings.get("password")
        verify = os.environ.get("CISCO_APIC_VERIFY") or settings.get("verify")
        api_endpoint = settings.get("api_endpoint", "api")  # Default endpoint, change if necessary
        if not url:
            msg = "url must be specified!"
            raise ValueError(msg)

        full_base_url = f"{url.rstrip('/')}/{api_endpoint.rstrip('/')}/"
        return AciApiClient(
            base_url=full_base_url,
            username=username,
            password=password,
            verify=verify,
        )

    def model_loader(self, model_name: str, model: AciModel) -> None:
        """
        Load and process models using schema mapping filters and transformations.

        This method retrieves data from Peering Manager, applies filters and transformations
        as specified in the schema mapping, and loads the processed data into the adapter.
        """
        # Retrieve schema mapping for this model
        for element in self.config.schema_mapping:
            if element.name != model_name:
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

            import json

            print(json.dumps(objs, indent=2))
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

    def obj_to_diffsync(
        self,
        obj: dict[str, Any],
        mapping: SchemaMappingModel,
        model: AciModel,
    ) -> dict[str, Any]:
        """Convert an object to DiffSync format based on the provided mapping schema."""
        obj_id = obj.get("id")
        data: dict[str, Any] = {"local_id": str(obj_id)}

        for field in mapping.fields:
            field_is_list = model.is_list(name=field.name)

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
        print(data)
        return data

    def _process_list_field(self, obj: dict[str, Any], field: Any, nodes: list[Any]) -> list[str]:
        """Process a list field and return a list of unique IDs."""
        unique_ids = []
        for node in get_value(obj, field.mapping):
            if not node:
                continue
            node_id = self._get_node_id(node)
            if not node_id:
                continue
            matching_nodes = [item for item in nodes if item.local_id == str(node_id)]
            if not matching_nodes:
                msg = f"Unable to locate the node {field.reference} {node_id}"
                raise ValueError(msg)
            unique_ids.append(matching_nodes[0].get_unique_id())
        return sorted(unique_ids)

    def _process_single_field(self, obj: dict[str, Any], field: Any, nodes: list[Any]) -> str | None:
        """Process a single field and return its unique ID."""
        node = get_value(obj, field.mapping)
        if not node:
            return None
        node_id = self._get_node_id(node)
        if not node_id:
            return None
        matching_nodes = [item for item in nodes if item.local_id == str(node_id)]
        if not matching_nodes:
            msg = f"Unable to locate the node {field.name} {node_id}"
            raise ValueError(msg)
        return matching_nodes[0].get_unique_id()

    def _get_node_id(self, node: Any) -> str | None:
        """Extract the node ID from the given node."""
        result = None
        if isinstance(node, dict):
            result = node.get("id")
        elif isinstance(node, tuple) and len(node) == 2 and node[0] == "id":
            result = node[1]
        return result


class AciModel(DiffSyncModelMixin, DiffSyncModel):
    """ACI Model."""

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
        return super().update(attrs)
