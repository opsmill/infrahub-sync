"""Check at startup that the Sync schema extension is loaded in Infrahub.

The operator loads `schema/sync.yml`; Sync never loads or changes a schema, and so
never needs schema-admin rights. Infrahub keeps no version of a loaded extension,
so the check is structural: every kind Sync reads or writes exists on the default
branch, with the fields and inherited generics Sync relies on. A missing piece is
named, together with the file that provides it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from infrahub_sdk.exceptions import SchemaNotFoundError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from infrahub_sdk import InfrahubClient, InfrahubClientSync

SCHEMA_FILE = "schema/sync.yml"


@dataclass(frozen=True, slots=True)
class RequiredKind:
    """What Sync relies on in one kind of the extension."""

    kind: str
    attributes: frozenset[str]
    relationships: frozenset[str]
    inherits: frozenset[str] = frozenset()


REQUIRED_KINDS: tuple[RequiredKind, ...] = (
    RequiredKind(
        "SyncConfiguration",
        frozenset({"name", "description", "document"}),
        frozenset({"versions", "runs"}),
        frozenset({"CoreTaskTarget"}),
    ),
    RequiredKind(
        "SyncConfigurationVersion",
        frozenset({"number", "checksum", "document", "created_at"}),
        frozenset({"configuration", "runs"}),
    ),
    RequiredKind(
        "SyncRun",
        frozenset(
            {
                "run_id",
                "operation",
                "phase",
                "outcome",
                "target_branch",
                "plan_checksum",
                "summary",
                "requested_by",
                "reason",
                "flow_run_id",
                "started_at",
                "finished_at",
            }
        ),
        frozenset({"configuration", "version", "plan_run", "approvals"}),
        frozenset({"CoreTaskTarget"}),
    ),
    RequiredKind(
        "SyncApproval",
        frozenset({"approved_checksum", "approved_by", "reason"}),
        frozenset({"run"}),
    ),
)


class SyncSchemaMissingError(RuntimeError):
    """The Sync schema extension is missing from Infrahub, or lacks what Sync needs."""

    def __init__(self, missing: Iterable[str]) -> None:
        self.missing = tuple(missing)
        super().__init__(
            f"the Sync schema extension is not loaded as this release needs it: missing {', '.join(self.missing)}; "
            f"load {SCHEMA_FILE} from this release with `infrahubctl schema load`"
        )


def _gaps(required: RequiredKind, schema: Any | None) -> list[str]:
    if schema is None:
        return [required.kind]
    attributes = {attribute.name for attribute in schema.attributes}
    relationships = {relationship.name for relationship in schema.relationships}
    inherits = set(getattr(schema, "inherit_from", None) or ())
    return [
        *(f"{required.kind}.{name}" for name in sorted(required.attributes - attributes)),
        *(f"{required.kind}.{name}" for name in sorted(required.relationships - relationships)),
        *(f"{required.kind} inheriting {name}" for name in sorted(required.inherits - inherits)),
    ]


async def require_sync_schema(client: InfrahubClient, branch: str) -> None:
    """Refuse, naming every gap, unless the extension is loaded as this release needs it."""
    missing: list[str] = []
    for required in REQUIRED_KINDS:
        try:
            schema = await client.schema.get(kind=required.kind, branch=branch, refresh=True)
        except SchemaNotFoundError:
            schema = None
        missing.extend(_gaps(required, schema))
    if missing:
        raise SyncSchemaMissingError(missing)


def require_sync_schema_sync(client: InfrahubClientSync, branch: str) -> None:
    """The same check, for the synchronous client of the service worker."""
    missing: list[str] = []
    for required in REQUIRED_KINDS:
        try:
            schema = client.schema.get(kind=required.kind, branch=branch, refresh=True)
        except SchemaNotFoundError:
            schema = None
        missing.extend(_gaps(required, schema))
    if missing:
        raise SyncSchemaMissingError(missing)
