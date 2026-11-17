"""Application-owned principal resolution for the Sync API.

A request presents one value: an API token or a session token. ``StoreAccountResolver`` reads its
SHA-256 digest in the product store, with the account that owns it, for every request. Nothing is
cached, so the next request of a disabled account is refused.

``apply_admin_account`` makes the ``admin`` account match the variables of the environment when
the service starts.
"""

from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .credentials import digest, hash_password, verify_password

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync.product_store import Account, ProductProjection

    from .settings import AdminAccountSettings

# TODO(022 T021-T026): removed once the tests use the store-backed fixture.
PRINCIPALS_ENV = "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"

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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StoreAccountResolver:
    """Resolve an API token or a session token to its account, with one store call for each request."""

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

        The store holds every other token as a digest, so the process cannot list them. The
        password of ``admin`` is never in this list.
        """
        return () if self._admin_token is None else (self._admin_token,)

    def resolve(self, token: str) -> Resolution:
        """Return the account that owns the presented value, or the code of the refusal.

        An unknown value, a disabled account, a deleted account and a session that was not used
        for longer than the idle period are refused as ``unauthenticated``. An API token after its
        expiry date is refused as ``token-expired``.
        """
        now = self._clock()
        resolved = self._projection.resolve_digest(digest(token), now=now)
        if resolved is None:
            return Resolution.refused()
        account = resolved.account
        if account.disabled or account.deleted_at is not None:
            return Resolution.refused()
        if resolved.kind == "token":
            if resolved.expires_at is not None and resolved.expires_at <= now:
                return Resolution.refused("token-expired")
        elif resolved.last_used_at is None or now - resolved.last_used_at > self._idle_period:
            # The store recorded this use before the verdict. The session is deleted, or its
            # next request would find it used a moment ago.
            self._projection.delete_account_session(resolved.credential_id)
            return Resolution.refused()
        return Resolution.accepted(
            Principal(
                actor=account.account_id,
                administrator=account.administrator,
                name=account.name,
                kind=resolved.kind,
                session_id=resolved.credential_id if resolved.kind == "session" else None,
            )
        )


def apply_admin_account(projection: ProductProjection, settings: AdminAccountSettings) -> Account:
    """Make the ``admin`` account of the store match the variables of the environment.

    One transaction, which two processes that start together run one after the other:

    - ``admin`` is created when it is missing, as an active administrator that the environment
      manages;
    - when the stored hash does not verify the password of the environment, a new hash is stored
      and the sessions of ``admin`` end;
    - ``admin`` keeps one API token, the token of the environment, or none when it is not set;
    - a refusal after repeated failed sign-ins for ``admin`` ends.

    A call that finds the store as the environment describes it changes no row. The caller removes
    the password from the environment of the process afterwards.

    Raises:
        DuplicateCredentialError: another account already has an API token with the same value.
    """

    def password_hash_for(stored_hash: str | None) -> str | None:
        if stored_hash is not None and verify_password(settings.password, stored_hash):
            return None
        return hash_password(settings.password)

    return projection.apply_environment_account(
        name=ADMIN_ACCOUNT_NAME,
        password_hash_for=password_hash_for,
        token_name=ADMIN_ACCOUNT_TOKEN_NAME,
        token_digest=None if settings.token is None else digest(settings.token),
    )


class _ConfiguredPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str = Field(min_length=16)
    administrator: bool = False


# TODO(022 T021-T026): removed once the tests use the store-backed fixture.
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

    def resolve(self, token: str) -> Resolution:
        """Compare every configured token with a timing-safe operation."""
        matched: Principal | None = None
        for actor, entry in self._entries:
            if hmac.compare_digest(token.encode(), entry.token.encode()):
                matched = Principal(actor=actor, administrator=entry.administrator)
        return Resolution.refused() if matched is None else Resolution.accepted(matched)
