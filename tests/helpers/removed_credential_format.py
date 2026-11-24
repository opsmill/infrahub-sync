"""A version stored in the removed credential format (spec 027, FR-021).

The package model refuses the ``credentials`` block and ``$credential`` references, so no test
can register such a version. A database written before the removal can hold one. These helpers
write that content into the row of a version that a test registered in the current format.

The build before the removal wrote ``"credentials": {}`` into every version, also into one that
declared no reference, and its checksum digests that content. ``store_as_the_earlier_build``
writes such a row: the content and the checksum.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.canonical import canonical_json_bytes

if TYPE_CHECKING:
    from pathlib import Path

REMOVED_REFERENCE_NAME = "removed-reference-marker"
REMOVED_IDENTIFIER = "INFRAHUB_SYNC_CREDENTIAL_MARKER_OF_THE_TEST"


def removed_format_content(content: dict[str, Any]) -> dict[str, Any]:
    """Return the package content as a release before the removal declared it.

    Each block that names a stored credential declares a ``$credential`` reference in its
    ``token`` setting instead, and the package gets a ``credentials`` block.
    """
    removed = copy.deepcopy(content)
    for block in ("source", "destination"):
        adapter = removed["configuration"][block]
        if adapter.pop("credential", None) is not None:
            adapter.setdefault("settings", {})["token"] = {"$credential": REMOVED_REFERENCE_NAME}
    removed["credentials"] = {REMOVED_REFERENCE_NAME: {"provider": "env", "identifier": REMOVED_IDENTIFIER}}
    return removed


def store_in_removed_format(root: Path, config_id: str, registry_version: int, content: dict[str, Any]) -> None:
    """Rewrite the stored content of one version to the removed format, in the SQLite store under ``root``."""
    databases = sorted(root.rglob("*.sqlite3"))
    assert len(databases) == 1, databases
    with sqlite3.connect(databases[0]) as connection:
        changed = connection.execute(
            "UPDATE configuration_versions SET declared_content = ? WHERE config_id = ? AND registry_version = ?",
            (json.dumps(removed_format_content(content)), config_id, registry_version),
        ).rowcount
    assert changed == 1


def earlier_build_content(content: dict[str, Any]) -> dict[str, Any]:
    """Return the package content as the build before the removal stored a package without a reference.

    That build knew no ``credential`` key in an adapter block, and it wrote an empty
    ``credentials`` block into every version.
    """
    earlier = copy.deepcopy(content)
    for block in ("source", "destination"):
        earlier["configuration"][block].pop("credential", None)
    earlier["credentials"] = {}
    return earlier


def earlier_build_checksum(content: dict[str, Any]) -> str:
    """Return the checksum that the build before the removal stored for ``content``."""
    return sha256(canonical_json_bytes(earlier_build_content(content), kind="configuration-package")).hexdigest()


def store_as_the_earlier_build(root: Path, config_id: str, registry_version: int) -> str:
    """Rewrite one version, content and checksum, as the build before the removal stored it.

    Call it before any run of the version exists: a run holds the checksum of its version.
    Returns the checksum that the earlier build stored.
    """
    databases = sorted(root.rglob("*.sqlite3"))
    assert len(databases) == 1, databases
    with sqlite3.connect(databases[0]) as connection:
        rows = connection.execute(
            "SELECT declared_content FROM configuration_versions WHERE config_id = ? AND registry_version = ?",
            (config_id, registry_version),
        ).fetchall()
        assert len(rows) == 1
        content = json.loads(rows[0][0])
        checksum = earlier_build_checksum(content)
        changed = connection.execute(
            "UPDATE configuration_versions SET declared_content = ?, package_checksum = ? "
            "WHERE config_id = ? AND registry_version = ?",
            (json.dumps(earlier_build_content(content)), checksum, config_id, registry_version),
        ).rowcount
    assert changed == 1
    return checksum
