"""Append a durable service execution the way the API does: through its owning receipt.

Every `prefect_executions` row belongs to exactly one still-unresolved mutation receipt,
and a write execution additionally needs the run's write admission to name that receipt.
Service tests that only need a durable link would otherwise have to restate that whole
reservation; this builds it once.

Not a test module: no assertions live here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING

from infrahub_sync.cache.sidecars import RunFile
from infrahub_sync.plan.writer import PLAN_DIR_NAME
from infrahub_sync.product_store import MutationReceipt
from infrahub_sync.product_store.bundle import BUNDLE_MEDIA_TYPE, PLAN_CHECKPOINT_ARTIFACT_ID, write_bundle
from infrahub_sync.service.checkpoints import publish_plan_checkpoint

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from infrahub_sync.plan.models import PlanManifest
    from infrahub_sync.product_store import PrefectExecutionLink, ProductProjection

_ORDINAL = count(1)
_WRITE_PURPOSES = ("apply", "sync")


def append_execution(
    projection: ProductProjection,
    run_id: str,
    link: PrefectExecutionLink,
    *,
    allocate_attempt: bool = False,
) -> PrefectExecutionLink:
    """Reserve the receipt that owns this append, then append `link` through it."""
    ordinal = next(_ORDINAL)
    receipt_id = f"m-fixture-{ordinal}"
    now = datetime.now(timezone.utc)
    reserved, _created = projection.reserve_mutation(
        MutationReceipt(
            receipt_id=receipt_id,
            actor="owner",
            key_digest=sha256(f"fixture-key-{ordinal}".encode()).hexdigest(),
            operation=link.purpose,
            target_run_id=run_id,
            request_fingerprint=sha256(f"{link.purpose}:{run_id}:{ordinal}".encode()).hexdigest(),
            reason="service execution fixture",
            resource_id=run_id,
            run_id=run_id,
            prefect_key=sha256(f"prefect:{receipt_id}".encode()).hexdigest(),
            created_at=now,
            updated_at=now,
        ),
        admit_write=link.purpose in _WRITE_PURPOSES,
    )
    return projection.add_prefect_execution(
        run_id, link, receipt_id=reserved.receipt_id, allocate_attempt=allocate_attempt
    )


class GrantingGuardSession:
    """A direct-session double that always grants the configuration write guard.

    Stage-driving tests whose subject is something else — binding, schema, ordering —
    still cross the guard, so they need a session that answers its three statements. What
    the guard does with a session that does not grant is
    `tests/service/test_managed_write_guard.py`'s subject, not theirs.
    """

    def execute(self, query: str, params: object = None) -> _GrantingCursor:  # noqa: PLR6301
        """Answer the acquire, ownership, and release statements the guard issues."""
        _ = params
        if "pg_locks" in query:
            return _GrantingCursor((_BACKEND_PID, True))
        if "pg_advisory_unlock" in query:
            return _GrantingCursor((True, _BACKEND_PID))
        return _GrantingCursor((None,))

    def close(self) -> None:
        """Close the dedicated session."""


class _GrantingCursor:
    def __init__(self, row: tuple[object, ...]) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...]:
        return self._row


_BACKEND_PID = 4242


def bind_granting_guard(monkeypatch: object, flow_module: object) -> None:
    """Bind a granting configuration write guard onto one service flow module."""
    monkeypatch.setattr(flow_module, "service_guard_session", GrantingGuardSession)  # ty: ignore[unresolved-attribute]
    monkeypatch.setattr(flow_module, "service_guard_secrets", lambda: ())  # ty: ignore[unresolved-attribute]


def publish_authored_plan(
    projection: ProductProjection,
    run_id: str,
    *,
    run_directory: Path,
    manifest: PlanManifest,
) -> None:
    """Hand an authored plan directory over the way a plan stage would.

    A stage that consumes a plan resolves it from the run's internal checkpoint, so a
    test that authors a plan directory itself has to publish it rather than rely on a
    later stage finding the directory.
    """
    publish_plan_checkpoint(projection, run_id, run_directory=run_directory, manifest=manifest)


def publish_plan_directory(projection: ProductProjection, run_id: str, run_directory: Path) -> None:
    """Publish a plan directory's current bytes as this run's plan checkpoint.

    Takes the files as they are rather than as a parsed manifest, so a test that authored
    a deliberately malformed plan can still hand it to the consuming stage and see that
    stage refuse it.
    """
    members = {
        f"{PLAN_DIR_NAME}/{name.name}": name.read_bytes() for name in sorted((run_directory / PLAN_DIR_NAME).iterdir())
    }
    side = run_directory / "A"
    if side.is_dir():
        members.update({f"A/{path.name}": path.read_bytes() for path in sorted(side.glob("*.parquet"))})
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_CHECKPOINT_ARTIFACT_ID,
        kind="run-bundle",
        media_type=BUNDLE_MEDIA_TYPE,
        data=write_bundle(members),
        visibility="internal",
    )


def stage_root(kwargs: Mapping[str, Any]) -> Path:
    """Return the explicit run-directory root a service stage handed the engine.

    Read rather than stringified. ``Path(str(kwargs.get("base_directory")))`` turns a
    missing value into the relative directory ``None`` and writes the run's files under
    whatever the caller's working directory happens to be; a stage that supplied no root
    is a defect in the stage, so this raises instead of inventing one.

    Raises:
        AssertionError: the stage passed no explicit run directory, or passed a value that
            is not an absolute path.
    """
    value = kwargs.get("base_directory")
    assert value is not None, "the service stage gave the engine no explicit run directory"
    root = Path(value)
    assert root.is_absolute(), f"a stage root must be absolute, got {str(root)!r}"
    return root


def write_applied_sidecar(run_directory: Path, *, mode: str = "apply") -> None:
    """Write the applied run sidecar a real engine leaves behind.

    The final checkpoint carries exactly this file, so a test whose engine is a double
    still has to leave the record that publication reads.

    The destination has to be the absolute private directory the stage was given. A double
    that lost its stage root would otherwise stringify the miss -- ``Path(str(None))`` is
    the relative directory ``None`` -- and quietly write this tree under the caller's
    working directory instead of failing.

    Raises:
        ValueError: `run_directory` is relative, so it does not name a stage's own root.
    """
    if not run_directory.is_absolute():
        msg = (
            f"the applied sidecar must be written into the stage's own absolute run "
            f"directory, got {str(run_directory)!r}"
        )
        raise ValueError(msg)
    run_directory.mkdir(parents=True, exist_ok=True)
    RunFile(path=run_directory / "run.json", status="applied", mode=mode).save()
