"""Sync API callers are identified and authorized by Infrahub."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from fastapi.testclient import TestClient
from infrahub_sdk.exceptions import AuthenticationError, ServerNotReachableError

from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import (
    SUPER_ADMIN_PERMISSION,
    InfrahubPrincipalResolver,
    Principal,
    PrincipalUnavailableError,
    permits,
)
from infrahub_sync.service.service import RunService
from tests.service.test_http_api import _FakeOrchestration, _registered_package

if TYPE_CHECKING:
    from pathlib import Path

PLANNER = frozenset({"object:Sync:Run:create:allow_all", "object:Sync:Run:view:allow_all"})
APPROVER = PLANNER | {"object:Sync:Approval:create:allow_default"}


@pytest.mark.parametrize(
    ("identifiers", "action", "name", "expected"),
    [
        ({"object:Sync:Run:create:allow_all"}, "create", "Run", True),
        ({"object:Sync:Run:create:allow_default"}, "create", "Run", True),
        ({"object:Sync:Run:create:allow_other"}, "create", "Run", False),
        ({"object:Sync:*:any:allow_all"}, "create", "Approval", True),
        ({"object:*:*:any:allow_all"}, "view", "Run", True),
        ({"object:Sync:Run:view:allow_all"}, "create", "Run", False),
        ({"object:Ipam:Run:create:allow_all"}, "create", "Run", False),
        ({"object:*:*:any:allow_all", "object:Sync:Approval:create:deny"}, "create", "Approval", False),
        ({"global:edit_default_branch:allow_all"}, "create", "Run", False),
        ({"object:Sync:Run"}, "create", "Run", False),
        (set(), "view", "Run", False),
    ],
)
def test_permits_fails_closed(identifiers: set[str], action: Any, name: str, expected: bool) -> None:  # noqa: ANN401, FBT001
    assert permits(identifiers, action, "Sync", name) is expected


def test_a_principal_without_a_permission_set_is_unrestricted() -> None:
    """The development resolver's principals: no Infrahub, no restriction."""
    assert Principal(actor="dev").allows("create", "Approval")


def test_a_super_administrator_is_allowed_everything() -> None:
    principal = Principal(actor="admin", administrator=True, permissions=frozenset())

    assert principal.allows("create", "Approval")


def _profile(*, name: str = "alice", status: str = "active", identifiers: frozenset[str] = PLANNER) -> dict:
    permissions = [{"node": {"identifier": {"value": identifier}}} for identifier in sorted(identifiers)]
    return {
        "AccountProfile": {
            "display_label": name,
            "status": {"value": status},
            "member_of_groups": {
                "edges": [{"node": {"roles": {"edges": [{"node": {"permissions": {"edges": permissions}}}]}}}]
            },
        }
    }


class _Client:
    def __init__(self, answer: object) -> None:
        self.answer = answer

    async def get_user(self) -> dict:
        if isinstance(self.answer, BaseException):
            raise self.answer
        return cast("dict", self.answer)


def _resolver(answer: object, tokens: list[str] | None = None) -> InfrahubPrincipalResolver:
    def client_for(token: str) -> Any:  # noqa: ANN401 - a stand-in SDK client
        if tokens is not None:
            tokens.append(token)
        return _Client(answer)

    return InfrahubPrincipalResolver(client_for)


async def test_the_resolver_asks_infrahub_with_the_callers_own_token() -> None:
    tokens: list[str] = []

    principal = await _resolver(_profile(identifiers=APPROVER), tokens).resolve("caller-token")

    assert tokens == ["caller-token"]
    assert principal is not None
    assert principal.actor == "alice"
    assert principal.permissions == APPROVER
    assert principal.allows("create", "Approval")


async def test_a_token_infrahub_refuses_resolves_to_no_principal() -> None:
    assert await _resolver(AuthenticationError("401")).resolve("bad") is None


async def test_an_inactive_account_resolves_to_no_principal() -> None:
    assert await _resolver(_profile(status="inactive")).resolve("caller-token") is None


async def test_a_super_administrator_is_recognised() -> None:
    principal = await _resolver(_profile(identifiers=frozenset({SUPER_ADMIN_PERMISSION}))).resolve("t")

    assert principal is not None
    assert principal.administrator


async def test_an_unreachable_infrahub_is_unavailable_not_unauthenticated() -> None:
    with pytest.raises(PrincipalUnavailableError):
        await _resolver(ServerNotReachableError("http://infrahub:8000")).resolve("caller-token")


def test_the_resolver_holds_no_caller_credential() -> None:
    assert _resolver(_profile()).secret_values == ()


class _MappedResolver:
    """Resolves fixed tokens to principals with the given Infrahub permissions."""

    def __init__(self, principals: dict[str, Principal]) -> None:
        self._principals = principals

    @property
    def secret_values(self) -> tuple[str, ...]:
        return ()

    async def resolve(self, token: str) -> Principal | None:
        return self._principals.get(token)


@pytest.fixture
def api(tmp_path: Path) -> tuple[TestClient, Any]:
    projection = local_product_projection(tmp_path.resolve())
    version = projection.create_configuration(_registered_package())
    service = RunService(projection, _FakeOrchestration(), secrets=())
    resolver = _MappedResolver(
        {
            "planner-token-0123456789": Principal(actor="planner", permissions=PLANNER),
            "approver-token-0123456789": Principal(actor="approver", permissions=APPROVER),
            "reader-token-0123456789": Principal(
                actor="reader", permissions=frozenset({"object:Sync:Run:view:allow_all"})
            ),
        }
    )
    return TestClient(create_app(service, resolver)), version


def _run(client: TestClient, version: Any, headers: dict[str, str], operation: str = "plan") -> Any:  # noqa: ANN401
    body: dict[str, object] = {
        "operation": operation,
        "config_id": version.config_id,
        "registry_version": version.registry_version,
        "reason": "infrahub permissions",
    }
    if operation == "sync":
        body["confirm_writes"] = True
    return client.post("/runs", headers={**headers, "Idempotency-Key": f"key-{operation}-{sorted(headers)}"}, json=body)


def test_an_infrahub_api_token_is_accepted_as_x_infrahub_key(api: tuple[TestClient, Any]) -> None:
    client, version = api

    response = _run(client, version, {"X-INFRAHUB-KEY": "planner-token-0123456789"})

    assert response.status_code == 202, response.text


def test_a_bearer_infrahub_token_is_accepted_too(api: tuple[TestClient, Any]) -> None:
    client, version = api

    response = _run(client, version, {"Authorization": "Bearer planner-token-0123456789"})

    assert response.status_code == 202, response.text


def test_a_reader_cannot_plan_and_is_told_which_permission_is_missing(api: tuple[TestClient, Any]) -> None:
    client, version = api

    response = _run(client, version, {"X-INFRAHUB-KEY": "reader-token-0123456789"})

    assert response.status_code == 403
    assert "object:Sync:Run:create" in response.json()["error"]["message"]


def test_a_planner_cannot_sync_without_the_approval_permission(api: tuple[TestClient, Any]) -> None:
    client, version = api

    refused = _run(client, version, {"X-INFRAHUB-KEY": "planner-token-0123456789"}, operation="sync")
    allowed = _run(client, version, {"X-INFRAHUB-KEY": "approver-token-0123456789"}, operation="sync")

    assert refused.status_code == 403
    assert "object:Sync:Approval:create" in refused.json()["error"]["message"]
    assert allowed.status_code == 202, allowed.text


def test_a_planner_cannot_apply(api: tuple[TestClient, Any]) -> None:
    client, _version = api

    response = client.post(
        "/runs/run-any/apply",
        headers={"X-INFRAHUB-KEY": "planner-token-0123456789", "Idempotency-Key": "apply"},
        json={"expected_checksum": "a" * 64, "confirm_writes": True, "reason": "apply"},
    )

    assert response.status_code == 403
    assert "object:Sync:Approval:create" in response.json()["error"]["message"]


def test_an_unknown_token_is_unauthenticated(api: tuple[TestClient, Any]) -> None:
    client, version = api

    assert _run(client, version, {"X-INFRAHUB-KEY": "unknown-token-0123456789"}).status_code == 401


def test_an_unreachable_infrahub_is_a_503_not_a_401(tmp_path: Path) -> None:
    projection = local_product_projection(tmp_path.resolve())
    service = RunService(projection, _FakeOrchestration(), secrets=())
    resolver = _resolver(ServerNotReachableError("http://infrahub:8000"))
    client = TestClient(create_app(service, resolver))

    response = client.get("/runs/run-any", headers={"X-INFRAHUB-KEY": "caller-token-0123456789"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "identity-unavailable"


def test_an_approver_may_act_on_a_run_another_account_planned(api: tuple[TestClient, Any]) -> None:
    """Infrahub's permission is the right to act on a run, not having started it."""
    client, version = api
    planned = _run(client, version, {"X-INFRAHUB-KEY": "planner-token-0123456789"})
    run_id = planned.json()["run"]["run_id"]

    applied = client.post(
        f"/runs/{run_id}/apply",
        headers={"X-INFRAHUB-KEY": "approver-token-0123456789", "Idempotency-Key": "apply-other"},
        json={
            "expected_checksum": "a" * 64,
            "confirm_writes": True,
            "reason": "an approver applies the planner's plan",
        },
    )

    # The run has no plan yet, so the apply is refused, but not for whose run it is.
    assert applied.status_code != 403, applied.text
    assert "initiating actor" not in applied.text


@pytest.mark.parametrize(
    ("identifiers", "default_branch", "expected"),
    [
        # The most specific permission decides, as Infrahub's resolver does.
        ({"object:*:*:any:allow_default", "object:Sync:Run:create:allow_other"}, True, False),
        ({"object:*:*:any:allow_default", "object:Sync:Run:create:allow_other"}, False, True),
        ({"object:*:*:any:deny", "object:Sync:Run:create:allow_default"}, True, True),
        # A deny outranks an allow of the same shape.
        ({"object:Sync:Run:create:allow_all", "object:Sync:Run:create:deny"}, True, False),
        # Equal specificity: the allows add up.
        ({"object:Sync:Run:create:allow_default", "object:Sync:Run:create:allow_other"}, False, True),
        ({"object:Sync:Run:create:allow_default"}, False, False),
        ({"object:Sync:Run:create:allow_all"}, False, True),
    ],
)
def test_permits_follows_infrahubs_precedence(identifiers: set[str], default_branch: bool, expected: bool) -> None:  # noqa: FBT001
    assert permits(identifiers, "create", "Sync", "Run", default_branch=default_branch) is expected


async def test_a_denied_super_administrator_is_not_an_administrator() -> None:
    identifiers = frozenset({SUPER_ADMIN_PERMISSION, "global:super_admin:deny"})

    principal = await _resolver(_profile(identifiers=identifiers)).resolve("caller-token")

    assert principal is not None
    assert not principal.administrator
    assert not principal.allows("create", "Run")


async def test_an_account_without_a_label_is_identified_by_its_id() -> None:
    profile = _profile()
    profile["AccountProfile"]["display_label"] = None
    profile["AccountProfile"]["id"] = "18a6c-account-id"

    principal = await _resolver(profile).resolve("caller-token")

    assert principal is not None
    assert principal.actor == "18a6c-account-id"
