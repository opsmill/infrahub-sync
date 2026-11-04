"""Shared helpers for the Entra ID adapter tests: a mocked Microsoft Graph, a fake credential and test models.

pytest does not collect this module (its name does not start with `test_`). The test modules call
`pytest.importorskip("msgraph")` before they import it; this module does the same, so it never
fails at import time where the optional SDK is missing.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from infrahub_sync import SchemaMappingModel, SyncAdapter, SyncConfig

pytest.importorskip("msgraph")

from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError

from infrahub_sync.adapters import entraid
from infrahub_sync.adapters.entraid import EntraidAdapter, EntraidModel

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

GRAPH_URL = "https://graph.microsoft.com/v1.0"
TOKEN = "fake-token"  # noqa: S105 - fake value, tests check that it never appears in messages
CLIENT_SECRET = "fake-client-secret"  # noqa: S105 - fake value
ENTRAID_ENV = {
    entraid.ENV_TENANT_ID: "00000000-0000-0000-0000-000000000000",
    entraid.ENV_CLIENT_ID: "11111111-1111-1111-1111-111111111111",
    entraid.ENV_CLIENT_SECRET: CLIENT_SECRET,
}
ONE_YEAR_SECONDS = 365 * 24 * 3600


def graph_id(number: int) -> str:
    """A GUID-like Graph object ID made of zeros and the number, for example `00000000-0000-0000-0000-000000000007`."""
    return f"00000000-0000-0000-0000-{number:012d}"


def user_record(number: int, upn: str, **properties: object) -> dict[str, Any]:
    """A Graph user record in the wire format. Use `example.com` user principal names."""
    return {
        "@odata.type": entraid.USER_ODATA_TYPE,
        "id": graph_id(number),
        "userPrincipalName": upn,
        "displayName": upn.split("@", 1)[0],
        **properties,
    }


def group_record(number: int, display_name: str, **properties: object) -> dict[str, Any]:
    """A Graph group record in the wire format."""
    return {"id": graph_id(number), "displayName": display_name, **properties}


def member_record(member_id: str, odata_type: str = entraid.USER_ODATA_TYPE) -> dict[str, Any]:
    """An item of a `/members` or `/transitiveMembers` page."""
    return {"@odata.type": odata_type, "id": member_id}


def set_entraid_env(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> None:
    """Set the three `ENTRAID_*` variables to fake values; an override of None removes the variable."""
    values: dict[str, str | None] = {**ENTRAID_ENV, **overrides}
    for name, value in values.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


class FakeCredential:
    """Async token credential that returns a fake token.

    With `fail` set it raises `ClientAuthenticationError`; with `error` set it raises that exception instead, for
    example an `azure.core.exceptions.ServiceRequestError` for a token endpoint that cannot be reached.
    """

    def __init__(
        self,
        *,
        fail: bool = False,
        message: str = "fake credential refused the request",
        error: Exception | None = None,
    ) -> None:
        self.fail = fail
        self.message = message
        self.error = error
        self.scopes: list[tuple[str, ...]] = []
        self.closed = False

    async def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:  # noqa: ARG002 - credential protocol
        self.scopes.append(scopes)
        if self.error is not None:
            raise self.error
        if self.fail:
            raise ClientAuthenticationError(message=self.message)
        return AccessToken(TOKEN, int(time.time()) + ONE_YEAR_SECONDS)

    async def close(self) -> None:
        self.closed = True


class SleepRecorder:
    """Replacement for `entraid._sleep` that records each wait instead of sleeping.

    Use it with `monkeypatch.setattr(entraid, "_sleep", recorder)`.
    """

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


class GraphRouter:
    """A mocked Microsoft Graph served through `httpx.MockTransport`.

    Register the pages of a path (relative to `GRAPH_URL`, for example `/users` or
    `/groups/<id>/members`) with `add_pages`. Every page except the last carries an
    `@odata.nextLink` that keeps the query string of the request and adds `$skiptoken=<page>`.
    Queued errors and timeouts of a path are answered first, in order. A path without
    registered pages answers one empty page. Every `httpx.Request` is kept in `requests`.
    """

    def __init__(self, base_url: str = GRAPH_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.base_path = httpx.URL(self.base_url).path.rstrip("/")
        self.requests: list[httpx.Request] = []
        self._pages: dict[str, list[list[dict[str, Any]]]] = {}
        self._queued: dict[str, list[Callable[[httpx.Request], httpx.Response]]] = {}

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def add_pages(self, path: str, pages: Sequence[Sequence[dict[str, Any]]]) -> None:
        """Serve these pages, in order, for `path`."""
        self._pages[path] = [list(page) for page in pages]

    def add_records(self, path: str, records: Sequence[dict[str, Any]]) -> None:
        """Serve these records as one page for `path`."""
        self.add_pages(path, [records])

    def add_members(self, group_id: str, members: Iterable[dict[str, Any]], *, transitive: bool = False) -> None:
        """Serve one page of member items for a group's `/members` or `/transitiveMembers` path."""
        self.add_records(self.members_path(group_id, transitive=transitive), list(members))

    def add_error(
        self,
        path: str,
        status: int,
        *,
        headers: Mapping[str, str] | None = None,
        code: str = "FakeGraphError",
        message: str = "Fake Graph error.",
    ) -> None:
        """Queue one error response with an OData error body for `path`."""
        body = {"error": {"code": code, "message": message}}

        def respond(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 - same signature as the other queued items
            return httpx.Response(status, headers=dict(headers or {}), json=body)

        self._queued.setdefault(path, []).append(respond)

    def add_timeout(self, path: str) -> None:
        """Queue one `httpx.ReadTimeout` for `path`."""

        def respond(request: httpx.Request) -> httpx.Response:
            msg = "fake read timeout"
            raise httpx.ReadTimeout(msg, request=request)

        self._queued.setdefault(path, []).append(respond)

    def add_body(self, path: str, content: bytes, *, status: int = 200) -> None:
        """Queue one response with this raw body for `path`, for example `b""` or `b"{}"`."""

        def respond(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 - same signature as the other queued items
            return httpx.Response(status, headers={"Content-Type": "application/json"}, content=content)

        self._queued.setdefault(path, []).append(respond)

    def add_request_error(self, path: str, error_cls: type[httpx.RequestError]) -> None:
        """Queue one `httpx.RequestError` subclass, for example `httpx.DecodingError`, for `path`."""

        def respond(request: httpx.Request) -> httpx.Response:
            msg = f"fake {error_cls.__name__}"
            raise error_cls(msg, request=request)

        self._queued.setdefault(path, []).append(respond)

    @staticmethod
    def members_path(group_id: str, *, transitive: bool = False) -> str:
        return f"/groups/{group_id}/{'transitiveMembers' if transitive else 'members'}"

    def relative_path(self, request: httpx.Request) -> str:
        path = request.url.path
        return path.removeprefix(self.base_path)

    def requests_for(self, path: str) -> list[httpx.Request]:
        """The requests sent to `path`, in order."""
        return [request for request in self.requests if self.relative_path(request) == path]

    def paths(self) -> list[str]:
        """The relative path of every request, in order."""
        return [self.relative_path(request) for request in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = self.relative_path(request)
        queued = self._queued.get(path)
        if queued:
            return queued.pop(0)(request)
        pages = self._pages.get(path, [[]])
        token = request.url.params.get("$skiptoken")
        index = int(token) if token else 0
        if index >= len(pages):
            return httpx.Response(400, json={"error": {"code": "BadSkipToken", "message": f"No page {index}."}})
        body: dict[str, Any] = {"value": pages[index]}
        if index + 1 < len(pages):
            body["@odata.nextLink"] = str(request.url.copy_set_param("$skiptoken", str(index + 1)))
        return httpx.Response(200, json=body)


class IdentityEmployee(EntraidModel):
    _modelname = "IdentityEmployee"
    _identifiers = ("entra_id",)
    _attributes = ("name", "display_name", "email", "enabled")
    entra_id: str
    name: str | None = None
    display_name: str | None = None
    email: str | None = None
    enabled: bool | None = None

    local_id: str | None = None
    local_data: Any | None = None


class IdentityServiceAccount(EntraidModel):
    _modelname = "IdentityServiceAccount"
    _identifiers = ("entra_id",)
    _attributes = ("name", "display_name", "email", "enabled")
    entra_id: str
    name: str | None = None
    display_name: str | None = None
    email: str | None = None
    enabled: bool | None = None

    local_id: str | None = None
    local_data: Any | None = None


class IdentityGroup(EntraidModel):
    _modelname = "IdentityGroup"
    _identifiers = ("entra_id",)
    _attributes = ("name", "description", "members")
    entra_id: str
    name: str | None = None
    description: str | None = None
    members: list[str] = []  # noqa: RUF012 - pydantic copies field defaults

    local_id: str | None = None
    local_data: Any | None = None


TEST_MODELS: dict[str, type[EntraidModel]] = {
    "IdentityEmployee": IdentityEmployee,
    "IdentityServiceAccount": IdentityServiceAccount,
    "IdentityGroup": IdentityGroup,
}


def make_config(
    schema_mapping: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any] | None = None,
    order: Sequence[str] | None = None,
) -> SyncConfig:
    """A sync configuration from an `entraid` source to an `infrahub` destination."""
    return SyncConfig(
        name="from-entraid-test",
        source=SyncAdapter(name="entraid", settings=dict(settings or {})),
        destination=SyncAdapter(name="infrahub", settings={"url": "http://localhost:8000"}),
        order=list(order or []),
        schema_mapping=[SchemaMappingModel(**entry) for entry in schema_mapping],
    )


def make_adapter(
    config: SyncConfig,
    router: GraphRouter,
    *,
    credential: FakeCredential | None = None,
    models: Mapping[str, type[EntraidModel]] | None = None,
    target: str = "source",
) -> EntraidAdapter:
    """Build a subclass of `EntraidAdapter` with the test models and construct it against `router`.

    `top_level` follows `config.compute_order()`, so kinds load in reference order.
    """
    model_classes = dict(models or TEST_MODELS)
    names = [entry.name for entry in config.schema_mapping]
    top_level = [name for name in config.compute_order() if name in names]
    attributes: dict[str, Any] = {"top_level": top_level}
    attributes.update({name: model_classes[name] for name in top_level})
    adapter_cls = type("FakeEntraidAdapter", (EntraidAdapter,), attributes)
    return adapter_cls(
        target=target,
        adapter=config.source,
        config=config,
        transport=router.transport,
        credential=credential or FakeCredential(),
    )
