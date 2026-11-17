"""Application-owned principal resolution for the Sync API.

A request presents one value: an API token or a session token. ``StoreAccountResolver`` reads its
SHA-256 digest in the product store, with the account that owns it, for every request. Nothing is
cached, so the next request of a disabled account is refused.

``apply_admin_account`` makes the ``admin`` account match the variables of the environment when
the service starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .credentials import digest, hash_password, verify_password

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync.product_store import Account, ProductProjection

    from .settings import AdminAccountSettings

ADMIN_ACCOUNT_NAME = "admin"
# The name of the one API token of ``admin`` that the environment defines.
ADMIN_ACCOUNT_TOKEN_NAME = "environment"

CredentialKind = Literal["token", "session"]
RefusalCode = Literal["unauthenticated", "token-expired"]


class Principal(BaseModel):
    """Authenticated Sync API actor.

    ``actor`` is what every write records and what the rights checks compare. For an account of
    the product store it is the account identifier, and ``name`` is the account name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: str = Field(min_length=1)
    administrator: bool = False
    name: str | None = None
    kind: CredentialKind | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class Resolution:
    """What a presented value resolves to: a principal, or the code of a refusal."""

    principal: Principal | None = None
    refusal: RefusalCode | None = None

    def __post_init__(self) -> None:
        """Refuse a result that is both, or neither, a principal and a refusal."""
        if (self.principal is None) == (self.refusal is None):
            msg = "a resolution holds a principal or a refusal code, and only one of them"
            raise ValueError(msg)

    @classmethod
    def accepted(cls, principal: Principal) -> Resolution:
        """Return the result for a value that belongs to this principal."""
        return cls(principal=principal)

    @classmethod
    def refused(cls, code: RefusalCode = "unauthenticated") -> Resolution:
        """Return the result for a value that is refused with this code."""
        return cls(refusal=code)


class PrincipalResolver(Protocol):
    """Narrow authentication provider replaced without changing HTTP routes."""

    @property
    def secret_values(self) -> tuple[str, ...]: ...

    def resolve(self, token: str) -> Resolution: ...

    def principal_of(self, actor: str) -> Principal | None: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StoreAccountResolver:
    """Resolve an API token or a session token to its account, with one store call for each request.

    A session found idle costs one more call, which deletes it.
    """

    def __init__(
        self,
        projection: ProductProjection,
        *,
        idle_seconds: int,
        admin_token: str | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._projection = projection
        self._idle_period = timedelta(seconds=idle_seconds)
        self._admin_token = admin_token
        self._clock = clock

    @property
    def secret_values(self) -> tuple[str, ...]:
        """Return the token of ``admin`` that the environment defines, for boundary redaction.

    def resolve(self, token: str) -> Principal | None:
        """Compare every configured token with a timing-safe operation."""
        matched: Principal | None = None
        for actor, entry in self._entries:
            if hmac.compare_digest(token.encode(), entry.token.encode()):
                matched = Principal(actor=actor, administrator=entry.administrator)
        return matched

    def principal_of(self, actor: str) -> Principal | None:
        """Return the principal of a configured actor name, or ``None``; a token is never read or returned."""
        for name, entry in self._entries:
            if name == actor:
                return Principal(actor=name, administrator=entry.administrator)
        return None
