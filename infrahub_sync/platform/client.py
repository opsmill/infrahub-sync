"""Infrahub clients for Sync's own records: the service account, and each API caller.

Sync's records are written by a dedicated service account. A caller of the Sync API
is identified and authorized with their own Infrahub token, through a client built
for that request alone. Neither client reads Infrahub settings from the ambient
environment: the SDK would otherwise fill any field left out from `INFRAHUB_*`.

Sync's records live on Infrahub's default branch, which is read from the server.
The SDK's own default is a static `main`, which an Infrahub with a renamed default
branch does not have. So these clients send a query that names no branch to `/graphql`
without a branch suffix, which Infrahub serves on its own default branch: the query
that reads the branch's name, and the caller's profile query. Every record read and
write names its branch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from infrahub_sdk import Config, InfrahubClient, InfrahubClientSync

from infrahub_sync.configuration.credentials import pin_infrahub_sdk_authority

if TYPE_CHECKING:
    from collections.abc import Mapping

_ClientT = TypeVar("_ClientT", InfrahubClient, InfrahubClientSync)

ADDRESS_ENV = "INFRAHUB_SYNC_INFRAHUB_ADDRESS"
TOKEN_ENV = "INFRAHUB_SYNC_INFRAHUB_TOKEN"
TLS_INSECURE_ENV = "INFRAHUB_SYNC_INFRAHUB_TLS_INSECURE"
TIMEOUT_SECONDS = 60


SERVICE_ACCOUNT_REFUSED = "Infrahub refused INFRAHUB_SYNC_INFRAHUB_TOKEN, the Sync service account's token"


class ServiceAccountRefusedError(RuntimeError):
    """Infrahub refused the Sync service account's token."""


class PlatformSettingsError(ValueError):
    """A setting the Infrahub record clients need is missing or malformed."""


class DefaultBranchUnavailableError(RuntimeError):
    """The Infrahub server reported no default branch."""

    def __init__(self) -> None:
        super().__init__("the Infrahub server reports no default branch")


@dataclass(frozen=True, slots=True)
class PlatformSettings:
    """Where Sync's records live, and the service account that writes them."""

    address: str
    token: str
    tls_insecure: bool = False

    def __repr__(self) -> str:
        return f"PlatformSettings(address={self.address!r}, token=<redacted>, tls_insecure={self.tls_insecure})"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> PlatformSettings:
        """Read the settings, naming any that is missing and never echoing a value."""
        values = os.environ if environ is None else environ
        address = values.get(ADDRESS_ENV, "").strip()
        token = values.get(TOKEN_ENV, "").strip()
        missing = [name for name, value in ((ADDRESS_ENV, address), (TOKEN_ENV, token)) if not value]
        if missing:
            msg = f"{' and '.join(missing)} must be set for Sync's records in Infrahub"
            raise PlatformSettingsError(msg)
        flag = values.get(TLS_INSECURE_ENV, "").strip().lower()
        if flag not in {"", "true", "false"}:
            msg = f"{TLS_INSECURE_ENV} must be true or false"
            raise PlatformSettingsError(msg)
        return cls(address=address.rstrip("/"), token=token, tls_insecure=flag == "true")

    @classmethod
    def optional_from_environment(cls, environ: Mapping[str, str] | None = None) -> PlatformSettings | None:
        """The settings, or `None` when the environment sets none of them.

        Settings that are only partly set, or malformed, raise `PlatformSettingsError`:
        a process given an Infrahub never falls back to working without it.
        """
        values = os.environ if environ is None else environ
        if not any(values.get(name, "").strip() for name in (ADDRESS_ENV, TOKEN_ENV, TLS_INSECURE_ENV)):
            return None
        return cls.from_environment(values)

    def sdk_config(self, token: str | None = None) -> Config:
        """An SDK configuration for this server and the given token, the service token by default."""
        pinned: dict[str, Any] = pin_infrahub_sdk_authority(
            {
                "address": self.address,
                "api_token": self.token if token is None else token,
                "timeout": TIMEOUT_SECONDS,
                "tls_insecure": self.tls_insecure,
                "default_branch_from_git": False,
            }
        )
        return Config(**pinned)


def _on_the_server_default_branch(client: _ClientT) -> _ClientT:
    """Send a query that names no branch to `/graphql`, Infrahub's own default branch."""
    client.default_branch = ""
    return client


def service_client(settings: PlatformSettings) -> InfrahubClient:
    """The asynchronous client of the service account, for the Sync API."""
    return _on_the_server_default_branch(InfrahubClient(address=settings.address, config=settings.sdk_config()))


def service_client_sync(settings: PlatformSettings) -> InfrahubClientSync:
    """The synchronous client of the service account, for the service worker."""
    return _on_the_server_default_branch(InfrahubClientSync(address=settings.address, config=settings.sdk_config()))


def caller_client(settings: PlatformSettings, token: str) -> InfrahubClient:
    """A client acting as one Sync API caller, built for that request alone."""
    return _on_the_server_default_branch(InfrahubClient(address=settings.address, config=settings.sdk_config(token)))


def _default_branch_name(branches: Mapping[str, Any]) -> str:
    for name, branch in branches.items():
        if getattr(branch, "is_default", False):
            return str(name)
    raise DefaultBranchUnavailableError


async def default_branch(client: InfrahubClient) -> str:
    """The server's default branch, where Sync's records live, pinned on the client once read."""
    name = _default_branch_name(await client.branch.all())
    client.default_branch = name
    return name


def default_branch_sync(client: InfrahubClientSync) -> str:
    """The server's default branch, for the synchronous client, pinned on the client once read."""
    name = _default_branch_name(client.branch.all())
    client.default_branch = name
    return name
