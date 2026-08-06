"""Plan-artifact fixture builders shared by the Phase C reader, verifier and review tests.

These builders write `plan/operations.jsonl` and `plan/manifest.json` **directly** rather
than through `write_plan_artifact`, and that is deliberate: several of the shapes T024, T025
and T026 have to produce cannot be produced through the record types at all, because the
record types refuse them. An operation whose `action` is `"purge"` raises
`UnsupportedOperationActionError` at construction; a `create` with no `payload` raises at
construction; a line count disagreeing with `operations_count` is unreachable from a writer
that derives one from the other. A fixture for those cases has to be assembled at the byte
level.

Everything a case is *not* about is kept consistent, so each fixture is broken in exactly
one way: `operations_count` is derived from the lines given, `plan_checksum` is computed over
the manifest as finally assembled — overrides included — and the encoding is the artifact's
own `canonical_json_bytes`. A fixture that was accidentally broken twice would let a test
pass on the wrong verdict.

Not a test module: no assertions live here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.checksum import compute_plan_checksum
from infrahub_sync.plan.identity import operation_id
from infrahub_sync.plan.models import PLAN_FORMAT_VERSION
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

RUN_ID = "20260726T1804-9f3ac210"
OTHER_RUN_ID = "20260727T0902-1b7de004"
CONFIG_VERSION = "5f2c9b1e7a4d3c8f"
SYNC_NAME = "demo"

# A payload value the tamper helper below rewrites. Payload, never identity — see
# `tamperable_operation`.
TAMPERABLE_VALUE = "production"


def operation_record(  # noqa: PLR0913 — one parameter per operation-record field the fixtures vary
    *,
    action: str = "create",
    kind: str = "BuiltinTag",
    identity: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
    relationships: Sequence[Mapping[str, Any]] | None = None,
    tier: int = 0,
) -> dict[str, Any]:
    """Build one operation line as a mapping, with a correctly derived identifier.

    The payload defaults to the identity, so the AD042 identity-in-payload guard is satisfied
    without every case restating it. `action` is a plain `str` rather than the `Literal`,
    because the point of some fixtures is an action the vocabulary does not admit.
    """
    effective_identity = {"name": "prod"} if identity is None else dict(identity)
    record: dict[str, Any] = {
        "operation_id": operation_id(action, kind, effective_identity),
        "action": action,
        "kind": kind,
        "identity": effective_identity,
        "tier": tier,
    }
    if action != "delete":
        record["payload"] = dict(effective_identity) if payload is None else dict(payload)
    if relationships:
        record["relationships"] = [dict(reference) for reference in relationships]
    return record


def plan_dir(run_directory: Path) -> Path:
    """The artifact directory inside a run directory."""
    return run_directory / PLAN_DIR_NAME


def manifest_path(run_directory: Path) -> Path:
    """The manifest's path inside a run directory."""
    return plan_dir(run_directory) / MANIFEST_FILE_NAME


def operations_path(run_directory: Path) -> Path:
    """The operations file's path inside a run directory."""
    return plan_dir(run_directory) / OPERATIONS_FILE_NAME


def encode_operations(records: Sequence[Mapping[str, Any]]) -> bytes:
    """Encode operation mappings as `operations.jsonl`'s bytes.

    One canonical JSON object per line, every line LF-terminated including the last, exactly
    as `infrahub_sync.plan.writer` emits them — so a fixture's checksum is a real checksum
    and not an artifact of a second encoder.
    """
    return b"".join(canonical_json_bytes(record) + b"\n" for record in records)


def write_artifact(  # noqa: PLR0913 — one parameter per manifest field the fixtures vary
    run_directory: Path,
    records: Sequence[Mapping[str, Any]] = (),
    *,
    run_id: str = RUN_ID,
    config_version: str = CONFIG_VERSION,
    source_snapshot: Sequence[Mapping[str, Any]] | None = None,
    deletes_computed: bool = True,
    **manifest_overrides: Any,  # noqa: ANN401 — a manifest field's value is any JSON value
) -> dict[str, Any]:
    """Write `<run_directory>/plan/` from raw mappings and return the manifest as written.

    `manifest_overrides` are applied **before** `plan_checksum` is computed, so a fixture
    that overrides `format_version` or `operations_count` — or adds an unknown field — is
    still checksum-consistent and therefore broken only in the way its case is about.
    """
    operations_bytes = encode_operations(records)
    body: dict[str, Any] = {
        "format_version": PLAN_FORMAT_VERSION,
        "run_id": run_id,
        "created_at": "2026-07-26T18:04:11.512034+00:00",
        "config_version": config_version,
        "source_snapshot": [dict(record) for record in (source_snapshot or ())],
        "operations_count": len(records),
        "delete_operations_computed": deletes_computed,
    }
    body.update(manifest_overrides)
    body["plan_checksum"] = compute_plan_checksum(body, operations_bytes)

    directory = plan_dir(run_directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / OPERATIONS_FILE_NAME).write_bytes(operations_bytes)
    (directory / MANIFEST_FILE_NAME).write_bytes(canonical_json_bytes(body))
    return body


def tamperable_operation() -> dict[str, Any]:
    """A create whose payload carries a value `tamper_with_operations` can alter.

    The altered value must be a **payload** value and not an identity one: an operation's
    `operation_id` is derived from its identity, so tampering with the identity invalidates
    the record itself and the reader reports the fixture as torn — a checksum case built on
    that fixture would never reach the checksum check at all.
    """
    return operation_record(payload={"name": "prod", "description": TAMPERABLE_VALUE})


def tamper_with_operations(run_directory: Path) -> None:
    """Change the operations file's bytes in place, keeping its line count and its records valid.

    The realistic tamper: an operator edits a payload value after the artifact was written.
    The line count still agrees with `operations_count` so the artifact is not torn, and
    every record still validates, so the recomputed checksum is the only thing that
    disagrees — which is the condition the checksum check exists to catch.
    """
    path = operations_path(run_directory)
    original = path.read_bytes()
    tampered = original.replace(TAMPERABLE_VALUE.encode(), b"edited-after-the-plan-was-written")
    if tampered == original:  # pragma: no cover — a fixture guard, not a branch under test
        msg = (
            f"the operations file at {path} carries no {TAMPERABLE_VALUE!r} to alter: build the "
            "fixture with `tamperable_operation()`"
        )
        raise AssertionError(msg)
    path.write_bytes(tampered)
