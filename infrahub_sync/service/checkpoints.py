"""The service's use of the internal run-bundle protocol.

Stages run on workers that share no filesystem, so what one stage produced and the next
one needs travels as an internal artifact in product storage. This module is the only
place that decides which files make up a checkpoint; the bundle codec owns the container
and the product store owns the bounded read, the size and digest validation, and the
atomic extraction.

Two checkpoints exist, and their membership is fixed rather than incidental:

The plan checkpoint carries what verify and apply consume -- the plan manifest, the
operations file, and the source snapshots the manifest declares. The final checkpoint
carries the applied run sidecar and nothing else. It records what the engine completed;
it is not a recovery input, a success receipt, or future incremental state, so nothing
reads it back and no stage may infer anything from its presence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME
from infrahub_sync.product_store.bundle import (
    BUNDLE_MEDIA_TYPE,
    FINAL_CHECKPOINT_ARTIFACT_ID,
    PLAN_CHECKPOINT_ARTIFACT_ID,
    extract_bundle,
    write_bundle,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from infrahub_sync.plan.models import PlanManifest
    from infrahub_sync.product_store import ArtifactReference, ProductProjection

__all__ = [
    "FINAL_RUN_FILE_MEMBER",
    "CheckpointUnavailableError",
    "publish_final_checkpoint",
    "publish_plan_checkpoint",
    "rehydrate_plan_checkpoint",
]

_BUNDLE_KIND = "run-bundle"
_PLAN_MANIFEST_MEMBER = f"{PLAN_DIR_NAME}/{MANIFEST_FILE_NAME}"
_PLAN_OPERATIONS_MEMBER = f"{PLAN_DIR_NAME}/{OPERATIONS_FILE_NAME}"
# The applied sidecar lives at the run directory's root, and a bundle member path is two
# segments, so the checkpoint states which container it belongs to.
FINAL_RUN_FILE_MEMBER = "final/run.json"
_RUN_FILE_NAME = "run.json"


class CheckpointUnavailableError(RuntimeError):
    """A stage's checkpoint could not be resolved, read, or validated.

    Carries the store's or the codec's own reason, so the refusal names which property
    failed rather than only that something did.
    """

    def __init__(self, artifact_id: str, reason: str) -> None:
        super().__init__(f"internal checkpoint {artifact_id!r} is unusable: {reason}")
        self.artifact_id = artifact_id
        self.reason = reason


def publish_plan_checkpoint(
    projection: ProductProjection,
    run_id: str,
    *,
    run_directory: Path,
    manifest: PlanManifest,
    secrets: Sequence[str] = (),
) -> ArtifactReference:
    """Publish the plan this stage wrote as this run's fixed plan checkpoint.

    The source snapshots come from the manifest rather than from a directory listing: the
    manifest is what the plan's checksum covers, so a file the plan does not account for
    cannot enter the checkpoint and a file it declares cannot be silently missing from it.
    """
    members = {
        _PLAN_MANIFEST_MEMBER: (run_directory / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes(),
        _PLAN_OPERATIONS_MEMBER: (run_directory / PLAN_DIR_NAME / OPERATIONS_FILE_NAME).read_bytes(),
    }
    for record in manifest.source_snapshot:
        members[record.path] = (run_directory / record.path).read_bytes()
    return projection.publish_artifact(
        run_id,
        artifact_id=PLAN_CHECKPOINT_ARTIFACT_ID,
        kind=_BUNDLE_KIND,
        media_type=BUNDLE_MEDIA_TYPE,
        data=write_bundle(members),
        visibility="internal",
        secrets=secrets,
    )


def publish_final_checkpoint(
    projection: ProductProjection,
    run_id: str,
    *,
    run_directory: Path,
    secrets: Sequence[str] = (),
) -> ArtifactReference:
    """Publish the applied run sidecar as this run's fixed final checkpoint."""
    return projection.publish_artifact(
        run_id,
        artifact_id=FINAL_CHECKPOINT_ARTIFACT_ID,
        kind=_BUNDLE_KIND,
        media_type=BUNDLE_MEDIA_TYPE,
        data=write_bundle({FINAL_RUN_FILE_MEMBER: (run_directory / _RUN_FILE_NAME).read_bytes()}),
        visibility="internal",
        secrets=secrets,
    )


def rehydrate_plan_checkpoint(
    projection: ProductProjection,
    run_id: str,
    *,
    destination: Path,
) -> Path:
    """Place this run's plan checkpoint into `destination`, or refuse.

    Everything that can refuse happens before a byte reaches disk: the lookup refuses an
    artifact belonging to another run or oversized by its committed reference, the
    provider bounds the transfer and checks stored size and digest, and the codec
    validates the whole archive. `destination` then either holds the complete plan or
    does not exist, so no later step can run against a partial one.
    """
    stored = projection.lookup_internal_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID)
    if stored.value is None:
        raise CheckpointUnavailableError(PLAN_CHECKPOINT_ARTIFACT_ID, stored.reason or "artifact-unavailable")
    extract_bundle(stored.value, destination)
    return destination
