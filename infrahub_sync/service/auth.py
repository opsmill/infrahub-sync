"""Application-owned principal resolution for the Sync API."""

from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from typing import Final, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PRINCIPALS_ENV = "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"


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


class PrincipalDirectory(Protocol):
    """Look up a configured principal by its actor name, as start-up does for anonymous access."""

    def principal_for_actor(self, actor: str) -> Principal | None: ...


CROSS_ORIGIN_REFUSED: Final = "cross-origin-refused"
HOST_NOT_ALLOWED: Final = "anonymous-host-not-allowed"
AnonymousRefusal = Literal["cross-origin-refused", "anonymous-host-not-allowed"]
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _host_and_port(url: str, default_port: int | None = None) -> tuple[str, int | None] | None:
    """Return the lowercase host name and the port of ``url``, or ``None`` when it names no host.

    IPv6 brackets are removed, so ``//[::1]:8001`` gives ``("::1", 8001)``. A URL without a port
    gives ``default_port``.
    """
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    return (host.lower(), default_port if port is None else port) if host else None


def is_host_name(entry: str) -> bool:
    """Return whether ``entry`` is a lowercase host name with no port and no IPv6 brackets.

    ``sync.example.net``, ``127.0.0.1`` and ``::1`` are host names; ``sync.example.net:8001``,
    ``[::1]`` and ``LocalHost`` are not, because a request's ``Host`` never matches them.
    """
    url = f"//[{entry}]" if ":" in entry else f"//{entry}"
    return bool(entry) and _host_and_port(url) == (entry, None)


@dataclass(frozen=True)
class AnonymousAccess:
    """The principal that requests without a token act as, and the host names they must use.

    Raises:
        ValueError: ``allowed_hosts`` is empty or holds an entry that is not a host name, which
            would refuse every request without a token.
    """

    principal: Principal
    allowed_hosts: frozenset[str]

    def __post_init__(self) -> None:
        if not self.allowed_hosts or not all(is_host_name(entry) for entry in self.allowed_hosts):
            msg = "allowed_hosts must hold at least one lowercase host name without a port or brackets"
            raise ValueError(msg)


def anonymous_refusal(host: str | None, origin: str | None, allowed_hosts: frozenset[str]) -> AnonymousRefusal | None:
    """Return why a request without a token may not act as the anonymous principal, or ``None``.

    A request whose ``Origin`` names another host or port, or is ``null``, comes from another site
    and is refused first (``cross-origin-refused``). A request whose ``Host`` names a host that is
    not allowed, such as a DNS-rebound name, is refused next (``anonymous-host-not-allowed``).
    Comparing ``Origin`` with ``Host`` rather than with a full URL stays correct behind a TLS proxy.
    """
    if origin is not None:
        default_port = _DEFAULT_PORTS.get(origin.partition(":")[0].lower())
        source = _host_and_port(origin, default_port)
        target = _host_and_port(f"//{host}", default_port) if host else None
        if source is None or source != target:
            return CROSS_ORIGIN_REFUSED
    target = _host_and_port(f"//{host}") if host else None
    if target is None or target[0] not in allowed_hosts:
        return HOST_NOT_ALLOWED
    return None


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

    def principal_for_actor(self, actor: str) -> Principal | None:
        """Return the configured principal with this actor name, or ``None``."""
        for configured_actor, entry in self._entries:
            if configured_actor == actor:
                return Principal(actor=configured_actor, administrator=entry.administrator)
        return None
