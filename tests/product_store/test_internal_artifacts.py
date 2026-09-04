"""Internal artifact visibility, bounded reads, and fixed-checkpoint publication."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from infrahub_sync.product_store import (
    ArtifactReference,
    DuplicateArtifactError,
    ProductProjection,
    ProductRun,
)
from infrahub_sync.product_store.bundle import (
    FINAL_CHECKPOINT_ARTIFACT_ID,
    PLAN_CHECKPOINT_ARTIFACT_ID,
    write_bundle,
)
from infrahub_sync.product_store.store import FileArtifactStore, S3ArtifactStore, SQLiteRunStore

_SECRET = "super-secret-token-value"  # noqa: S105 - deliberate non-secret boundary canary.
_BUNDLE_MEMBERS = {
    "plan/operations.jsonl": b'{"op": "create"}\n',
    "plan/manifest.json": b'{"checksum": "' + b"b" * 64 + b'"}',
    "A/devices.parquet": b"PAR1payloadPAR1",
}


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.body_reads: list[str] = []

    def put(self, *, bucket: str, key: str, data: bytes, if_absent: bool = False) -> None:
        marker = (bucket, key)
        if if_absent and marker in self.objects:
            raise DuplicateArtifactError(key)
        self.objects[marker] = data

    def get(self, *, bucket: str, key: str) -> bytes | None:
        return self.objects.get((bucket, key))

    def head(self, *, bucket: str, key: str) -> int | None:
        stored = self.objects.get((bucket, key))
        return None if stored is None else len(stored)

    def get_bounded(self, *, bucket: str, key: str, limit: int) -> bytes | None:
        stored = self.objects.get((bucket, key))
        if stored is None:
            return None
        self.body_reads.append(key)
        return stored[: limit + 1]

    def copy(self, *, bucket: str, source: str, destination: str) -> None:
        self.objects[bucket, destination] = self.objects[bucket, source]

    def delete(self, *, bucket: str, key: str) -> None:
        self.objects.pop((bucket, key), None)


@pytest.fixture(params=("filesystem", "s3"))
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> ProductProjection:
    """Both deployed provider shapes, so parity is the default rather than a separate case."""
    if request.param == "filesystem":
        return ProductProjection(
            SQLiteRunStore(tmp_path / "local.sqlite3"),
            FileArtifactStore(tmp_path / "objects"),
        )
    return ProductProjection(
        SQLiteRunStore(tmp_path / "local.sqlite3"),
        S3ArtifactStore(_FakeS3(), bucket="product-artifacts", prefix="unit"),
    )


def _run(run_id: str = "run-bundle-001") -> ProductRun:
    return ProductRun(
        run_id=run_id,
        operation="plan",
        configuration_reference="sha256:configuration",
        actor="operator@example.com",
        started_at=datetime(2026, 9, 3, 12, tzinfo=timezone.utc),
        phase="planning",
    )


def _created(projection: ProductProjection, run_id: str = "run-bundle-001") -> str:
    projection.create_run(_run(run_id))
    return run_id


# ---------------------------------------------------------------------------
# Internal visibility
# ---------------------------------------------------------------------------


def test_an_internal_artifact_is_absent_from_the_public_run_projection(projection: ProductProjection) -> None:
    run_id = _created(projection)
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_CHECKPOINT_ARTIFACT_ID,
        kind="run-bundle",
        media_type="application/zip",
        data=write_bundle(_BUNDLE_MEMBERS),
        visibility="internal",
    )
    projection.publish_artifact(
        run_id,
        artifact_id="plan-review",
        kind="saved-plan-review",
        media_type="application/json",
        data=b'{"review": true}',
    )

    run = projection.lookup_run(run_id)

    assert run.value is not None
    assert [reference.artifact_id for reference in run.value.artifact_refs] == ["plan-review"]


def test_a_guessed_internal_identifier_does_not_resolve_through_the_public_path(
    projection: ProductProjection,
) -> None:
    """The identifier is fixed and therefore guessable, so secrecy cannot be the control."""
    run_id = _created(projection)
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_CHECKPOINT_ARTIFACT_ID,
        kind="run-bundle",
        media_type="application/zip",
        data=write_bundle(_BUNDLE_MEMBERS),
        visibility="internal",
    )

    result = projection.lookup_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID)

    assert result.value is None
    assert result.reason == projection.lookup_artifact(run_id, "never-published").reason


def test_the_product_resolves_its_own_internal_artifact(projection: ProductProjection) -> None:
    """Refusing the public path is only correct if the private one still works."""
    run_id = _created(projection)
    data = write_bundle(_BUNDLE_MEMBERS)
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_CHECKPOINT_ARTIFACT_ID,
        kind="run-bundle",
        media_type="application/zip",
        data=data,
        visibility="internal",
    )

    result = projection.lookup_internal_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID)

    assert result.value == data


def test_the_internal_path_does_not_resolve_a_public_artifact(projection: ProductProjection) -> None:
    run_id = _created(projection)
    projection.publish_artifact(
        run_id,
        artifact_id="plan-review",
        kind="saved-plan-review",
        media_type="application/json",
        data=b'{"review": true}',
    )

    result = projection.lookup_internal_artifact(run_id, "plan-review")

    assert result.value is None


def test_visibility_is_part_of_publication_identity(projection: ProductProjection) -> None:
    """Reclassifying published bytes is a content conflict, not a repeat publication.

    The refusal has to be the differing-content one: refusing as "already published"
    would pass just as well while visibility was ignored entirely.
    """
    run_id = _created(projection)
    data = write_bundle(_BUNDLE_MEMBERS)
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_CHECKPOINT_ARTIFACT_ID,
        kind="run-bundle",
        media_type="application/zip",
        data=data,
        visibility="internal",
    )

    with pytest.raises(DuplicateArtifactError, match="different content or metadata"):
        projection.publish_artifact(
            run_id,
            artifact_id=PLAN_CHECKPOINT_ARTIFACT_ID,
            kind="run-bundle",
            media_type="application/zip",
            data=data,
        )


# ---------------------------------------------------------------------------
# Fixed checkpoint identifiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "artifact_id",
    [PLAN_CHECKPOINT_ARTIFACT_ID, FINAL_CHECKPOINT_ARTIFACT_ID],
)
def test_an_equal_fixed_checkpoint_retry_is_idempotent(projection: ProductProjection, artifact_id: str) -> None:
    run_id = _created(projection)
    data = write_bundle(_BUNDLE_MEMBERS)
    first = projection.publish_artifact(
        run_id,
        artifact_id=artifact_id,
        kind="run-bundle",
        media_type="application/zip",
        data=data,
        visibility="internal",
    )

    with pytest.raises(DuplicateArtifactError, match="is already published"):
        projection.publish_artifact(
            run_id,
            artifact_id=artifact_id,
            kind="run-bundle",
            media_type="application/zip",
            data=data,
            visibility="internal",
        )

    stored = projection.lookup_internal_reference(run_id, artifact_id)
    assert projection.lookup_internal_artifact(run_id, artifact_id).value == data
    assert stored.value is not None
    assert stored.value.digest == first.digest


@pytest.mark.parametrize(
    "artifact_id",
    [PLAN_CHECKPOINT_ARTIFACT_ID, FINAL_CHECKPOINT_ARTIFACT_ID],
)
def test_a_different_digest_at_the_same_checkpoint_conflicts(projection: ProductProjection, artifact_id: str) -> None:
    run_id = _created(projection)
    projection.publish_artifact(
        run_id,
        artifact_id=artifact_id,
        kind="run-bundle",
        media_type="application/zip",
        data=write_bundle(_BUNDLE_MEMBERS),
        visibility="internal",
    )

    with pytest.raises(DuplicateArtifactError, match="different content or metadata"):
        projection.publish_artifact(
            run_id,
            artifact_id=artifact_id,
            kind="run-bundle",
            media_type="application/zip",
            data=write_bundle({**_BUNDLE_MEMBERS, "A/devices.parquet": b"PAR1differentPAR1"}),
            visibility="internal",
        )


def test_the_two_checkpoint_identifiers_are_distinct() -> None:
    assert PLAN_CHECKPOINT_ARTIFACT_ID != FINAL_CHECKPOINT_ARTIFACT_ID


# ---------------------------------------------------------------------------
# Bounded reads
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Digest and stored size, before any structure is trusted
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Provider parity
# ---------------------------------------------------------------------------


def test_both_providers_store_the_same_bundle_under_the_same_digest(tmp_path: Path) -> None:
    """A bundle written by one deployment shape has to be the same object in the other."""
    data = write_bundle(_BUNDLE_MEMBERS)
    filesystem = ProductProjection(
        SQLiteRunStore(tmp_path / "file.sqlite3"),
        FileArtifactStore(tmp_path / "objects"),
    )
    s3 = ProductProjection(
        SQLiteRunStore(tmp_path / "s3.sqlite3"),
        S3ArtifactStore(_FakeS3(), bucket="product-artifacts"),
    )
    references = []
    for candidate in (filesystem, s3):
        run_id = _created(candidate)
        references.append(
            candidate.publish_artifact(
                run_id,
                artifact_id=PLAN_CHECKPOINT_ARTIFACT_ID,
                kind="run-bundle",
                media_type="application/zip",
                data=data,
                visibility="internal",
            )
        )

    assert references[0].digest == references[1].digest
    assert references[0].size == references[1].size
    assert (
        filesystem.lookup_internal_artifact("run-bundle-001", PLAN_CHECKPOINT_ARTIFACT_ID).value
        == s3.lookup_internal_artifact("run-bundle-001", PLAN_CHECKPOINT_ARTIFACT_ID).value
    )


# ---------------------------------------------------------------------------
# The redaction split
# ---------------------------------------------------------------------------


def test_a_public_artifact_containing_a_secret_is_still_redacted(projection: ProductProjection) -> None:
    run_id = _created(projection)
    projection.publish_artifact(
        run_id,
        artifact_id="plan-review",
        kind="saved-plan-review",
        media_type="application/json",
        data=f'{{"endpoint": "https://host/{_SECRET}"}}'.encode(),
        secrets=[_SECRET],
    )

    stored = projection.lookup_artifact(run_id, "plan-review")

    assert stored.value is not None
    assert _SECRET.encode() not in stored.value


def test_an_internal_bundle_containing_the_same_secret_is_stored_byte_identical(
    projection: ProductProjection,
) -> None:
    """Redacting inside a binary container corrupts the archive instead of protecting it."""
    run_id = _created(projection)
    data = write_bundle({**_BUNDLE_MEMBERS, "plan/operations.jsonl": _SECRET.encode()})

    reference = projection.publish_artifact(
        run_id,
        artifact_id=PLAN_CHECKPOINT_ARTIFACT_ID,
        kind="run-bundle",
        media_type="application/zip",
        data=data,
        visibility="internal",
        secrets=[_SECRET],
    )

    stored = projection.lookup_internal_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID)
    assert stored.value == data
    assert reference.size == len(data)


def test_redaction_of_a_bundle_would_have_changed_its_digest() -> None:
    """The control measurement: without the split, determinism is data-dependent."""
    data = write_bundle({**_BUNDLE_MEMBERS, "plan/operations.jsonl": _SECRET.encode()})

    assert data.replace(_SECRET.encode(), b"[REDACTED]") != data


def test_an_internal_reference_is_not_reachable_through_the_public_reference_lookup(
    projection: ProductProjection,
) -> None:
    run_id = _created(projection)
    projection.publish_artifact(
        run_id,
        artifact_id=FINAL_CHECKPOINT_ARTIFACT_ID,
        kind="run-bundle",
        media_type="application/zip",
        data=write_bundle(_BUNDLE_MEMBERS),
        visibility="internal",
    )

    reference = projection.lookup_internal_reference(run_id, FINAL_CHECKPOINT_ARTIFACT_ID)

    assert isinstance(reference.value, ArtifactReference)
    assert reference.value.visibility == "internal"
