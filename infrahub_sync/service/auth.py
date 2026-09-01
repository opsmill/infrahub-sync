"""Application-owned principal resolution for the Sync API."""

from __future__ import annotations

import hmac
import json
import os
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PRINCIPALS_ENV = "INFRAHUB_SYNC_MANAGED_BEARER_TOKENS"


class Principal(BaseModel):
    """Authenticated Sync API actor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: str = Field(min_length=1)
    administrator: bool = False


class PrincipalResolver(Protocol):
    """Narrow authentication provider replaced without changing HTTP routes."""

    @property
    def secret_values(self) -> tuple[str, ...]: ...

    def resolve(self, token: str) -> Principal | None: ...


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
