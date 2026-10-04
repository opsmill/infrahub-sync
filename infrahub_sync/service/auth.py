"""Principal resolution for the Sync API.

A caller is identified and authorized by Infrahub: the API forwards the caller's
Infrahub API token to Infrahub, reads the account and its permissions, and admits a
request from Infrahub's object permissions on Sync's kinds. Sync keeps no accounts,
tokens, or roles of its own.

`EnvironmentPrincipalResolver` remains for development stacks and tests that run
without an Infrahub; its principals carry no permission set and are not restricted.
"""

from __future__ import annotations

import hmac
import json
import os
from typing import TYPE_CHECKING, Literal, Protocol

from infrahub_sdk.exceptions import AuthenticationError, Error
from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from infrahub_sdk import InfrahubClient

PRINCIPALS_ENV = "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"


Action = Literal["view", "create", "update", "delete"]
SYNC_NAMESPACE = "Sync"


class Principal(BaseModel):
    """Authenticated Sync API actor.

    `permissions` holds the caller's Infrahub permission identifiers. `None` means the
    principal came from a resolver without Infrahub, which does not restrict it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: str = Field(min_length=1)
    administrator: bool = False
    permissions: frozenset[str] | None = None

    def allows(self, action: Action, name: str, *, default_branch: bool = True) -> bool:
        """Whether Infrahub lets this principal take `action` on `Sync<name>`.

        On the default branch unless `default_branch` is false, which asks about any
        other branch.
        """
        if self.permissions is None or self.administrator:
            return True
        return permits(self.permissions, action, SYNC_NAMESPACE, name, default_branch=default_branch)


# Infrahub's permission decision flags (`infrahub.permissions.constants.PermissionDecisionFlag`).
_DENY = 1
_ALLOW_DEFAULT = 2
_ALLOW_OTHER = 4
_ALLOW_ALL = _ALLOW_DEFAULT | _ALLOW_OTHER
_DECISIONS = {"deny": _DENY, "allow_default": _ALLOW_DEFAULT, "allow_other": _ALLOW_OTHER, "allow_all": _ALLOW_ALL}


def _covering(identifier: str, action: Action, namespace: str, name: str) -> tuple[int, int] | None:
    """The decision and specificity of one object permission when it covers the request."""
    parts = identifier.split(":")
    if len(parts) != 5 or parts[0] != "object":
        return None
    _scope, ns, kind_name, granted, decision_name = parts
    decision = _DECISIONS.get(decision_name)
    if decision is None or ns not in {"*", namespace} or kind_name not in {"*", name}:
        return None
    if granted not in {"any", action}:
        return None
    # A deny outranks an allow of the same shape: Infrahub adds one when no allow flag is set.
    specificity = (ns != "*") + (kind_name != "*") + (granted != "any") + ((decision & _ALLOW_ALL) == 0)
    return decision, specificity


def permits(
    identifiers: Iterable[str], action: Action, namespace: str, name: str, *, default_branch: bool = True
) -> bool:
    """Decide one request from Infrahub permission identifiers, as Infrahub's resolver does.

    The most specific covering permission decides; at equal specificity the allows add
    up and a deny adds nothing. A `deny` outranks an allow of the same shape, as in
    `PermissionResolver.report_object_permission`. No covering permission refuses.
    """
    highest = -1
    combined = _DENY
    for identifier in sorted(identifiers):
        covering = _covering(identifier, action, namespace, name)
        if covering is None:
            continue
        decision, specificity = covering
        if specificity > highest:
            combined, highest = decision, specificity
        elif specificity == highest and decision != _DENY:
            combined |= decision
    required = _ALLOW_DEFAULT if default_branch else _ALLOW_OTHER
    return combined & required == required


class PrincipalResolver(Protocol):
    """Narrow authentication provider replaced without changing HTTP routes."""

    @property
    def secret_values(self) -> tuple[str, ...]: ...

    def resolve(self, token: str) -> Principal | Awaitable[Principal | None] | None: ...


class _ConfiguredPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str = Field(min_length=16)
    administrator: bool = False


class EnvironmentPrincipalResolver:
    """Resolve bearer tokens from one environment-configured JSON mapping."""

    def __init__(self, entries: tuple[tuple[str, _ConfiguredPrincipal], ...]) -> None:
        self._entries = entries

    @classmethod
    def from_environment(cls) -> EnvironmentPrincipalResolver:
        """Load ``{actor: {token, administrator}}`` without retaining raw JSON."""
        raw = os.environ.get(PRINCIPALS_ENV)
        if not raw:
            msg = f"{PRINCIPALS_ENV} must contain a JSON object of Sync API principals"
            raise ValueError(msg)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            msg = f"{PRINCIPALS_ENV} must contain valid JSON"
            raise ValueError(msg) from None
        if not isinstance(payload, dict) or not payload:
            msg = f"{PRINCIPALS_ENV} must contain a non-empty JSON object"
            raise ValueError(msg)
        try:
            entries = tuple(
                (str(actor).strip(), _ConfiguredPrincipal.model_validate(value)) for actor, value in payload.items()
            )
        except ValidationError:
            msg = f"{PRINCIPALS_ENV} contains an invalid principal definition"
            raise ValueError(msg) from None
        if any(not actor for actor, _ in entries):
            msg = f"{PRINCIPALS_ENV} contains an empty actor name"
            raise ValueError(msg)
        tokens = [entry.token for _, entry in entries]
        if len(tokens) != len(set(tokens)):
            msg = f"{PRINCIPALS_ENV} assigns one bearer token to multiple actors"
            raise ValueError(msg)
        return cls(entries)

    @property
    def secret_values(self) -> tuple[str, ...]:
        """Return token values for boundary redaction, never persistence."""
        return tuple(entry.token for _, entry in self._entries)

    def resolve(self, token: str) -> Principal | None:
        """Compare every configured token with a timing-safe operation."""
        matched: Principal | None = None
        for actor, entry in self._entries:
            if hmac.compare_digest(token.encode(), entry.token.encode()):
                matched = Principal(actor=actor, administrator=entry.administrator)
        return matched


SUPER_ADMIN_PERMISSION = "global:super_admin:allow_all"
SUPER_ADMIN_DENIED = "global:super_admin:deny"


class PrincipalUnavailableError(RuntimeError):
    """Infrahub could not be asked who a caller is; the request cannot be authenticated."""


class InfrahubPrincipalResolver:
    """Resolve a caller's Infrahub API token through Infrahub itself."""

    def __init__(self, client_for: Callable[[str], InfrahubClient], *, service_secrets: tuple[str, ...] = ()) -> None:
        self._client_for = client_for
        self._service_secrets = service_secrets

    @property
    def secret_values(self) -> tuple[str, ...]:
        """The service account's own credential, for redaction; caller tokens are never held."""
        return self._service_secrets

    async def resolve(self, token: str) -> Principal | None:
        """Return the caller's principal, `None` for a token Infrahub refuses."""
        client = self._client_for(token)
        try:
            profile = await client.get_user()
        except AuthenticationError:
            return None
        except Error:
            raise PrincipalUnavailableError from None
        account = (profile or {}).get("AccountProfile") or {}
        # The account's label, or its id when it has none: Infrahub's profile query
        # returns no unique account name.
        actor = str(account.get("display_label") or account.get("id") or "").strip()
        status = ((account.get("status") or {}).get("value") or "active").lower()
        if not actor or status != "active":
            return None
        identifiers = frozenset(_permission_identifiers(account))
        return Principal(
            actor=actor,
            # A global deny wins over the grant, as in Infrahub's own resolver.
            administrator=SUPER_ADMIN_PERMISSION in identifiers and SUPER_ADMIN_DENIED not in identifiers,
            permissions=identifiers,
        )


def _permission_identifiers(account: dict) -> list[str]:
    """Flatten the permission identifiers of every group the account belongs to."""
    identifiers: list[str] = []
    for group in (account.get("member_of_groups") or {}).get("edges") or []:
        for role in ((group.get("node") or {}).get("roles") or {}).get("edges") or []:
            for permission in ((role.get("node") or {}).get("permissions") or {}).get("edges") or []:
                value = ((permission.get("node") or {}).get("identifier") or {}).get("value")
                if isinstance(value, str):
                    identifiers.append(value)
    return identifiers
