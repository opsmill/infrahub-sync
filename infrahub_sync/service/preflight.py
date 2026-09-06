"""Prove one deployment's destination answers, before anything is started.

This runs in the Sync image because that is where the declared package, the
credential resolver, and the destination's own settings already live. The host
lifecycle entry point owns Docker; it owns no configuration parsing, and it
never sees a credential value.

Every failure leaves as a family name. The inputs are a URL and a credential,
and an HTTP client renders both into its own exception text, so nothing from
underneath crosses this boundary.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

import httpx

from infrahub_sync.configuration.credentials import CredentialConfigurationError, resolve_reference
from infrahub_sync.configuration.models import ConfigurationPackageParseError, parse_configuration_package
from infrahub_sync.product_store import configs

from .bootstrap import CONFIGURATION_INVALID, CONFIGURATION_PATH_ENV, SETTING_MISSING, BootstrapError

if TYPE_CHECKING:
    from infrahub_sync.configuration import ConfigurationPackage

logger = logging.getLogger(__name__)

DESTINATION_UNREACHABLE = "destination-unreachable"
CREDENTIAL_UNRESOLVED = "destination-credential-unresolved"
DESTINATION_URL_MISSING = "destination-url-missing"

PROBE_TIMEOUT_SECONDS = 15.0


def declared_destination_url(package: ConfigurationPackage) -> str:
    """Return the absolute URL the declared destination names."""
    settings = package.configuration.destination.settings or {}
    url = settings.get("url")
    if not isinstance(url, str) or not url:
        raise BootstrapError(DESTINATION_URL_MISSING)
    return url


def resolve_destination_credentials(package: ConfigurationPackage) -> None:
    """Prove every credential the destination references resolves in this environment.

    Resolution only: the value is never returned, logged, or sent. A reference
    that resolves to nothing here would fail at the first run instead, long after
    the operator stopped watching.
    """
    settings = package.configuration.destination.settings or {}
    for value in settings.values():
        # Declared settings arrive as read-only mappings, not dicts: a dict check
        # here would skip every reference and pass an environment holding none.
        if not isinstance(value, Mapping) or set(value) != {"$credential"}:
            continue
        name = value["$credential"]
        if not isinstance(name, str):
            raise BootstrapError(CREDENTIAL_UNRESOLVED)
        try:
            resolve_reference(package, name)
        except CredentialConfigurationError:
            raise BootstrapError(CREDENTIAL_UNRESOLVED) from None


def probe(url: str, *, client_factory: type[httpx.Client] = httpx.Client) -> None:
    """Answer whether the destination URL is reachable, and refuse in a fixed family.

    This generic boundary cannot know an adapter's authentication protocol. Any
    HTTP response therefore proves reachability, including an authentication
    challenge. Credential references are resolved separately, but their remote
    validity is established only by an adapter's first authenticated read.
    """
    try:
        with client_factory(timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False) as client:
            client.get(url)
    # `InvalidURL` is raised while parsing the address and is not an `HTTPError`,
    # so it needs naming here to share the family. It renders the URL.
    except (httpx.HTTPError, httpx.InvalidURL):
        raise BootstrapError(DESTINATION_UNREACHABLE) from None


def _package() -> ConfigurationPackage:
    path = os.environ.get(CONFIGURATION_PATH_ENV)
    if path is None or not path.strip():
        logger.error("preflight setting %s is missing", CONFIGURATION_PATH_ENV)
        raise BootstrapError(SETTING_MISSING)
    try:
        return parse_configuration_package(configs.load_package_content(path))
    except (configs.ConfigsError, ConfigurationPackageParseError):
        raise BootstrapError(CONFIGURATION_INVALID) from None


def main() -> int:
    """Check the declared destination, reporting only a family on failure."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | infrahub-sync preflight | %(message)s")
    try:
        package = _package()
        resolve_destination_credentials(package)
        probe(declared_destination_url(package))
    except BootstrapError as error:
        logger.error("refused: %s", error.family)  # noqa: TRY400 - the family is the whole report.
        return 1
    logger.info("destination answered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
