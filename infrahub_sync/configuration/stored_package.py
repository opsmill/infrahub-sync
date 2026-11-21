"""Reading the declared content of a stored configuration version (spec 027, FR-021).

New input goes through :func:`~infrahub_sync.configuration.models.parse_configuration_package`.
Content read from the product store goes through :func:`parse_stored_configuration_package`,
which accepts what the build before the removal of the credential format wrote into every
version: an empty ``credentials`` block.
"""

from __future__ import annotations

from hashlib import sha256
from typing import NamedTuple

from infrahub_sync.plan.canonical import canonical_json_bytes

from .models import ConfigurationPackage, parse_configuration_package
from .removed_credential_format import with_empty_credentials_block, without_empty_credentials_block


class StoredConfigurationPackage(NamedTuple):
    """A package read from a stored configuration version, and the checksum of the content as stored."""

    package: ConfigurationPackage
    checksum: str
    """The digest of the stored content. Compare this, not ``package.checksum()``, with the stored checksum."""


def parse_stored_configuration_package(value: object) -> StoredConfigurationPackage:
    """Parse the declared content of a stored configuration version.

    Use this for content read from the product store, and :func:`parse_configuration_package`
    for new input. The build before the removal of the credential format wrote an empty
    ``credentials`` block into every version, and the stored checksum of such a version digests
    that block. This function ignores the empty block and returns the checksum of the content
    with it, so the version keeps the checksum that its runs, its saved plans and its approvals
    hold. For any other content the checksum is ``package.checksum()``.

    Raises:
        RemovedCredentialFormatError: the content holds a ``credentials`` block that is not
            empty, or a ``$credential`` reference.
        ConfigurationPackageParseError: the content is not a package.
    """
    content, held_empty_block = without_empty_credentials_block(value)
    package = parse_configuration_package(content)
    if not held_empty_block:
        return StoredConfigurationPackage(package, package.checksum())
    stored_content = with_empty_credentials_block(package.declared_content())
    checksum = sha256(canonical_json_bytes(stored_content, kind="configuration-package")).hexdigest()
    return StoredConfigurationPackage(package, checksum)
