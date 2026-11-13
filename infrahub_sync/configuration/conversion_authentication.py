"""The credential settings that each v2 authentication method read, frozen at v2 ``2.0.2``.

``configs convert`` references only the credential settings that the v2 configuration used
(spec FR-006, research R4): V3 refuses a referenced variable that is missing or empty, so a
reference to a credential v2 never read would stop the converted package from running.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class AuthenticationMethods:
    """The v2 ``auth_method`` values of one adapter and the credential settings each one used.

    ``default`` is the value v2 used when the file has no ``auth_method``; ``uses`` maps each
    value v2 accepted to the credential settings it read.
    """

    default: str
    uses: Mapping[str, tuple[str, ...]]


_TOKEN = ("token",)
_USERNAME_PASSWORD = ("username", "password")
# ``rest_api_client.py:32-50`` in ``2.0.2``: any other value raised "Invalid authentication
# configuration!". Peering Manager is built on the generic REST adapter and only sets the default.
_GENERIC_REST_AUTHENTICATION = AuthenticationMethods(
    "token",
    MappingProxyType(
        {
            "token": _TOKEN,
            "x-auth-token": _TOKEN,
            "api-key": _TOKEN,
            "key": _TOKEN,
            "basic": _USERNAME_PASSWORD,
            "none": (),
        }
    ),
)

V2_AUTHENTICATION_METHODS: Mapping[str, AuthenticationMethods] = MappingProxyType(
    {
        "genericrestapi": _GENERIC_REST_AUTHENTICATION,
        "peeringmanager": _GENERIC_REST_AUTHENTICATION,
        # ``prometheus.py:73,90,94,381`` in ``2.0.2``.
        "prometheus": AuthenticationMethods(
            "none", MappingProxyType({"none": (), "basic": _USERNAME_PASSWORD, "bearer": _TOKEN})
        ),
    }
)
"""The adapters whose v2 ``auth_method`` setting chose the credential settings they read."""

V2_FIXED_CREDENTIALS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    # ``slurpitsync.py:39-40`` in ``2.0.2`` passes the settings to ``slurpit.api(url, api_key, ...)``,
    # which has no ``token`` parameter.
    {"slurpitsync": ("api_key",)}
)
"""The adapters that always read the same credential settings, fewer than V3 declares."""


class UsedCredentials(NamedTuple):
    """The credential settings v2 read for one adapter block, and why it did not read the others."""

    settings: frozenset[str]
    reason: str


class UnknownAuthenticationMethodError(ValueError):
    """The ``auth_method`` value is one that v2 did not accept; the message names the accepted values."""


def used_credentials(name: str, declared: tuple[str, ...], settings: Mapping[str, Any]) -> UsedCredentials:
    """Return the credential settings that v2 read for adapter ``name`` with these settings.

    ``declared`` is the adapter's V3 credential settings, all of which an adapter without an
    authentication method read.

    Raises:
        UnknownAuthenticationMethodError: ``auth_method`` holds a value v2 did not accept.
    """
    if name in V2_FIXED_CREDENTIALS:
        return UsedCredentials(frozenset(V2_FIXED_CREDENTIALS[name]), f"v2's {name} adapter does not read it")
    methods = V2_AUTHENTICATION_METHODS.get(name)
    if methods is None:
        return UsedCredentials(frozenset(declared), "")
    method = settings.get("auth_method", methods.default)
    if not isinstance(method, str) or method not in methods.uses:
        msg = f"v2's {name} adapter did not accept this authentication method; use one of {', '.join(methods.uses)}"
        raise UnknownAuthenticationMethodError(msg)
    default = "" if "auth_method" in settings else ", the v2 default,"
    return UsedCredentials(frozenset(methods.uses[method]), f"auth_method {method}{default} does not use it")
