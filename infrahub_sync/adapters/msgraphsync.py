"""Source-only adapter that reads users and groups from Microsoft Entra ID through Microsoft Graph.

The adapter builds every request with the `msgraph-sdk` request builders and reads the raw JSON
body, so records keep the Graph property names (`displayName`, `userPrincipalName`) that the
`schema_mapping` entries name. It sends `GET` requests only.

`msgraph-sdk` and `azure-identity` are optional dependencies:
`pip install msgraph-sdk azure-identity`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import random
import ssl
from collections import Counter
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import unquote, urlsplit

import httpx
import structlog
from azure.core.exceptions import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    AzureError,
    ClientAuthenticationError,
)
from azure.identity.aio import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    ClientSecretCredential,
    DefaultAzureCredential,
)
from diffsync import Adapter, DiffSyncModel
from kiota_abstractions.api_error import APIError  # ty: ignore[unresolved-import]  # pylint: disable=import-error
from kiota_abstractions.base_request_configuration import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    RequestConfiguration,
)
from kiota_authentication_azure.azure_identity_authentication_provider import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    AzureIdentityAuthenticationProvider,
)
from kiota_http.middleware.options import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    RetryHandlerOption,
)
from msgraph import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    GraphRequestAdapter,
    GraphServiceClient,
)
from msgraph.generated.models.o_data_errors.o_data_error import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    ODataError,
)
from msgraph.graph_request_adapter import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
    options as sdk_default_options,
)
from msgraph_core import GraphClientFactory  # ty: ignore[unresolved-import]  # pylint: disable=import-error

from infrahub_sync import DiffSyncMixin, DiffSyncModelMixin
from infrahub_sync.adapters.utils import get_value

if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence

    from kiota_abstractions.request_information import (  # ty: ignore[unresolved-import]  # pylint: disable=import-error
        RequestInformation,
    )
    from typing_extensions import Self

    from infrahub_sync import SchemaMappingModel, SyncAdapter, SyncConfig

logger = logging.getLogger(__name__)
# structlog over the package's standard logger: the CLI's level and handler apply, and caplog sees the records.
log = structlog.wrap_logger(
    logger,
    wrapper_class=structlog.stdlib.BoundLogger,
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.processors.KeyValueRenderer(key_order=["event"]),
    ],
)

USERS = "users"
GROUPS = "groups"
RESOURCES = (USERS, GROUPS)
# A collection cannot use these names: they already mean an unfiltered read of the resource.
RESERVED_COLLECTION_NAMES = frozenset(RESOURCES)
MEMBERS_KEY = "members"
USER_ODATA_TYPE = "#microsoft.graph.user"
REQUIRED_PERMISSIONS = ("User.Read.All", "Group.Read.All", "GroupMember.Read.All")
# Graph also requires this permission to list the members of a group with hidden membership.
HIDDEN_MEMBERSHIP_PERMISSION = "Member.Read.Hidden"

DEFAULT_GRAPH_URL = "https://graph.microsoft.com/v1.0"
DEFAULT_PAGE_SIZE = 999
MAX_PAGE_SIZE = 999
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 5
MAX_RETRIES_LIMIT = 10
# Without a `Retry-After` header, retry n waits FALLBACK_RETRY_SECONDS * 2**(n-1) seconds, at most MAX_BACKOFF_SECONDS.
FALLBACK_RETRY_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 60.0
# A `Retry-After` above this limit stops the read: retrying earlier than Graph allows only prolongs the throttling.
MAX_RETRY_WAIT_SECONDS = 300.0
MEMBER_CONCURRENCY = 4
RETRY_STATUSES = frozenset({429, 502, 503, 504})
# 401: Graph rejected the access token. 403: the token is valid but lacks a Graph permission.
PERMISSION_STATUSES = frozenset({401, 403})
# A page read as zero records would make the diff plan to delete every synced node, so such reads stop.
NO_RECORDS_NOTE = "The read stops so that the sync does not treat the missing records as deleted."

AUTH_CLIENT_SECRET = "client_secret"
AUTH_DEFAULT = "default"
AUTH_MODES = (AUTH_CLIENT_SECRET, AUTH_DEFAULT)
MEMBERSHIP_DIRECT = "direct"
MEMBERSHIP_TRANSITIVE = "transitive"
MEMBERSHIP_MODES = (MEMBERSHIP_DIRECT, MEMBERSHIP_TRANSITIVE)

ENV_TENANT_ID = "MSGRAPH_TENANT_ID"
ENV_CLIENT_ID = "MSGRAPH_CLIENT_ID"
ENV_CLIENT_SECRET = "MSGRAPH_CLIENT_SECRET"

ALLOWED_SETTINGS = (
    "auth",
    "tenant_id",
    "client_id",
    "graph_url",
    "page_size",
    "timeout",
    "max_retries",
    "membership",
    "select",
    "collections",
)
COLLECTION_KEYS = ("resource", "filter", "advanced_query", "member_of")
ERROR_MAPPING = {"XXX": ODataError}

# Retry waits go through this module attribute so that tests can replace it.
_sleep = asyncio.sleep

T = TypeVar("T")


class MsgraphConfigError(ValueError):
    """The adapter settings or the schema mapping contain a missing or invalid value."""


class MsgraphRequestError(RuntimeError):
    """A Microsoft Graph request failed.

    `status` is the HTTP status (None when no response arrived), `code` the Graph error code and
    `path` the request path and query. None of them contains a header or a token.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.path = path


class MsgraphPermissionError(MsgraphRequestError):
    """Microsoft Graph answered HTTP 401 (it rejected the access token) or 403 (a Graph permission is missing)."""


class MsgraphAuthenticationError(MsgraphRequestError):
    """The credential could not get an access token: the token endpoint refused the request or was not reached."""


@dataclass(frozen=True)
class Collection:
    """A named read of one Graph resource, optionally filtered with an OData `$filter`."""

    name: str
    resource: str
    filter: str | None = None
    advanced_query: bool = False
    member_of: str | None = None


@dataclass(frozen=True)
class MsgraphSettings:
    """Validated settings of the `msgraphsync` source, built with `from_settings`."""

    auth: str = AUTH_CLIENT_SECRET
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False, compare=False)
    graph_url: str = DEFAULT_GRAPH_URL
    page_size: int = DEFAULT_PAGE_SIZE
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    membership: str = MEMBERSHIP_DIRECT
    select: dict[str, tuple[str, ...]] = field(default_factory=dict)
    collections: dict[str, Collection] = field(default_factory=dict)

    @classmethod
    def from_settings(
        cls,
        raw: Mapping[str, Any] | None,
        environ: Mapping[str, str] | None = None,
    ) -> MsgraphSettings:
        """Validate the `source.settings` block and read the credentials from the environment.

        `MSGRAPH_TENANT_ID` and `MSGRAPH_CLIENT_ID` win over the `tenant_id` and `client_id`
        settings. The client secret comes only from `MSGRAPH_CLIENT_SECRET`.
        """
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            msg = f"The msgraphsync settings must be a mapping, got {type(raw).__name__}."
            raise MsgraphConfigError(msg)
        env: Mapping[str, str] = os.environ if environ is None else environ

        if AUTH_CLIENT_SECRET in raw:
            msg = (
                "Setting 'client_secret' is not accepted in the configuration file. "
                f"Set the environment variable {ENV_CLIENT_SECRET} instead."
            )
            raise MsgraphConfigError(msg)
        unknown = sorted(str(key) for key in raw if key not in ALLOWED_SETTINGS)
        if unknown:
            msg = f"Unknown msgraphsync setting(s): {', '.join(unknown)}. Allowed settings: {', '.join(ALLOWED_SETTINGS)}."
            raise MsgraphConfigError(msg)

        auth = _choice(raw, "auth", AUTH_MODES, AUTH_CLIENT_SECRET)
        tenant_id = env.get(ENV_TENANT_ID) or _optional_str(raw, "tenant_id")
        client_id = env.get(ENV_CLIENT_ID) or _optional_str(raw, "client_id")
        client_secret = env.get(ENV_CLIENT_SECRET) or None
        if auth == AUTH_CLIENT_SECRET:
            missing = [
                name
                for name, value in (
                    (ENV_TENANT_ID, tenant_id),
                    (ENV_CLIENT_ID, client_id),
                    (ENV_CLIENT_SECRET, client_secret),
                )
                if not value
            ]
            if missing:
                msg = (
                    f"auth 'client_secret' is missing values. Set the environment variable(s): {', '.join(missing)}. "
                    "The tenant and client IDs can also come from the 'tenant_id' and 'client_id' settings."
                )
                raise MsgraphConfigError(msg)

        return cls(
            auth=auth,
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            graph_url=_graph_url(raw),
            page_size=_int_in_range(raw, "page_size", DEFAULT_PAGE_SIZE, 1, MAX_PAGE_SIZE),
            timeout=_timeout(raw),
            max_retries=_int_in_range(raw, "max_retries", DEFAULT_MAX_RETRIES, 0, MAX_RETRIES_LIMIT),
            membership=_choice(raw, "membership", MEMBERSHIP_MODES, MEMBERSHIP_DIRECT),
            select=_select(raw.get("select")),
            collections=_collections(raw.get("collections")),
        )

    @property
    def token_scope(self) -> str:
        """The OAuth scope of the Graph instance in `graph_url`, for example `https://graph.microsoft.com/.default`."""
        parts = urlsplit(self.graph_url)
        return f"{parts.scheme}://{parts.netloc}/.default"

    @property
    def graph_host(self) -> str:
        """The host name of `graph_url`, the only host that receives the access token."""
        return urlsplit(self.graph_url).hostname or ""

    def resolve_collection(self, name: str) -> Collection:
        """Return the collection called `name`; `users` and `groups` are unfiltered reads."""
        if name in self.collections:
            return self.collections[name]
        if name in RESOURCES:
            return Collection(name=name, resource=name)
        valid = [*sorted(self.collections), *RESOURCES]
        msg = f"Unknown Microsoft Graph collection '{name}'. Valid names: {', '.join(valid)}."
        raise MsgraphConfigError(msg)


def _choice(raw: Mapping[str, Any], key: str, allowed: Sequence[str], default: str) -> str:
    value = raw.get(key, default)
    if value not in allowed:
        msg = f"Setting '{key}' must be one of {', '.join(allowed)}, got {value!r}."
        raise MsgraphConfigError(msg)
    return value


def _optional_str(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"Setting '{key}' must be a string, got {value!r}."
        raise MsgraphConfigError(msg)
    return value or None


def _int_in_range(raw: Mapping[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        msg = f"Setting '{key}' must be an integer from {low} to {high}, got {value!r}."
        raise MsgraphConfigError(msg)
    return value


def _timeout(raw: Mapping[str, Any]) -> float:
    value = raw.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        msg = f"Setting 'timeout' must be a number of seconds greater than 0, got {value!r}."
        raise MsgraphConfigError(msg)
    return float(value)


def _graph_url(raw: Mapping[str, Any]) -> str:
    value = raw.get("graph_url", DEFAULT_GRAPH_URL)
    if not isinstance(value, str) or not value.lower().startswith("https://") or not urlsplit(value).hostname:
        msg = (
            f"Setting 'graph_url' must be an https:// URL, got {value!r}. "
            "The Azure authentication provider sends tokens over HTTPS only."
        )
        raise MsgraphConfigError(msg)
    return value.rstrip("/")


def _str_tuple(value: Any, key: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    msg = f"Setting '{key}' must be a string or a list of strings, got {value!r}."
    raise MsgraphConfigError(msg)


def _select(value: Any) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        msg = f"Setting 'select' must be a mapping of {' or '.join(RESOURCES)} to property names, got {value!r}."
        raise MsgraphConfigError(msg)
    select: dict[str, tuple[str, ...]] = {}
    for resource, names in value.items():
        if resource not in RESOURCES:
            msg = f"Setting 'select' has key {resource!r}; allowed keys: {', '.join(RESOURCES)}."
            raise MsgraphConfigError(msg)
        select[resource] = _str_tuple(names, f"select.{resource}")
    return select


def _collections(value: Any) -> dict[str, Collection]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        msg = f"Setting 'collections' must be a mapping of collection names to definitions, got {value!r}."
        raise MsgraphConfigError(msg)
    return {name: _collection(name, body) for name, body in value.items()}


def _collection(name: Any, body: Any) -> Collection:
    if not isinstance(name, str) or not name:
        msg = f"Setting 'collections' has an invalid collection name {name!r}; names must be non-empty strings."
        raise MsgraphConfigError(msg)
    where = f"collections.{name}"
    if name in RESERVED_COLLECTION_NAMES:
        msg = (
            f"Setting '{where}': the name '{name}' is reserved for the unfiltered read of the resource. "
            "Choose another collection name."
        )
        raise MsgraphConfigError(msg)
    if not isinstance(body, Mapping):
        msg = f"Setting '{where}' must be a mapping with the keys {', '.join(COLLECTION_KEYS)}, got {body!r}."
        raise MsgraphConfigError(msg)
    unknown = sorted(str(key) for key in body if key not in COLLECTION_KEYS)
    if unknown:
        msg = f"Setting '{where}' has unknown key(s): {', '.join(unknown)}. Allowed keys: {', '.join(COLLECTION_KEYS)}."
        raise MsgraphConfigError(msg)
    resource = body.get("resource")
    if resource not in RESOURCES:
        msg = f"Setting '{where}.resource' must be one of {', '.join(RESOURCES)}, got {resource!r}."
        raise MsgraphConfigError(msg)
    query_filter = body.get("filter")
    if query_filter is not None and not isinstance(query_filter, str):
        msg = f"Setting '{where}.filter' must be a string (an OData $filter expression), got {query_filter!r}."
        raise MsgraphConfigError(msg)
    advanced_query = body.get("advanced_query", False)
    if not isinstance(advanced_query, bool):
        msg = f"Setting '{where}.advanced_query' must be true or false, got {advanced_query!r}."
        raise MsgraphConfigError(msg)
    member_of = body.get("member_of")
    if "member_of" in body:
        if not isinstance(member_of, str) or not member_of.strip():
            msg = f"Setting '{where}.member_of' must be a non-empty string (a group object ID), got {member_of!r}."
            raise MsgraphConfigError(msg)
        if resource != USERS:
            msg = f"Setting '{where}.member_of' is allowed only with resource 'users', got {resource!r}."
            raise MsgraphConfigError(msg)
    return Collection(
        name=name, resource=resource, filter=query_filter or None, advanced_query=advanced_query, member_of=member_of
    )


def build_credential(settings: MsgraphSettings) -> Any:
    """Return the async Azure credential for the `auth` mode. Constructing it does not contact Azure."""
    if settings.auth == AUTH_CLIENT_SECRET:
        if not (settings.tenant_id and settings.client_id and settings.client_secret):
            msg = f"auth 'client_secret' needs {ENV_TENANT_ID}, {ENV_CLIENT_ID} and {ENV_CLIENT_SECRET}."
            raise MsgraphConfigError(msg)
        return ClientSecretCredential(settings.tenant_id, settings.client_id, settings.client_secret)
    return DefaultAzureCredential()


class MsgraphClient:
    """Read-only access to the Microsoft Graph endpoints the adapter needs.

    Use it as an async context manager: leaving it closes the HTTP client and the credential.
    `requests` counts every request sent (retries included), `retries` the waits after HTTP 429,
    502, 503 or 504, and `member_requests` the requests sent to read group members.
    """

    def __init__(
        self,
        settings: MsgraphSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        credential: Any | None = None,
    ) -> None:
        self.settings = settings
        self.requests = 0
        self.retries = 0
        self.member_requests = 0
        self._credential = credential if credential is not None else build_credential(settings)
        middleware_options: dict[str, Any] = {
            **sdk_default_options,
            # The adapter runs its own bounded retry loop in `_send`; the SDK retry handler is off.
            RetryHandlerOption.get_key(): RetryHandlerOption(should_retry=False),
        }
        self._http: httpx.AsyncClient = GraphClientFactory.create_with_default_middleware(
            client=httpx.AsyncClient(transport=transport, timeout=settings.timeout),
            options=middleware_options,
        )
        auth_provider = AzureIdentityAuthenticationProvider(
            self._credential,
            scopes=[settings.token_scope],
            allowed_hosts=[settings.graph_host],
        )
        self._adapter = GraphRequestAdapter(auth_provider, client=self._http)
        self._adapter.base_url = settings.graph_url
        self.sdk = GraphServiceClient(request_adapter=self._adapter)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        try:
            await self._http.aclose()
        finally:
            close = getattr(self._credential, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result

    async def list_collection(self, collection: Collection, select: Sequence[str]) -> list[dict[str, Any]]:
        """Return every record of a collection, following `@odata.nextLink` until the last page."""
        builder = self.sdk.users if collection.resource == USERS else self.sdk.groups
        if collection.member_of:
            group = self.sdk.groups.by_group_id(collection.member_of)
            members = group.transitive_members if self.settings.membership == MEMBERSHIP_TRANSITIVE else group.members
            builder = members.graph_user
        eventual = collection.advanced_query or bool(collection.member_of and collection.filter)
        info = builder.to_get_request_information(
            _request_configuration(
                builder,
                select=list(select),
                top=self.settings.page_size,
                filter=collection.filter or None,
                count=True if eventual else None,
            )
        )
        if collection.member_of and info.url_template is not None:
            # Some SDK versions shorten the cast to `graph.user`; use Graph's fully qualified type name.
            info.url_template = info.url_template.replace("/graph.user", "/microsoft.graph.user")
        try:
            return await self._read_pages(builder, info, eventual=eventual, member=bool(collection.member_of))
        except MsgraphRequestError as exc:
            if collection.member_of and exc.status == 404:
                msg = (
                    f"Microsoft Graph collection '{collection.name}': group '{collection.member_of}' was not found. "
                    f"{exc} {NO_RECORDS_NOTE}"
                )
                raise MsgraphRequestError(msg, status=exc.status, code=exc.code, path=exc.path) from exc
            raise

    async def user_member_ids(self, group_id: str, *, transitive: bool) -> list[str]:
        """Return the sorted, unique IDs of the users that are direct or transitive members of a group."""
        group = self.sdk.groups.by_group_id(group_id)
        builder = group.transitive_members if transitive else group.members
        info = builder.to_get_request_information(
            _request_configuration(builder, select=["id"], top=self.settings.page_size)
        )
        members = await self._read_pages(builder, info, eventual=False, member=True)
        return sorted(
            {
                str(member["id"])
                for member in members
                if member.get("@odata.type") == USER_ODATA_TYPE and member.get("id") is not None
            }
        )

    async def _read_pages(
        self, builder: Any, info: RequestInformation, *, eventual: bool, member: bool = False
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        while True:
            if eventual:
                # `@odata.nextLink` keeps the query string but not this header; advanced queries need it on every page.
                info.headers.try_add("ConsistencyLevel", "eventual")
            page = await self._send(info, member=member)
            values = page.get("value")
            if not isinstance(values, list):
                path = _request_path(info)
                msg = f"Microsoft Graph returned a page without a 'value' list for {path}. {NO_RECORDS_NOTE}"
                raise MsgraphRequestError(msg, path=path)
            items.extend(values)
            next_link = page.get("@odata.nextLink")
            if not next_link:
                return items
            info = builder.with_url(next_link).to_get_request_information()

    async def _send(self, info: RequestInformation, *, member: bool = False) -> dict[str, Any]:
        """Send one GET request, retrying transient HTTP and transport failures up to `max_retries` times.

        `member` marks a request that reads group members: each attempt also counts in `member_requests`. The
        counters stay exact while member reads run concurrently because all reads run on one asyncio thread and
        no `await` separates the read and the write of a counter.

        The path for messages is read after the send attempt: the request adapter adds the base URL to the
        request information while it sends it.
        """
        attempt = 0
        while True:
            self.requests += 1
            if member:
                self.member_requests += 1
            try:
                body = await self._adapter.send_primitive_async(info, "bytes", ERROR_MAPPING)
            except APIError as exc:
                path = _request_path(info)
                status = exc.response_status_code
                if status in RETRY_STATUSES and attempt < self.settings.max_retries:
                    attempt += 1
                    wait = _retry_wait(exc.response_headers, attempt)
                    if wait > MAX_RETRY_WAIT_SECONDS:
                        msg = (
                            f"Microsoft Graph returned HTTP {status} for {path} and asked to wait {wait:g} seconds "
                            f"before the next request, more than the adapter waits ({MAX_RETRY_WAIT_SECONDS:g} "
                            "seconds). Run the sync again later."
                        )
                        raise MsgraphRequestError(msg, status=status, path=path) from exc
                    self.retries += 1
                    log.warning(
                        "graph request throttled, waiting before retry",
                        status=status,
                        path=path,
                        wait_seconds=wait,
                        retry=attempt,
                        max_retries=self.settings.max_retries,
                    )
                    await _sleep(wait)
                    continue
                raise self._api_error(exc, path, retried=attempt, member=member) from exc
            except (httpx.RequestError, httpx.InvalidURL) as exc:
                path = _request_path(info)
                if _retryable_transport_error(exc) and attempt < self.settings.max_retries:
                    attempt += 1
                    wait = _retry_wait(None, attempt)
                    self.retries += 1
                    log.warning(
                        "graph request failed, waiting before retry",
                        error=type(exc).__name__,
                        path=path,
                        wait_seconds=wait,
                        retry=attempt,
                        max_retries=self.settings.max_retries,
                    )
                    await _sleep(wait)
                    continue
                if isinstance(exc, httpx.TimeoutException):
                    msg = (
                        f"Microsoft Graph request {path} timed out after {self.settings.timeout:g} seconds "
                        f"(setting 'timeout') after {attempt} retries."
                    )
                else:
                    msg = f"Microsoft Graph request {path} failed: {type(exc).__name__} after {attempt} retries."
                raise MsgraphRequestError(msg, path=path) from exc
            except AzureError as exc:
                # Raised by the credential while it gets the token; Graph HTTP errors are `APIError`, handled above.
                raise self._authentication_error(exc, _request_path(info)) from exc
            return _parse_body(body, _request_path(info))

    def _api_error(self, exc: APIError, path: str, *, retried: int, member: bool = False) -> MsgraphRequestError:
        status = exc.response_status_code
        error = exc.error if isinstance(exc, ODataError) else None
        code = getattr(error, "code", None)
        detail = getattr(error, "message", None) or exc.message
        graph_says = f" ({code}: {detail})" if code or detail else ""
        if status in PERMISSION_STATUSES:
            msg = f"Microsoft Graph refused {path} with HTTP {status}{graph_says}. {self._permission_guidance()}"
            if member:
                msg += (
                    f" Reading the members of a group with hidden membership also needs {HIDDEN_MEMBERSHIP_PERMISSION}."
                )
            return MsgraphPermissionError(msg, status=status, code=code, path=path)
        if status in RETRY_STATUSES:
            msg = (
                f"Microsoft Graph returned HTTP {status} for {path} after {retried} retries{graph_says}. "
                "Run the sync again later, or raise the setting 'max_retries'."
            )
        else:
            msg = f"Microsoft Graph returned HTTP {status} for {path}{graph_says}."
        return MsgraphRequestError(msg, status=status, code=code, path=path)

    def _permission_guidance(self) -> str:
        """What HTTP 401 and 403 mean, and which permissions the identity of the `auth` mode needs."""
        user_read, group_read, group_member_read = REQUIRED_PERMISSIONS
        permissions = f"{user_read} and {group_read} (or {group_member_read} instead of {group_read})"
        if self.settings.auth == AUTH_CLIENT_SECRET:
            needs = (
                f"The application needs the Microsoft Graph application permissions {permissions}, with admin consent."
            )
        else:
            needs = (
                "The signed-in identity (user, managed identity or workload identity) needs read access to users "
                f"and groups, for example {permissions}."
            )
        return (
            "HTTP 401 means Graph rejected the access token: check the tenant, the credential, and that "
            f"'graph_url' matches the token audience. HTTP 403 means a permission is missing. {needs}"
        )

    def _authentication_error(self, exc: AzureError, path: str) -> MsgraphAuthenticationError:
        if self.settings.auth == AUTH_CLIENT_SECRET:
            check = f"Check the environment variables {ENV_TENANT_ID}, {ENV_CLIENT_ID} and {ENV_CLIENT_SECRET}."
        else:
            check = (
                "Check that an Azure CLI login (az login), a managed identity or a workload identity "
                "is available to DefaultAzureCredential."
            )
        if not isinstance(exc, ClientAuthenticationError):
            check = (
                f"The token endpoint could not be reached or refused the request ({type(exc).__name__}): check "
                f"the network connection to the Microsoft sign-in service. {check}"
            )
        msg = (
            f"Could not get an access token for Microsoft Graph (auth '{self.settings.auth}') "
            f"while requesting {path}. {check} Details: {exc.message}"
        )
        return MsgraphAuthenticationError(msg, path=path)


def _request_configuration(builder: Any, **query: Any) -> RequestConfiguration:
    """Request configuration with the builder's own query parameter class.

    Kiota nests a `<Builder>GetQueryParameters` class in every request builder class.
    """
    name = f"{type(builder).__name__}GetQueryParameters"
    params_cls = getattr(type(builder), name, None)
    if params_cls is None:
        msg = f"{type(builder).__name__} has no {name} class; the msgraph-sdk version is not supported."
        raise TypeError(msg)
    return RequestConfiguration(query_parameters=params_cls(**query))


def _request_path(info: RequestInformation) -> str:
    """Path and query of a request, for messages. URLs carry no credentials; headers are never included."""
    parts = urlsplit(info.url)
    path = unquote(parts.path)
    return f"{path}?{unquote(parts.query)}" if parts.query else path


def _retryable_transport_error(exc: Exception) -> bool:
    """Whether a transport failure can recover, excluding invalid requests and untrusted certificates."""
    if not isinstance(exc, httpx.TransportError) or isinstance(
        exc, (httpx.UnsupportedProtocol, httpx.LocalProtocolError)
    ):
        return False
    cause: BaseException | None = exc
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return False
        seen.add(id(cause))
        cause = cause.__cause__ or cause.__context__
    return True


def _retry_wait(headers: Mapping[str, str] | None, attempt: int) -> float:
    """Seconds to wait before retry number `attempt` (from 1).

    A valid `Retry-After` header is honored in full, as Microsoft Graph requires. Without one, the wait doubles at
    each retry from `FALLBACK_RETRY_SECONDS`, up to `MAX_BACKOFF_SECONDS`, with full jitter and a one-second floor.
    """
    for key, value in (headers or {}).items():
        if key.lower() == "retry-after":
            try:
                return max(0.0, float(value))
            except ValueError:
                break
    upper_bound = min(FALLBACK_RETRY_SECONDS * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
    return max(1.0, random.uniform(0, upper_bound))


def _parse_body(body: bytes | None, path: str) -> dict[str, Any]:
    """The JSON object of a Graph response body. Every request reads a list, so an empty body is an error."""
    if not body:
        msg = f"Microsoft Graph returned an empty body for {path}. {NO_RECORDS_NOTE}"
        raise MsgraphRequestError(msg, path=path)
    try:
        page = json.loads(body)
    except ValueError as exc:
        msg = f"Microsoft Graph returned a body that is not JSON for {path}."
        raise MsgraphRequestError(msg, path=path) from exc
    if not isinstance(page, dict):
        msg = f"Microsoft Graph returned a JSON {type(page).__name__} instead of an object for {path}."
        raise MsgraphRequestError(msg, path=path)
    return page


class MsgraphsyncModel(DiffSyncModelMixin, DiffSyncModel):
    """Base class of the models generated for the `msgraphsync` source.

    The adapter is read-only, so creating, updating or deleting through it is refused.
    """

    @classmethod
    def create(cls, adapter: Adapter, ids: dict[Any, Any], attrs: dict[Any, Any]) -> MsgraphsyncModel | None:
        msg = "The Microsoft Graph adapter is read-only: it cannot create objects in Microsoft Entra ID."
        raise NotImplementedError(msg)

    def update(self, attrs: dict[Any, Any]) -> MsgraphsyncModel | None:
        msg = "The Microsoft Graph adapter is read-only: it cannot update objects in Microsoft Entra ID."
        raise NotImplementedError(msg)

    def delete(self) -> MsgraphsyncModel | None:
        msg = "The Microsoft Graph adapter is read-only: it cannot delete objects in Microsoft Entra ID."
        raise NotImplementedError(msg)


@dataclass(frozen=True)
class CollectionPlan:
    """What one run reads for a collection.

    `select` is the `$select` list: `id`, the Graph properties that the `schema_mapping` entries read, then the
    extra properties of `settings.select`. `needs_members` is true when a groups entry maps `members` to a reference.
    """

    collection: Collection
    select: tuple[str, ...]
    path: str
    needs_members: bool = False


def build_request_plan(
    settings: MsgraphSettings,
    entries: Sequence[tuple[SchemaMappingModel, Collection]],
) -> dict[str, CollectionPlan]:
    """Return one `CollectionPlan` per collection that at least one `schema_mapping` entry reads.

    Entries that read the same collection share one read, so its `select` is the union of their properties, in
    the order the entries name them, without duplicates.
    """
    collections: dict[str, Collection] = {}
    selects: dict[str, list[str]] = {}
    members: dict[str, bool] = {}
    for entry, collection in entries:
        collections[collection.name] = collection
        _append_new(selects.setdefault(collection.name, ["id"]), _entry_properties(entry, collection))
        members[collection.name] = members.get(collection.name, False) or _maps_members(entry, collection)
    plan: dict[str, CollectionPlan] = {}
    for name, collection in collections.items():
        _append_new(selects[name], settings.select.get(collection.resource, ()))
        path = f"/{collection.resource}"
        if collection.member_of:
            relationship = "transitiveMembers" if settings.membership == MEMBERSHIP_TRANSITIVE else "members"
            path = f"/groups/{collection.member_of}/{relationship}/microsoft.graph.user"
        plan[name] = CollectionPlan(
            collection=collection, select=tuple(selects[name]), path=path, needs_members=members[name]
        )
    return plan


def _append_new(names: list[str], new: Sequence[str]) -> None:
    for name in new:
        if name not in names:
            names.append(name)


def _entry_properties(entry: SchemaMappingModel, collection: Collection) -> list[str]:
    """Top-level Graph properties that one entry reads through plain field mappings and filters.

    `members` on groups is not a Graph property: the adapter adds it to the group records. Filters run before the
    transforms, so every property a filter reads is selected. A field mapping that names a transform output reads
    the transformed key, which is not a Graph property, so it is not selected.
    """
    synthetic = {MEMBERS_KEY} if collection.resource == GROUPS else set()
    transform_outputs = {transform.field for transform in entry.transforms or []}
    field_paths = [field_map.mapping for field_map in entry.fields if field_map.mapping and not field_map.reference]
    filter_paths = [filter_map.field for filter_map in entry.filters or []]
    tops = [top for top in (path.split(".", 1)[0] for path in field_paths) if top not in transform_outputs]
    tops += [path.split(".", 1)[0] for path in filter_paths]
    return [top for top in tops if top not in synthetic]


def _maps_members(entry: SchemaMappingModel, collection: Collection) -> bool:
    return collection.resource == GROUPS and any(
        field_map.mapping == MEMBERS_KEY and field_map.reference for field_map in entry.fields
    )


def _check_members_reference(entry: SchemaMappingModel, collection: Collection) -> None:
    """Refuse a groups field that maps `members` without a `reference`: member IDs only make sense as references."""
    if collection.resource != GROUPS:
        return
    for field_map in entry.fields:
        if field_map.mapping == MEMBERS_KEY and not field_map.reference:
            msg = (
                f"schema_mapping '{entry.name}', field '{field_map.name}': 'mapping: {MEMBERS_KEY}' holds the IDs "
                "of the group's user members and needs a 'reference' to the kind that loads those users."
            )
            raise MsgraphConfigError(msg)


def _peer_ids(value: Any, index: Mapping[str, str]) -> tuple[list[str], int]:
    """Resolve Graph IDs to the unique IDs of loaded objects: sorted unique IDs found, and the count not found."""
    if value is None:
        graph_ids: list[Any] = []
    elif isinstance(value, (list, tuple, set)):
        graph_ids = list(value)
    else:
        graph_ids = [value]
    found: set[str] = set()
    missing = 0
    for graph_id in graph_ids:
        unique_id = index.get(str(graph_id))
        if unique_id is None:
            missing += 1
        else:
            found.add(unique_id)
    return sorted(found), missing


class MsgraphsyncAdapter(DiffSyncMixin, Adapter):
    """Load users and groups from Microsoft Entra ID. Source only: it sends GET requests to Graph and never writes.

    Each `schema_mapping` entry names a collection in `mapping` (`users`, `groups` or a name defined in
    `settings.collections`). On the first `model_loader` call, the adapter reads every planned collection once and
    keeps the records for the rest of the run.
    """

    type = "Msgraphsync"

    def __init__(
        self,
        target: str,
        adapter: SyncAdapter,
        config: SyncConfig,
        *args: Any,
        transport: httpx.AsyncBaseTransport | None = None,
        credential: Any | None = None,
        **kwargs: Any,
    ) -> None:
        if target != "source":
            msg = (
                f"The Microsoft Graph adapter can only be the source of a sync, but it is configured as the {target}. "
                "Move 'msgraphsync' to the 'source' block of the configuration."
            )
            raise MsgraphConfigError(msg)
        super().__init__(*args, **kwargs)
        self.target = target
        self.config = config
        self.settings = MsgraphSettings.from_settings(adapter.settings)
        # `transport` and `credential` let tests replace Microsoft Graph and Azure; both are None in a real run.
        self._transport = transport
        self._credential = credential
        self._entries: list[tuple[SchemaMappingModel, Collection]] = []
        for entry in config.schema_mapping:
            if entry.mapping:
                collection = self._resolve_entry_collection(entry.name, entry.mapping)
                _check_members_reference(entry, collection)
                self._entries.append((entry, collection))
        self.request_plan = build_request_plan(self.settings, self._entries)
        self._records_by_collection: dict[str, list[dict[str, Any]]] | None = None

    def _resolve_entry_collection(self, entry_name: str, collection_name: str) -> Collection:
        try:
            return self.settings.resolve_collection(collection_name)
        except MsgraphConfigError as exc:
            msg = f"schema_mapping '{entry_name}': {exc}"
            raise MsgraphConfigError(msg) from exc

    @staticmethod
    def _run(coro: Coroutine[Any, Any, T]) -> T:
        """Run a coroutine to completion from synchronous code.

        `asyncio.run` cannot start while an event loop runs in this thread (for example when the sync is started
        from a notebook or an async application), so the coroutine then runs in a new loop in a worker thread.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        def run_in_new_loop() -> T:
            return asyncio.run(coro)

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(run_in_new_loop).result()

    async def _read_all(self) -> dict[str, list[dict[str, Any]]]:
        """Read every planned collection with one Graph client and return the records by collection name."""
        records: dict[str, list[dict[str, Any]]] = {}
        async with MsgraphClient(self.settings, transport=self._transport, credential=self._credential) as graph:
            for name, plan in self.request_plan.items():
                requests_before, retries_before = graph.requests, graph.retries
                member_requests_before = graph.member_requests
                records[name] = await graph.list_collection(plan.collection, plan.select)
                if plan.needs_members:
                    await self._add_members(graph, records[name])
                log.info(
                    "collection read",
                    collection=name,
                    resource=plan.collection.resource,
                    path=plan.path,
                    records=len(records[name]),
                    requests=graph.requests - requests_before,
                    member_requests=graph.member_requests - member_requests_before,
                    retries=graph.retries - retries_before,
                )
            log.info(
                "graph reads finished",
                collections=len(records),
                member_requests=graph.member_requests,
                requests=graph.requests,
                retries=graph.retries,
            )
        return records

    async def _add_members(self, graph: MsgraphClient, groups: list[dict[str, Any]]) -> None:
        """Store the sorted user member IDs of every group under `members`.

        At most `MEMBER_CONCURRENCY` groups are read at once. `membership: transitive` reads the members of nested
        groups too. Groups without an `id` are left alone; the conversion to a model refuses them later with an
        `MsgraphConfigError` that names the entry. When one read fails, the other reads are cancelled and awaited
        before the error propagates, so none of them still uses the Graph client after it closes.
        """
        transitive = self.settings.membership == MEMBERSHIP_TRANSITIVE
        semaphore = asyncio.Semaphore(MEMBER_CONCURRENCY)
        with_id = [group for group in groups if group.get("id")]

        async def read(group: dict[str, Any]) -> list[str]:
            async with semaphore:
                return await graph.user_member_ids(str(group["id"]), transitive=transitive)

        tasks = [asyncio.create_task(read(group)) for group in with_id]
        try:
            member_ids = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for group, ids in zip(with_id, member_ids, strict=True):
            group[MEMBERS_KEY] = ids

    def _records(self) -> dict[str, list[dict[str, Any]]]:
        """The records of every planned collection, read from Graph on the first call of the run."""
        if self._records_by_collection is None:
            self._records_by_collection = self._run(self._read_all())
        return self._records_by_collection

    def model_loader(self, model_name: str, model: type[MsgraphsyncModel]) -> None:
        """Load the objects of one kind from the records of the collection its `schema_mapping` entry reads."""
        for entry, collection in self._entries:
            if entry.name != model_name:
                continue
            source = self._records()[collection.name]
            # `transform_records` writes into the record dicts. Copies keep entries that share a collection apart.
            records = model.filter_records(records=[dict(record) for record in source], schema_mapping=entry)
            records = model.transform_records(records=records, schema_mapping=entry)
            peers = self._peer_indexes(entry)
            dropped: Counter[str] = Counter()
            for record in records:
                self.add(model(**self.record_to_diffsync(record, entry, model, peers=peers, dropped=dropped)))
            log.info("kind loaded", kind=model_name, collection=collection.name, loaded=len(records), read=len(source))
            for field_name, count in sorted(dropped.items()):
                log.warning(
                    "references to objects that are not loaded were dropped",
                    kind=model_name,
                    field=field_name,
                    dropped=count,
                )

    def _peer_indexes(self, entry: SchemaMappingModel) -> dict[str, dict[str, str]]:
        """Graph ID to unique ID of the loaded objects of every kind that the entry references.

        Built once per `model_loader` call, so resolving references does not scan the store for every record.
        """
        indexes: dict[str, dict[str, str]] = {}
        for field_map in entry.fields:
            reference = field_map.reference
            if not field_map.mapping or not reference or reference in indexes:
                continue
            index: dict[str, str] = {}
            for obj in self.store.get_all(model=reference):
                local_id = getattr(obj, "local_id", None)
                if local_id is not None:
                    index[str(local_id)] = obj.get_unique_id()
            indexes[reference] = index
        return indexes

    def record_to_diffsync(
        self,
        record: dict[str, Any],
        mapping: SchemaMappingModel,
        model: type[MsgraphsyncModel],
        *,
        peers: Mapping[str, Mapping[str, str]],
        dropped: Counter[str],
    ) -> dict[str, Any]:
        """Turn one Graph record into the fields of a DiffSync model.

        `peers` maps each referenced kind to its Graph ID index, built by `_peer_indexes`. References to objects
        that are not loaded are left out and counted per field in `dropped`.
        """
        local_id = record.get("id")
        if not local_id:
            msg = (
                f"schema_mapping '{mapping.name}': a Microsoft Graph record has no 'id'. "
                "Check that no transform of this entry sets 'id'."
            )
            raise MsgraphConfigError(msg)
        data: dict[str, Any] = {"local_id": str(local_id)}
        for field_map in mapping.fields:
            if field_map.static is not None:
                data[field_map.name] = field_map.static
            elif field_map.mapping and field_map.reference:
                found, missing = _peer_ids(get_value(record, field_map.mapping), peers.get(field_map.reference, {}))
                if missing:
                    dropped[field_map.name] += missing
                if model.is_list(name=field_map.name):
                    data[field_map.name] = found
                elif len(found) > 1:
                    msg = (
                        f"schema_mapping '{mapping.name}', field '{field_map.name}': the Graph property "
                        f"'{field_map.mapping}' resolves to {len(found)} loaded '{field_map.reference}' objects, "
                        "but the relationship holds one peer. Map a property that holds one ID, or use a "
                        "relationship that holds many peers."
                    )
                    raise MsgraphConfigError(msg)
                elif found:
                    data[field_map.name] = found[0]
            elif field_map.mapping:
                value = get_value(record, field_map.mapping)
                if isinstance(value, list) and not model.is_list(name=field_map.name):
                    msg = (
                        f"schema_mapping '{mapping.name}', field '{field_map.name}': the Graph property "
                        f"'{field_map.mapping}' is a list, but the attribute holds one value. "
                        "Turn the list into one value with a transform of this entry and map the transformed key."
                    )
                    raise MsgraphConfigError(msg)
                if value is not None:
                    data[field_map.name] = value
        return data
