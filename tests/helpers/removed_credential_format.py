"""A version stored in the removed credential format (spec 027, FR-021).

The package model refuses the ``credentials`` block and ``$credential`` references, so no test
can register such a version. A database written before the removal can hold one. These helpers
write that content into the row of a version that a test registered in the current format.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from typing import TYPE_CHECKING, Any

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
